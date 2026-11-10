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
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import analysis as analysis_mod
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

    def worker_token() -> str:
        return os.environ.get("NOPLAN_WORKER_TOKEN", "")

    def daily_limit() -> int:
        raw = os.environ.get("NOPLAN_DAILY_ANALYSIS_LIMIT", "10")
        try:
            return max(1, int(raw))
        except ValueError:
            return 10

    def require_worker(request: Request) -> None:
        token = worker_token()
        if not token:
            raise HTTPException(status_code=503, detail="未配置 NOPLAN_WORKER_TOKEN")
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied, f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="worker token 无效")

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
            latest = db.latest_analysis_for_permit(conn, permit_id)
            online = db.worker_online(conn)
            used = db.daily_analysis_count(conn)
        if not permit:
            raise HTTPException(status_code=404, detail="许可单不存在")
        result = review.review(permit, t)
        ticker = analysis_mod.ticker_for(permit.get("code"))
        limit = daily_limit()
        return render(
            request,
            "detail.html",
            {
                "permit": permit,
                "r": result,
                "meta": STATUS_META,
                "thresholds": t,
                "analysis": latest,
                "ticker": ticker,
                "analysis_stale": bool(latest and ticker and latest["ticker"] != ticker),
                "worker_online": online,
                "used": used,
                "limit": limit,
                "can_analyze": bool(ticker) and online and used < limit,
            },
        )

    @app.post("/permits/{permit_id}/analysis")
    def permit_analysis(request: Request, permit_id: int):
        require(request)
        with closing(db.connect(db_path)) as conn:
            permit = db.get_permit(conn, permit_id)
            if not permit:
                raise HTTPException(status_code=404, detail="许可单不存在")
            ticker = analysis_mod.ticker_for(permit.get("code"))
            if not ticker:
                raise HTTPException(status_code=400, detail="证券代码无法识别，请填 6 位 A 股代码")
            if not db.worker_online(conn):
                raise HTTPException(status_code=409, detail="分析服务离线，请稍后再试")
            try:
                db.enqueue_analysis(
                    conn,
                    permit_id,
                    ticker,
                    analysis_mod.today_local().isoformat(),
                    daily_limit(),
                )
            except db.DailyLimitError as exc:
                raise HTTPException(status_code=429, detail=str(exc)) from exc
        return RedirectResponse(f"/permits/{permit_id}#analysis", status_code=303)

    @app.get("/analyses/{analysis_id}")
    def analysis_report(request: Request, analysis_id: int):
        require(request)
        with closing(db.connect(db_path)) as conn:
            row = db.get_analysis(conn, analysis_id)
        if not row:
            raise HTTPException(status_code=404, detail="分析不存在")
        return render(request, "analysis.html", {"a": row})

    @app.post("/internal/worker/heartbeat")
    async def internal_heartbeat(request: Request):
        require_worker(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        version = str(body.get("version", "")) if isinstance(body, dict) else ""
        with closing(db.connect(db_path)) as conn:
            db.worker_heartbeat(conn, version)
        return {"ok": True}

    @app.post("/internal/analysis/claim")
    def internal_claim(request: Request):
        require_worker(request)
        with closing(db.connect(db_path)) as conn:
            job = db.claim_next_analysis(conn)
        if not job:
            return Response(status_code=204)
        return JSONResponse(job)

    @app.post("/internal/analysis/{analysis_id}/finish")
    async def internal_finish(request: Request, analysis_id: int):
        require_worker(request)
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="请求体必须是 JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
        duration = body.get("duration_s")
        try:
            duration_value = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_value = None
        with closing(db.connect(db_path)) as conn:
            if not db.get_analysis(conn, analysis_id):
                raise HTTPException(status_code=404, detail="分析不存在")
            try:
                db.finish_analysis(
                    conn,
                    analysis_id,
                    str(body.get("status", "")),
                    rating=body.get("rating"),
                    summary=body.get("summary"),
                    target_price=body.get("target_price"),
                    horizon=body.get("horizon"),
                    full_report=body.get("full_report"),
                    error=body.get("error"),
                    run_dir=body.get("run_dir"),
                    duration_s=duration_value,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

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