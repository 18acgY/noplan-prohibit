"""Translate between Yahoo-style tickers and the two forms akshare expects.

TradingAgents hands every vendor a Yahoo-style symbol (``600009.SS``), because
that is what the default vendor uses. akshare wants the bare six-digit code
(``600009``) for price and news calls, and a market-prefixed form (``SH600009``)
for the statement endpoints. Getting this wrong returns an empty frame, which the
agents would read as "no data" rather than "wrong symbol", so the mapping is
centralised here.

A suffix decides the market when present; a bare code infers it from the leading
digits (6xx Shanghai, 0xx/3xx Shenzhen, 4xx/8xx Beijing).
"""

from __future__ import annotations

import re

_SHANGHAI_SUFFIXES = frozenset({"SS", "SH"})
_SHENZHEN_SUFFIXES = frozenset({"SZ"})
_BEIJING_SUFFIXES = frozenset({"BJ"})
_A_SHARE_SUFFIXES = _SHANGHAI_SUFFIXES | _SHENZHEN_SUFFIXES | _BEIJING_SUFFIXES

_BARE_CODE = re.compile(r"^\d{6}$")


def _split(raw: str) -> tuple[str, str]:
    """Split ``600009.SS`` into ``("600009", "SS")``; no suffix yields ``""``."""
    symbol = (raw or "").strip().upper()
    code, _, suffix = symbol.partition(".")
    return code, suffix


def is_a_share(raw: str) -> bool:
    """Whether ``raw`` names a mainland-listed A-share.

    Foreign listings of six-digit local codes — ``600009.HK`` is a thing — must
    not qualify, so a suffix that is not an A-share market rejects outright
    instead of falling through to the bare-code check.
    """
    code, suffix = _split(raw)
    if suffix:
        return suffix in _A_SHARE_SUFFIXES and bool(_BARE_CODE.match(code))
    return bool(_BARE_CODE.match(code))


def bare_code(raw: str) -> str:
    """``600009.SS`` -> ``600009``. The form price and news endpoints take."""
    code, _ = _split(raw)
    return code


def _infer_market(code: str) -> str:
    if code.startswith("6"):
        return "SH"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def prefixed_code(raw: str) -> str:
    """``600009.SS`` -> ``SH600009``. The form statement endpoints take."""
    code, suffix = _split(raw)
    if suffix in _SHANGHAI_SUFFIXES:
        return f"SH{code}"
    if suffix in _SHENZHEN_SUFFIXES:
        return f"SZ{code}"
    if suffix in _BEIJING_SUFFIXES:
        return f"BJ{code}"
    return f"{_infer_market(code)}{code}"