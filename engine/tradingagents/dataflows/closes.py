"""Close-price series for settling past decisions.

Settlement needs numbers, not the analyst-facing text tools, so it cannot go
through ``route_to_vendor``: the router turns any vendor failure into a
DATA_UNAVAILABLE string, which would then be mistaken for a price series.

A-share stocks and ETFs come from the akshare vendor (Yahoo is unreachable
here); everything else keeps using Yahoo.
"""

from __future__ import annotations

import pandas as pd

from tradingagents.dataflows.vendors.akshare import market as akshare_market
from tradingagents.dataflows.vendors.akshare.symbols import is_a_share
from tradingagents.dataflows.vendors.yahoo.market import get_closes as yahoo_get_closes

# A-share index codes collide with unrelated stock codes: 000001 is the Shanghai
# Composite as an index and Ping An Bank as a Shenzhen stock. The akshare stock
# endpoint would quietly return the bank, so indices are refused here and the
# A-share benchmark is configured as an ETF instead (benchmark_map).
_INDEX_PREFIX_BY_SUFFIX = {"SS": "000", "SZ": "399"}


def _is_a_share_index(symbol: str) -> bool:
    code, _, suffix = symbol.strip().upper().partition(".")
    prefix = _INDEX_PREFIX_BY_SUFFIX.get(suffix)
    return bool(prefix) and code.startswith(prefix) and len(code) == 6


def get_closes(symbol: str, start_date: str, end_date: str) -> pd.Series:
    """Daily closes for ``symbol`` between the two dates, oldest first."""
    if not is_a_share(symbol):
        return yahoo_get_closes(symbol, start_date, end_date)
    if _is_a_share_index(symbol):
        raise ValueError(
            f"{symbol} is an A-share index; akshare's stock endpoint would return "
            "the same-coded stock instead. Configure an ETF benchmark (e.g. "
            "510300.SS) in benchmark_map rather than an index code."
        )

    frame = akshare_market.fetch_history(symbol, start_date, end_date)
    series = frame["Close"].astype(float)
    series.index = pd.to_datetime(series.index)
    return series[series > 0].sort_index()