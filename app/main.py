"""次日交易许可单 —— 最小可用 Web 应用。"""

from __future__ import annotations

import hmac
import os
import secrets
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db, review

BASE_DIR = Path(__file__).resolve().parent

STATUS_META = {
    review.PASS: {"label": "预审通过", "cls": "ok"},
    review.NO_HEAVY: {"label": "禁止重仓", "cls": "warn"},
    review.PARAM_FAIL: {"label": "参数未达门槛", "cls": "bad"},
    review.DISCIPLINE_RED: {"label": "纪律红灯", "cls": "bad"},
    review.INCOMPLETE: {"label": "资料不完整", "cls": "bad"},
    review.DATE_INVALID: {"label": "日期不合规", "cls": "bad"},
}


class AuthRequired(Exception):
    """未登录时跳转到登录页。"""


def _money(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _pct(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _parse_form(form: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for field in db.TEXT_FIELDS:
        data[field] = str(form.get(field, "") or "").strip()
    for field in db.NUMERIC_FIELDS:
        raw = form.get(field)
        try:
            data[field] = float(str(raw).strip()) if raw not in (None, "") else None
        except ValueError:
            data[field] = None
    return data


def create_app() -> FastAPI:
    password = os.environ.get("NOPLAN_PASSWORD")
    if not password:
        raise RuntimeError(
            "未设置 NOPLAN_PASSWORD。请先设置登录密码，例如："
            "export NOPLAN_PASSWORD='你的密码'"
        )
    # 没配置持久密钥时每次启动随机生成：会话在重启后失效，但绝不会用固定默认值。
    secret = os.environ.get("NOPLAN_SECRET") or secrets.token_hex(32)
    db_path = os.environ.get("NOPLAN_DB") or str(BASE_DIR.parent / "data" / "noplan.db")

    db.init_db(db_path)

    app = FastAPI(title="次日交易许可单")
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="noplan_session",
        max_age=60 * 60 * 12,
        same_site="lax",
    )
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    templates.env.filters["money"] = _money
    templates.env.filters["pct"] = _pct

    @app.exception_handler(AuthRequired)
    async def _auth_redirect(request: Request, exc: AuthRequired) -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    def require(request: Request) -> None:
        if not request.session.get("auth"):
            raise AuthRequired()

    def thresholds(conn) -> review.Thresholds:
        settings = db.get_settings(conn)
        return review.Thresholds(
            upside_min=settings["upside_min"],
            reward_risk_min=settings["reward_risk_min"],
            score_min=settings["score_min"],
        )

    def render(request: Request, name: str, context: dict[str, Any], status_code: int = 200):
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/")
    def index(request: Request):
        require(request)
        return RedirectResponse("/permits", status_code=303)

    @app.get("/login")
    def login_page(request: Request):
        if request.session.get("auth"):
            return RedirectResponse("/permits", status_code=303)
        return render(request, "login.html", {"error": None})

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        supplied = str(form.get("password", "") or "")
        if hmac.compare_digest(supplied, password):
            request.session["auth"] = True
            return RedirectResponse("/permits", status_code=303)
        return render(request, "login.html", {"error": "密码不正确"}, status_code=401)

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/permits")
    def permits_list(request: Request):
        require(request)
        with closing(db.connect(db_path)) as conn:
            rows = db.list_permits(conn)
            t = thresholds(conn)
        items = [{**row, "review": review.review(row, t)} for row in rows]
        return render(request, "list.html", {"items": items, "meta": STATUS_META})

    @app.get("/permits/new")
    def permit_new(request: Request):
        require(request)
        permit = {
            "fill_date": date.today().isoformat(),
            "plan_date": (date.today() + timedelta(days=1)).isoformat(),
            "action": "开仓",
            **{f"emo_{i}": "否" for i in range(1, 9)},
        }
        return render(
            request,
            "form.html",
            {"permit": permit, "mode": "new", "post_url": "/permits"},
        )

    @app.post("/permits")
    async def permit_create(request: Request):
        require(request)
        data = _parse_form(await request.form())
        with closing(db.connect(db_path)) as conn:
            permit_id = db.create_permit(conn, data)
        return RedirectResponse(f"/permits/{permit_id}", status_code=303)

    @app.get("/permits/{permit_id}")
    def permit_detail(request: Request, permit_id: int):
        require(request)
        with closing(db.connect(db_path)) as conn:
            permit = db.get_permit(conn, permit_id)
            t = thresholds(conn)
        if not permit:
            raise HTTPException(status_code=404, detail="许可单不存在")
        result = review.review(permit, t)
        return render(
            request,
            "detail.html",
            {"permit": permit, "r": result, "meta": STATUS_META, "thresholds": t},
        )

    @app.get("/permits/{permit_id}/edit")
    def permit_edit(request: Request, permit_id: int):
        require(request)
        with closing(db.connect(db_path)) as conn:
            permit = db.get_permit(conn, permit_id)
        if not permit:
            raise HTTPException(status_code=404, detail="许可单不存在")
        return render(
            request,
            "form.html",
            {"permit": permit, "mode": "edit", "post_url": f"/permits/{permit_id}/edit"},
        )

    @app.post("/permits/{permit_id}/edit")
    async def permit_update(request: Request, permit_id: int):
        require(request)
        data = _parse_form(await request.form())
        with closing(db.connect(db_path)) as conn:
            if not db.get_permit(conn, permit_id):
                raise HTTPException(status_code=404, detail="许可单不存在")
            db.update_permit(conn, permit_id, data)
        return RedirectResponse(f"/permits/{permit_id}", status_code=303)

    @app.post("/permits/{permit_id}/delete")
    def permit_delete(request: Request, permit_id: int):
        require(request)
        with closing(db.connect(db_path)) as conn:
            db.delete_permit(conn, permit_id)
        return RedirectResponse("/permits", status_code=303)

    @app.get("/settings")
    def settings_page(request: Request):
        require(request)
        with closing(db.connect(db_path)) as conn:
            settings = db.get_settings(conn)
        return render(request, "settings.html", {"settings": settings})

    @app.post("/settings")
    async def settings_save(request: Request):
        require(request)
        form = await request.form()

        def as_float(key: str, fallback: float) -> float:
            try:
                return float(str(form.get(key, "")).strip())
            except (TypeError, ValueError):
                return fallback

        with closing(db.connect(db_path)) as conn:
            current = db.get_settings(conn)
            db.save_settings(
                conn,
                {
                    "upside_min": as_float("upside_min", current["upside_min"] * 100) / 100,
                    "reward_risk_min": as_float("reward_risk_min", current["reward_risk_min"]),
                    "score_min": as_float("score_min", current["score_min"]),
                },
            )
        return RedirectResponse("/settings", status_code=303)

    @app.get("/discipline")
    def discipline_page(request: Request):
        require(request)
        return render(request, "discipline.html", {})

    return app