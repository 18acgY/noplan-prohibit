"""A-share OHLCV history and technical indicators through akshare.

akshare scrapes rather than calling a documented API, which shapes three decisions
here:

* **Domestic traffic must bypass the outbound proxy.** The launcher exports
  ``HTTP_PROXY``/``HTTPS_PROXY`` so Yahoo Finance is reachable; routing a Chinese
  site through that same proxy is at best slower and at worst refused. ``guards``
  adds only the domestic suffixes to ``NO_PROXY``, so both paths run at once.
* **One upstream is not enough.** Eastmoney, Sina and Tencent serve the same daily
  bars from different hosts behind different IP limits. Any one of them throttles
  under load, so the price path walks a chain instead of betting on a single source.
* **A throttled host must be left alone.** Eastmoney's own message warns that
  retrying extends its penalty from five minutes to forty. A run makes dozens of
  tool calls, so an upstream that reported a rate limit goes on cooldown and is
  skipped rather than re-probed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd
from dateutil.relativedelta import relativedelta

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError

from .guards import ensure_domestic_no_proxy, require_a_share

logger = logging.getLogger(__name__)

# Eastmoney's own wording for the throttle, plus the shapes other upstreams use.
_RATE_LIMIT_MARKERS = ("限流", "RateLimit", "Too Many Requests", "429")

# Eastmoney says a first throttle clears in about five minutes.
_COOLDOWN = timedelta(minutes=5)

_COLUMN_MAP = {
    "日期": "Date",
    "开盘": "Open",
    "最高": "High",
    "最低": "Low",
    "收盘": "Close",
    "成交量": "Volume",
}

_VALUE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

# akshare needs a long tail before the first usable indicator value (a 200-day
# SMA needs 200 sessions). Two years of calendar days is comfortably enough.
_HISTORY_TAIL_DAYS = 730

# A symbol's history is downloaded once and then reused, because a nine-indicator
# pass asks for the same window nine times. Repeating that download is what kept
# tripping the upstreams' rate limits, so the full history is fetched once per
# symbol and every request slices it. Fifteen minutes matches the yfinance path.
OHLCV_CACHE_TTL_SECONDS = 900
_MAX_HISTORY_DAYS = 1825

_cooldown_until: dict[str, datetime] = {}
_memory: dict[str, tuple[datetime, pd.DataFrame]] = {}


@dataclass(frozen=True)
class Upstream:
    """One host that can serve daily bars, with its own column vocabulary."""

    name: str
    fetch: Callable[[str, str, str], pd.DataFrame]
    parse: Callable[[pd.DataFrame, str], pd.DataFrame]


# ---- Cooldown registry ----------------------------------------------------


def reset_cooldowns() -> None:
    """Forget every cooldown. Used by tests and by a caller forcing a retry."""
    _cooldown_until.clear()


def _on_cooldown(name: str) -> bool:
    until = _cooldown_until.get(name)
    if until is None:
        return False
    if datetime.now() < until:
        return True
    del _cooldown_until[name]
    return False


def _start_cooldown(name: str) -> None:
    _cooldown_until[name] = datetime.now() + _COOLDOWN
    logger.warning(
        "akshare upstream %r answered with a rate limit; skipping it for %s",
        name,
        _COOLDOWN,
    )


def _looks_like_rate_limit(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


# ---- Column normalisation -------------------------------------------------


def _normalise(raw: pd.DataFrame, mapping: dict, symbol: str, upstream: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise NoMarketDataError(symbol, symbol, f"{upstream} returned no rows")

    missing = [column for column in mapping if column not in raw.columns]
    if missing:
        raise VendorUnavailableError(
            f"{upstream} response for {symbol} is missing column(s) {missing}; "
            f"got {list(raw.columns)}"
        )

    frame = raw.rename(columns=mapping)[["Date", *_VALUE_COLUMNS]].copy()
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame.set_index("Date").sort_index()


_EM_MAP = _COLUMN_MAP

_SINA_MAP = {
    "date": "Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}

_TX_MAP = {
    "date": "Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}


def parse_hist(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Eastmoney: Chinese column names."""
    return _normalise(raw, _EM_MAP, symbol, "eastmoney")


