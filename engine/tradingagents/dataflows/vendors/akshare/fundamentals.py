"""A-share company profile and financial statements through akshare.

Yahoo Finance serves A-share statements at half-year and annual granularity and
withholds them for past-dated runs, which is thin for a company whose quarter-on-
quarter operating trend is the whole question. Eastmoney carries the report-level
statements, so this module serves those instead.

Statement endpoints take a market-prefixed symbol (``SH600009``) while the profile
endpoint takes the bare code; both translations come from ``symbols``.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError

from .guards import ensure_domestic_no_proxy, require_a_share
from .symbols import prefixed_code

logger = logging.getLogger(__name__)

# Statements arrive as one row per report with a hundred-odd columns. The agents
# read this as text, so only the most recent reports are worth the tokens.
_MAX_REPORT_ROWS = 8

_STATEMENT_FETCHERS = {
    "Balance Sheet": "stock_balance_sheet_by_report_em",
    "Cash Flow": "stock_cash_flow_sheet_by_report_em",
    "Income Statement": "stock_profit_sheet_by_report_em",
}


def format_profile(ticker: str, profile: pd.DataFrame) -> str:
    """Render the key/value profile frame as text."""
    if profile is None or profile.empty:
        raise NoMarketDataError(ticker, ticker, "akshare returned no company profile")

    for column in ("item", "value"):
        if column not in profile.columns:
            raise VendorUnavailableError(
                f"akshare profile for {ticker} is missing {column!r}; "
                f"got {list(profile.columns)}"
            )

    lines = [f"# Company profile for {ticker.upper()}\n"]
    for _, row in profile.iterrows():
        lines.append(f"- {row['item']}: {row['value']}")
    return "\n".join(lines) + "\n"


def get_fundamentals(ticker: str, as_of_date: str = None) -> str:
    """Router entry point for ``get_fundamentals``."""
    code = require_a_share(ticker)
    ensure_domestic_no_proxy()

    import akshare as ak

    try:
        profile = ak.stock_individual_info_em(symbol=code)
    except Exception as exc:  # noqa: BLE001 - a scrape failure is a vendor outage
        raise VendorUnavailableError(
            f"akshare profile request for {code} failed: {exc}"
        ) from exc

    text = format_profile(ticker, profile)

    # The financial abstract is a convenience table, not the decision input; a
    # failure there must not sink a profile we already hold.
    try:
        abstract = ak.stock_financial_abstract(symbol=code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("akshare financial abstract unavailable for %s: %s", code, exc)
        return text

    if abstract is not None and not abstract.empty:
        text += "\n# Financial summary\n\n" + abstract.head(_MAX_REPORT_ROWS).to_csv(index=False)

    return text


def _statement(ticker: str, freq: str, as_of_date: str, title: str) -> str:
    """Fetch and render one report-level statement."""
    prefixed = prefixed_code(ticker)
    require_a_share(ticker)
    ensure_domestic_no_proxy()

    import akshare as ak

    fetcher = getattr(ak, _STATEMENT_FETCHERS[title])

    try:
        raw = fetcher(symbol=prefixed)
    except Exception as exc:  # noqa: BLE001
        raise VendorUnavailableError(
            f"akshare {title} request for {prefixed} failed: {exc}"
        ) from exc

    if raw is None or raw.empty:
        raise NoMarketDataError(ticker, prefixed, f"no {title} rows")

    frame = raw
    # Ask for annual data and get annual rows: Eastmoney returns every report, so
    # a year-end filter is what makes ``freq`` mean anything here.
    if str(freq).lower().startswith("annual") and "REPORT_DATE" in frame.columns:
        stamps = frame["REPORT_DATE"].astype(str)
        year_end = frame[stamps.str.endswith("12-31")]
        if not year_end.empty:
            frame = year_end

    header = f"# {title} for {ticker.upper()} ({freq})\n\n"
    return header + frame.head(_MAX_REPORT_ROWS).to_csv(index=False)


def get_balance_sheet(ticker: str, freq: str = "quarterly", as_of_date: str = None) -> str:
    return _statement(ticker, freq, as_of_date, "Balance Sheet")


def get_cashflow(ticker: str, freq: str = "quarterly", as_of_date: str = None) -> str:
    return _statement(ticker, freq, as_of_date, "Cash Flow")


def get_income_statement(ticker: str, freq: str = "quarterly", as_of_date: str = None) -> str:
    return _statement(ticker, freq, as_of_date, "Income Statement")