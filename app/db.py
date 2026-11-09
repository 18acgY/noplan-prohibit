"""SQLite 持久化层。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        conn.commit()
    finally:
        conn.close()


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