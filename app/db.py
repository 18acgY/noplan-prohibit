"""SQLite 持久化层。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import progress

TEXT_FIELDS = [
    "fill_date",
    "plan_date",
    "action",
    "code",
    "name",
    "market",
    "core_logic",
    "evidence",
    "counter_argument",
    "trigger_cond",
    "cancel",
    "exit_plan",
    "emo_1",
    "emo_2",
    "emo_3",
    "emo_4",
    "emo_5",
    "emo_6",
    "emo_7",
    "emo_8",
    "admit_signal",
]

NUMERIC_FIELDS = [
    "score_market",
    "score_sector",
    "score_stock",
    "score_corr",
    "buy_price",
    "stop_price",
    "target_price",
    "qty",
    "account_total",
    "risk_cap",
]

ALL_FIELDS = TEXT_FIELDS + NUMERIC_FIELDS

SETTING_DEFAULTS = {
    "upside_min": 0.15,
    "reward_risk_min": 2.0,
    "score_min": 60.0,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS permits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fill_date TEXT, plan_date TEXT, action TEXT, code TEXT, name TEXT, market TEXT,
  core_logic TEXT, evidence TEXT, counter_argument TEXT,
  score_market REAL, score_sector REAL, score_stock REAL, score_corr REAL,
  buy_price REAL, stop_price REAL, target_price REAL, qty REAL,
  account_total REAL, risk_cap REAL,
  trigger_cond TEXT, cancel TEXT, exit_plan TEXT,
  emo_1 TEXT, emo_2 TEXT, emo_3 TEXT, emo_4 TEXT,
  emo_5 TEXT, emo_6 TEXT, emo_7 TEXT, emo_8 TEXT,
  admit_signal TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  permit_id INTEGER NOT NULL,
  ticker TEXT NOT NULL,
  analysis_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  rating TEXT,
  summary TEXT,
  target_price TEXT,
  horizon TEXT,
  full_report TEXT,
  error TEXT,
  run_dir TEXT,
  duration_s REAL,
  stage TEXT,
  stage_detail TEXT,
  stage_started_at TEXT,
  progress_updated_at TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  started_at TEXT,
  worker_claimed_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_analyses_status ON analyses(status);
CREATE INDEX IF NOT EXISTS idx_analyses_ticker_date ON analyses(ticker, analysis_date);
CREATE TABLE IF NOT EXISTS worker_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_heartbeat TEXT,
  version TEXT
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """给旧库补新列（CREATE TABLE IF NOT EXISTS 不会加列）。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(analyses)")}
    for column in ("stage", "stage_detail", "stage_started_at", "progress_updated_at"):
        if column not in existing:
            conn.execute(f"ALTER TABLE analyses ADD COLUMN {column} TEXT")
    if "cancel_requested" not in existing:
        conn.execute(
            "ALTER TABLE analyses ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_settings(conn: sqlite3.Connection) -> dict[str, float]:
    settings = dict(SETTING_DEFAULTS)
    for row in conn.execute("SELECT key, value FROM settings"):
        try:
            settings[row["key"]] = float(row["value"])
        except (TypeError, ValueError):
            continue
    return settings


def save_settings(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    for key in SETTING_DEFAULTS:
        if key in values and values[key] is not None:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(values[key])),
            )
    conn.commit()


