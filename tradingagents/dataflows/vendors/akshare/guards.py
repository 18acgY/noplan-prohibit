"""Preconditions shared by the akshare vendor functions.

Two concerns recur across the price, news and statement endpoints, so they live
here rather than being copied or imported from a sibling's private names.
"""

from __future__ import annotations

import os

from tradingagents.dataflows.errors import NoMarketDataError

from .symbols import bare_code, is_a_share

# Domains akshare scrapes. Keeping them out of the outbound proxy lets the
# domestic leg and the Yahoo leg run in the same process — the analyst threads
# run concurrently, so clearing the proxy variables for a call would race.
_DOMESTIC_NO_PROXY = (
    "eastmoney.com",
    "sina.com.cn",
    "sinaimg.cn",
    "sinajs.cn",
    "163.com",
    "qq.com",
    "sse.com.cn",
    "szse.cn",
    "bse.cn",
    "csindex.com.cn",
)


def ensure_domestic_no_proxy() -> None:
    """Add the scraped domains to ``NO_PROXY`` without touching the proxy vars.

    Only ever appends: an outbound proxy set for Yahoo Finance keeps working,
    while the Chinese sites go direct.
    """
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    entries = [entry.strip() for entry in current.split(",") if entry.strip()]
    missing = [domain for domain in _DOMESTIC_NO_PROXY if domain not in entries]
    if not missing:
        return
    merged = ",".join(entries + missing)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


def require_a_share(symbol: str) -> str:
    """Return the bare code, or decline before any request is made.

    Raising ``NoMarketDataError`` (rather than returning an empty string) is what
    lets the router fall through to the next vendor for a US or HK symbol.
    """
    if not is_a_share(symbol):
        raise NoMarketDataError(
            symbol,
            bare_code(symbol),
            "akshare here serves mainland A-shares only",
        )
    return bare_code(symbol)