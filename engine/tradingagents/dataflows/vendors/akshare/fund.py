"""ETF fund-level data through akshare.

Funds are not companies, so the equivalent of fundamentals is the fund layer:
NAV history, holdings and the premium/discount between market price and NAV.
These endpoints live under fund.eastmoney.com rather than the push2 quote host,
so they keep serving while the quote API is rate-limited — which is exactly the
window in which an ETF run previously had no usable data at all.

The formatting functions take frames so they can be unit-tested without network
access; the ``fetch_*`` helpers are the only network-facing code.
"""

from __future__ import annotations

import pandas as pd

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError

_NAV_COLUMNS = {
    "净值日期": "Date",
    "单位净值": "Nav",
    "累计净值": "AccNav",
    "日增长率": "DailyPct",
    "申购状态": "Purchase",
    "赎回状态": "Redeem",
}

_HOLDING_COLUMNS = {
    "股票代码": "Code",
    "股票名称": "Name",
    "占净值比例": "Weight",
    "持股数": "Shares",
    "持仓市值": "Value",
    "季度": "Quarter",
}


def _require(frame: pd.DataFrame, columns: dict, symbol: str, source: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise NoMarketDataError(symbol, symbol, f"{source} returned no rows")
    missing = [name for name in columns if name not in frame.columns]
    if missing:
        raise VendorUnavailableError(
            f"{source} response for {symbol} is missing column(s) {missing}; "
            f"got {list(frame.columns)}"
        )
    return frame


# ---- NAV ------------------------------------------------------------------


def fetch_nav(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    from tradingagents.dataflows.vendors.akshare._compat import resolve

    # akshare 1.20.41 renamed this interface and deleted the old name.
    return resolve("fund_exchange_nav_hist_em", "fund_etf_fund_info_em")(
        fund=code,
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
    )


def parse_nav(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    frame = _require(raw, _NAV_COLUMNS, symbol, "akshare fund NAV")
    out = frame.rename(columns=_NAV_COLUMNS)[list(_NAV_COLUMNS.values())].copy()
    out["Date"] = pd.to_datetime(out["Date"])
    out = out.set_index("Date").sort_index()
    for column in ("Nav", "AccNav", "DailyPct"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def format_nav(parsed: pd.DataFrame, symbol: str) -> str:
    latest = parsed.index[-1]
    last = parsed.iloc[-1]
    change = None
    if len(parsed) > 20:
        base = parsed["Nav"].iloc[-21]
        if base:
            change = (last["Nav"] / base - 1) * 100
    lines = [
        f"# ETF fund NAV for {symbol}",
        f"# latest NAV date: {latest:%Y-%m-%d}, unit NAV: {last['Nav']:.4f}",
    ]
    if change is not None:
        lines.append(f"# 20-session NAV change: {change:+.2f}%")
    lines.append("# date, unit_nav, cumulative_nav, daily_pct, purchase, redeem")
    for day, row in parsed.tail(12).iterrows():
        lines.append(
            f"{day:%Y-%m-%d}, {row['Nav']:.4f}, {row['AccNav']:.4f}, "
            f"{row['DailyPct']:+.2f}%, {row['Purchase']}, {row['Redeem']}"
        )
    return "\n".join(lines)


# ---- Holdings -------------------------------------------------------------


def fetch_holdings(code: str, year: str) -> pd.DataFrame:
    import akshare as ak

    return ak.fund_portfolio_hold_em(symbol=code, date=year)


def parse_holdings(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    frame = _require(raw, _HOLDING_COLUMNS, symbol, "akshare fund holdings")
    out = frame.rename(columns=_HOLDING_COLUMNS)
    out["Weight"] = pd.to_numeric(out["Weight"], errors="coerce")
    # The endpoint returns several quarters; the newest one is the only one that
    # describes the fund the run is analysing.
    out["QuarterOrder"] = out["Quarter"].astype(str).rank(method="dense")
    return out.sort_values(["QuarterOrder", "Weight"], ascending=[False, False])


def format_holdings(parsed: pd.DataFrame, symbol: str) -> str:
    latest_quarter = parsed["Quarter"].iloc[0]
    current = parsed[parsed["Quarter"] == latest_quarter]
    top = current.head(10)
    top3 = current.head(3)["Weight"].sum()
    top10 = current.head(10)["Weight"].sum()
    lines = [
        f"# ETF holdings for {symbol}",
        f"# quarter: {latest_quarter}",
        f"# concentration: top3 {top3:.1f}% / top10 {top10:.1f}% of NAV",
        "# rank, code, name, weight_pct, shares(10k), value(10k CNY)",
    ]
    for rank, (_, row) in enumerate(top.iterrows(), start=1):
        lines.append(
            f"{rank}, {row['Code']}, {row['Name']}, {row['Weight']:.2f}%, "
            f"{row['Shares']}, {row['Value']}"
        )
    return "\n".join(lines)


# ---- Premium / discount ---------------------------------------------------


def build_premium_report(
    prices: pd.DataFrame, nav: pd.DataFrame, symbol: str, look_back_days: int = 30
) -> str:
    """Compare market close with published unit NAV, both aligned by date.

    A NAV dated after the last price is not used; a NAV that lags the price is
    reported explicitly, because a stale NAV makes the premium look tight.
    """
    if prices is None or prices.empty:
        raise NoMarketDataError(symbol, symbol, "no price rows for premium")
    if nav is None or nav.empty:
        raise NoMarketDataError(symbol, symbol, "no NAV rows for premium")

    close = (prices["Close"] if "Close" in prices.columns else prices.iloc[:, 0]).copy()
    close.index = pd.to_datetime(close.index).normalize()
    frame = pd.DataFrame({"Close": close}).join(nav[["Nav"]], how="inner")
    frame = frame.dropna()
    if frame.empty:
        raise NoMarketDataError(symbol, symbol, "price and NAV share no trading date")

    frame["PremiumPct"] = (frame["Close"] / frame["Nav"] - 1) * 100
    window = frame.tail(max(1, look_back_days))
    latest = window.iloc[-1]
    stale = pd.Timestamp(nav.index[-1]).normalize() < pd.Timestamp(close.index[-1]).normalize()
    lines = [
        f"# ETF premium/discount for {symbol} (close vs unit NAV)",
        f"# latest: {window.index[-1]:%Y-%m-%d} close {latest['Close']:.4f}, "
        f"unit NAV {latest['Nav']:.4f}, premium {latest['PremiumPct']:+.2f}%",
        f"# {look_back_days}-session premium range: "
        f"{window['PremiumPct'].min():+.2f}% .. {window['PremiumPct'].max():+.2f}%, "
        f"mean {window['PremiumPct'].mean():+.2f}%",
    ]
    if stale:
        lines.append(
            "# WARNING: the latest published NAV predates the latest close; "
            f"NAV date {pd.Timestamp(nav.index[-1]):%Y-%m-%d}. Premium may be overstated."
        )
    lines.append("# date, close, unit_nav, premium_pct")
    for day, row in window.tail(12).iterrows():
        lines.append(
            f"{day:%Y-%m-%d}, {row['Close']:.4f}, {row['Nav']:.4f}, {row['PremiumPct']:+.2f}%"
        )
    return "\n".join(lines)