def create_permit(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    now = _now()
    columns = ", ".join(ALL_FIELDS)
    marks = ", ".join("?" for _ in ALL_FIELDS)
    values = [data.get(field) for field in ALL_FIELDS]
    cursor = conn.execute(
        f"INSERT INTO permits ({columns}, created_at, updated_at) VALUES ({marks}, ?, ?)",
        [*values, now, now],
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_permit(conn: sqlite3.Connection, permit_id: int, data: dict[str, Any]) -> None:
    assignments = ", ".join(f"{field} = ?" for field in ALL_FIELDS)
    values = [data.get(field) for field in ALL_FIELDS]
    conn.execute(
        f"UPDATE permits SET {assignments}, updated_at = ? WHERE id = ?",
        [*values, _now(), permit_id],
    )
    conn.commit()


def get_permit(conn: sqlite3.Connection, permit_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM permits WHERE id = ?", (permit_id,)).fetchone()
    return dict(row) if row else None


def list_permits(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM permits ORDER BY plan_date DESC, id DESC").fetchall()
    return [dict(row) for row in rows]


def delete_permit(conn: sqlite3.Connection, permit_id: int) -> None:
    conn.execute("DELETE FROM permits WHERE id = ?", (permit_id,))
    conn.commit()


class DailyLimitError(Exception):
    """今天的成功分析次数已用完。"""


def daily_analysis_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM analyses "
        "WHERE status = 'done' "
        "AND date(datetime(created_at, '+8 hours')) = date(datetime('now', '+8 hours'))"
    ).fetchone()
    return int(row["c"])


def enqueue_analysis(
    conn: sqlite3.Connection,
    permit_id: int,
    ticker: str,
    analysis_date: str,
    daily_limit: int | None = None,
) -> tuple[dict[str, Any], bool]:
    """返回 (任务行, 是否新建)。同 ticker+日期已有成功结果时直接复用。"""
    done = conn.execute(
        "SELECT * FROM analyses WHERE ticker = ? AND analysis_date = ? AND status = 'done' "
        "ORDER BY id DESC LIMIT 1",
        (ticker, analysis_date),
    ).fetchone()
    if done:
        return dict(done), False

    active = conn.execute(
        "SELECT * FROM analyses WHERE ticker = ? AND analysis_date = ? "
        "AND status IN ('queued', 'running') ORDER BY id LIMIT 1",
        (ticker, analysis_date),
    ).fetchone()
    if active:
        return dict(active), False

    if daily_limit is not None and daily_analysis_count(conn) >= daily_limit:
        raise DailyLimitError(f"今日分析次数已达上限（{daily_limit} 次）")

    cursor = conn.execute(
        "INSERT INTO analyses (permit_id, ticker, analysis_date, status, created_at) "
        "VALUES (?, ?, ?, 'queued', ?)",
        (permit_id, ticker, analysis_date, _now()),
    )
    conn.commit()
    row = get_analysis(conn, int(cursor.lastrowid))
    assert row is not None
    return row, True


def claim_next_analysis(conn: sqlite3.Connection) -> dict[str, Any] | None:
    now = _now()
    row = conn.execute(
        "UPDATE analyses SET status = 'running', started_at = ?, worker_claimed_at = ?, "
        "stage = 'prepare', stage_started_at = ?, progress_updated_at = ? "
        "WHERE id = (SELECT id FROM analyses WHERE status = 'queued' ORDER BY id LIMIT 1) "
        "RETURNING *",
        (now, now, now, now),
    ).fetchone()
    conn.commit()
    return dict(row) if row else None


def queue_ahead(conn: sqlite3.Connection, analysis_id: int) -> int:
    """同一队列里，排在这条任务前面的任务数。"""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM analyses WHERE status = 'queued' AND id < ?",
        (analysis_id,),
    ).fetchone()
    return int(row["c"])


def request_cancel(conn: sqlite3.Connection, analysis_id: int) -> dict[str, Any] | None:
    """排队中直接取消；运行中只打标记，等 worker 收尾。

    已完成 / 已失败的任务返回 None（调用方提示无法取消）。
    """
    row = get_analysis(conn, analysis_id)
    if not row:
        return None

    if row["status"] == "queued":
        now = _now()
        cursor = conn.execute(
            "UPDATE analyses SET status = 'cancelled', finished_at = ?, "
            "progress_updated_at = ? WHERE id = ? AND status = 'queued'",
            (now, now, analysis_id),
        )
        conn.commit()
        if cursor.rowcount:
            return get_analysis(conn, analysis_id)
        # 刚被 worker 领走：继续按运行中处理
        row = get_analysis(conn, analysis_id)

    if row and row["status"] == "running":
        cursor = conn.execute(
            "UPDATE analyses SET cancel_requested = 1, progress_updated_at = ? "
            "WHERE id = ? AND status = 'running'",
            (_now(), analysis_id),
        )
        conn.commit()
        if cursor.rowcount:
            return get_analysis(conn, analysis_id)

    return None


def update_progress(
    conn: sqlite3.Connection,
    analysis_id: int,
    *,
    stage: str | None = None,
    agent: str | None = None,
    state: str | None = None,
) -> dict[str, Any] | None:
    """写入阶段或某个 agent 的状态；阶段只前进不后退。"""
    row = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    if not row:
        return None
    now = _now()
    updates: dict[str, Any] = {"progress_updated_at": now}

    if stage:
        if progress.stage_index(stage) is None:
            raise ValueError(f"未知阶段：{stage}")
        new_stage = progress.advance(row["stage"], stage)
        if new_stage != row["stage"]:
            updates["stage"] = new_stage
            updates["stage_started_at"] = now

    if agent and state:
        if agent not in progress.AGENT_KEYS:
            raise ValueError(f"未知 agent：{agent}")
        if state not in progress.VALID_STATES:
            raise ValueError(f"未知状态：{state}")
        detail = progress.parse_detail(row["stage_detail"])
        detail[progress.AGENT_KEYS[agent]] = state
        updates["stage_detail"] = json.dumps(detail, ensure_ascii=False)

    assignments = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(
        f"UPDATE analyses SET {assignments} WHERE id = ?",
        [*updates.values(), analysis_id],
    )
    conn.commit()
    return get_analysis(conn, analysis_id)


def finish_analysis(
    conn: sqlite3.Connection,
    analysis_id: int,
    status: str,
    *,
    rating: str | None = None,
    summary: str | None = None,
    target_price: str | None = None,
    horizon: str | None = None,
    full_report: str | None = None,
    error: str | None = None,
    run_dir: str | None = None,
    duration_s: float | None = None,
) -> None:
    if status not in ("done", "failed", "cancelled"):
        raise ValueError("status 只能是 done / failed / cancelled")
    conn.execute(
        "UPDATE analyses SET status = ?, rating = ?, summary = ?, target_price = ?, "
        "horizon = ?, full_report = ?, error = ?, run_dir = ?, duration_s = ?, "
        "cancel_requested = 0, progress_updated_at = ?, finished_at = ? WHERE id = ?",
        (
            status,
            rating,
            summary,
            target_price,
            horizon,
            full_report,
            error,
            run_dir,
            duration_s,
            _now(),
            _now(),
            analysis_id,
        ),
    )
    conn.commit()


def get_analysis(conn: sqlite3.Connection, analysis_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    return dict(row) if row else None


def latest_analysis_for_permit(conn: sqlite3.Connection, permit_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM analyses WHERE permit_id = ? ORDER BY id DESC LIMIT 1",
        (permit_id,),
    ).fetchone()
    return dict(row) if row else None


def worker_heartbeat(conn: sqlite3.Connection, version: str = "") -> None:
    conn.execute(
        "INSERT INTO worker_state (id, last_heartbeat, version) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET last_heartbeat = excluded.last_heartbeat, "
        "version = excluded.version",
        (_now(), version),
    )
    conn.commit()


def worker_online(conn: sqlite3.Connection, max_age_seconds: int = 90) -> bool:
    row = conn.execute("SELECT last_heartbeat FROM worker_state WHERE id = 1").fetchone()
    if not row or not row["last_heartbeat"]:
        return False
    try:
        beat = datetime.fromisoformat(row["last_heartbeat"])
    except ValueError:
        return False
    if beat.tzinfo is None:
        beat = beat.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - beat.astimezone(timezone.utc)).total_seconds()
    return age <= max_age_seconds