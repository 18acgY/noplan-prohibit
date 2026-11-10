"""分析相关的纯逻辑：代码 → 交易所后缀、本地日期。"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

# 中国不实行夏令时，固定 +08:00，避免依赖系统的 tzdata。
TZ = timezone(timedelta(hours=8))

_CODE_RE = re.compile(r"^(\d{6})(?:\.(SS|SZ|BJ))?$")

# 6 位代码前缀 → 交易所
_PREFIX_EXCHANGE = {
    "6": "SS",
    "9": "SS",
    "5": "SS",
    "0": "SZ",
    "1": "SZ",
    "2": "SZ",
    "3": "SZ",
    "4": "BJ",
    "8": "BJ",
}


def ticker_for(raw: str | None) -> str | None:
    """把用户填的证券代码变成 TradingAgents 认的 ticker。

    纯 6 位数字按前缀自动加后缀；已经带 .SS/.SZ/.BJ 的原样规范化。
    无法识别时返回 None（不要猜，交给界面提示）。
    """
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if not text:
        return None
    match = _CODE_RE.match(text)
    if not match:
        return None
    code, suffix = match.group(1), match.group(2)
    if suffix:
        return f"{code}.{suffix}"
    exchange = _PREFIX_EXCHANGE.get(code[0])
    return f"{code}.{exchange}" if exchange else None


def today_local() -> date:
    return datetime.now(TZ).date()


def now_local_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")