def parse_hist_sina(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Sina: lowercase english column names."""
    return _normalise(raw, _SINA_MAP, symbol, "sina")


def parse_hist_tx(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Tencent: lowercase english column names, close ordered before high."""
    return _normalise(raw, _TX_MAP, symbol, "tencent")


# ---- Upstream fetchers ----------------------------------------------------
#
# Each takes the bare code (``600009``) and formats it the way its own endpoint
# wants: Eastmoney wants the bare code, Sina and Tencent want the lowercase
# market-prefixed form.


def _fetch_eastmoney(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    return ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust="qfq",
    )


def _fetch_sina(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    return ak.stock_zh_a_daily(
        symbol=_lower_prefixed(code),
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust="qfq",
    )


def _fetch_tencent(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    return ak.stock_zh_a_hist_tx(
        symbol=_lower_prefixed(code),
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust="qfq",
    )


def _lower_prefixed(code: str) -> str:
    """``600009`` -> ``sh600009`` (Sina and Tencent spell the market lowercase).

    Shanghai codes are not only the 6xxxxx shares: 5xxxxx are the Shanghai-listed
    funds (588/589 ETFs included). Treating them as Shenzhen is what made Sina
    answer "没有 sz589660" for a Shanghai ETF.
    """
    return code.lower() if code[:2].isalpha() else (
        f"sh{code}" if code.startswith(("6", "5"))
        else f"bj{code}" if code.startswith(("4", "8"))
        else f"sz{code}"
    )


_UPSTREAMS: tuple[Upstream, ...] = (
    Upstream("eastmoney", _fetch_eastmoney, parse_hist),
    Upstream("sina", _fetch_sina, parse_hist_sina),
    Upstream("tencent", _fetch_tencent, parse_hist_tx),
)

# ---- Listed funds ---------------------------------------------------------
#
# 588xxx/589xxx (Shanghai) and 159xxx (Shenzhen) are ETFs, not companies. The
# stock endpoints above do not reliably serve them: Sina rejects the code, and
# Eastmoney is the only stock-side host that answers, so a single throttle left
# an ETF run with no prices at all. akshare's fund endpoints do serve them, and
# fund_etf_hist_sina keeps working while Eastmoney is rate-limited — which is
# exactly the fallback this chain needs.

_FUND_PREFIXES = (
    "15", "16", "18",  # Shenzhen funds / LOFs
    "50", "51", "52", "53", "54", "55", "56", "57", "58", "59",  # Shanghai funds
)


def _is_fund(code: str) -> bool:
    """Whether the bare code names a listed fund rather than an operating company."""
    return str(code)[:2] in _FUND_PREFIXES


def _fetch_etf_em(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    return ak.fund_etf_hist_em(
        symbol=code,
        period="daily",
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust="",
    )


def _fetch_etf_sina(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Eastmoney-independent fallback: Sina serves fund history by exchange prefix."""
    import akshare as ak

    return ak.fund_etf_hist_sina(symbol=_lower_prefixed(code))


_FUND_UPSTREAMS: tuple[Upstream, ...] = (
    Upstream("etf-eastmoney", _fetch_etf_em, parse_hist),
    Upstream("etf-sina", _fetch_etf_sina, parse_hist_sina),
)


def _chain_for(code: str) -> tuple[Upstream, ...]:
    """Pick the endpoint family that can actually serve this instrument."""
    return _FUND_UPSTREAMS if _is_fund(code) else _UPSTREAMS


def reset_history_cache() -> None:
    """Drop the in-process history cache; tests and a forced refresh call this."""
    _memory.clear()


def _cache_path(symbol: str) -> Path:
    """Where this symbol's downloaded history lives between processes."""
    from tradingagents.dataflows.config import get_config

    directory = Path(get_config().get("data_cache_dir") or ".")
    directory.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", symbol.upper()) or "symbol"
    return directory / f"{safe}-akshare-ohlcv.csv"


def _cache_is_fresh(path: Path) -> bool:
    try:
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return False
    return age.total_seconds() <= OHLCV_CACHE_TTL_SECONDS


def _read_cache(path: Path) -> pd.DataFrame | None:
    try:
        frame = pd.read_csv(path, index_col=0, parse_dates=True)
    except Exception as exc:  # noqa: BLE001 - an unreadable cache is simply a miss
        logger.warning("ignoring unreadable akshare cache %s: %s", path, exc)
        return None
    return frame if not frame.empty else None


def _write_cache(path: Path, frame: pd.DataFrame) -> None:
    try:
        frame.to_csv(path)
    except OSError as exc:
        logger.warning("could not write akshare cache %s: %s", path, exc)


def _walk_upstreams(
    code: str,
    symbol: str,
    start_date: str,
    end_date: str,
    chain: tuple[Upstream, ...],
) -> pd.DataFrame:
    """Ask each upstream in turn; return the first usable frame."""
    failures: list[str] = []
    empty: list[str] = []
    for upstream in chain:
        if _on_cooldown(upstream.name):
            failures.append(f"{upstream.name}: cooling down after a rate limit")
            continue
        try:
            raw = upstream.fetch(code, start_date, end_date)
            frame = upstream.parse(raw, symbol)
        except NoMarketDataError as exc:
            # This host had nothing for the window; another may still have rows.
            # Treating it as "the symbol has no data" would abort the chain, and
            # the router logs NoMarketDataError silently, so the run would look
            # like the instrument is uncovered.
            empty.append(f"{upstream.name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - one host failing is not fatal
            if _looks_like_rate_limit(exc):
                _start_cooldown(upstream.name)
            else:
                logger.warning("akshare upstream %r failed: %s", upstream.name, exc)
            failures.append(f"{upstream.name}: {' '.join(str(exc).split())[:160]}")
            continue
        return frame

    # Only when every host we actually reached came back empty is the symbol
    # itself without data. If any host failed unavailable, the chain was impeded
    # and that is a vendor outage, not a statement about the instrument.
    if not failures and empty:
        raise NoMarketDataError(symbol, code, "; ".join(empty))

    raise VendorUnavailableError(
        f"no A-share price upstream could serve {symbol}: "
        + "; ".join(failures + empty)
    )


def load_history(
    symbol: str,
    upstreams: tuple[Upstream, ...] | None = None,
) -> pd.DataFrame:
    """The symbol's long history, from memory, from disk, or from the chain.

    One download serves every request for that symbol until the TTL expires. The
    analyst asks for ~8 indicators over identical windows, so fetching per call
    multiplied the request count by that factor — which is what made the upstreams
    throttle. Passing ``upstreams`` explicitly bypasses all caching, so the
    fallback and cooldown tests always exercise the chain.
    """
    code = require_a_share(symbol)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - relativedelta(days=_MAX_HISTORY_DAYS)).strftime(
        "%Y-%m-%d"
    )

    if upstreams is not None:
        ensure_domestic_no_proxy()
        return _walk_upstreams(code, symbol, start_date, end_date, tuple(upstreams))

    cached = _memory.get(symbol)
    if cached is not None:
        age = (datetime.now() - cached[0]).total_seconds()
        if age <= OHLCV_CACHE_TTL_SECONDS:
            return cached[1]
        del _memory[symbol]

    path = _cache_path(symbol)
    if _cache_is_fresh(path):
        frame = _read_cache(path)
        if frame is not None:
            _memory[symbol] = (datetime.now(), frame)
            return frame

    ensure_domestic_no_proxy()
    frame = _walk_upstreams(code, symbol, start_date, end_date, _chain_for(code))
    _write_cache(path, frame)
    _memory[symbol] = (datetime.now(), frame)
    return frame


def fetch_history(
    symbol: str,
    start_date: str,
    end_date: str,
    upstreams: tuple[Upstream, ...] | None = None,
) -> pd.DataFrame:
    """Daily bars for the requested window, sliced from the cached history."""
    require_a_share(symbol)
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    frame = load_history(symbol, upstreams=upstreams)
    window = frame.loc[pd.Timestamp(start_date) : pd.Timestamp(end_date)]
    if window.empty:
        raise NoMarketDataError(
            symbol, symbol, f"no rows between {start_date} and {end_date}"
        )
    return window


def format_ohlcv(symbol: str, frame: pd.DataFrame, start_date: str, end_date: str) -> str:
    """The text the market analyst reads, matching the yfinance vendor's shape."""
    header = f"# Stock data for {symbol.upper()} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(frame)}\n\n"
    return header + frame.to_csv()


def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Router entry point for ``get_stock_data``; raises for foreign symbols."""
    require_a_share(symbol)
    frame = fetch_history(symbol, start_date, end_date)
    return format_ohlcv(symbol, frame, start_date, end_date)


def get_indicators(symbol: str, indicator: str, as_of_date: str, look_back_days: int) -> str:
    """Router entry point for ``get_indicators``.

    Mirrors the yfinance vendor's window: compute over a long tail so long moving
    averages are defined, then report the requested look-back window.
    """
    from stockstats import wrap

    require_a_share(symbol)

    as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    window_start = as_of_dt - relativedelta(days=int(look_back_days))
    history_start = as_of_dt - relativedelta(days=_HISTORY_TAIL_DAYS)

    frame = fetch_history(symbol, history_start.strftime("%Y-%m-%d"), as_of_date)

    stats = wrap(frame.reset_index())
    stats["Date"] = pd.to_datetime(stats["Date"]).dt.strftime("%Y-%m-%d")
    stats[indicator]  # triggers stockstats to compute the column

    values = {}
    for _, row in stats.iterrows():
        value = row[indicator]
        values[row["Date"]] = "N/A" if pd.isna(value) else str(value)

    lines = []
    cursor = as_of_dt
    while cursor >= window_start:
        day = cursor.strftime("%Y-%m-%d")
        lines.append(
            f"{day}: {values.get(day, 'N/A: Not a trading day (weekend or holiday)')}"
        )
        cursor = cursor - relativedelta(days=1)

    header = (
        f"## {indicator} values from {window_start.strftime('%Y-%m-%d')} "
        f"to {as_of_date}:\n\n"
    )
    return header + "\n".join(lines) + "\n"