"""Tests for the akshare A-share data vendor.

Only the pure logic is unit-tested here: symbol mapping, the non-A-share decline
path, and formatting of vendor responses from fixture frames. Live network calls
sit behind the ``integration`` marker.

akshare speaks bare six-digit codes (``600009``) for price/news calls but a
market-prefixed form (``SH600009``) for the statement endpoints, while
TradingAgents feeds every vendor Yahoo-style symbols (``600009.SS``). The mapping
between the two is the part most likely to be silently wrong, so it is pinned here.
"""

from __future__ import annotations

import os
import time

import pandas as pd
import pytest

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows import router
from tradingagents.dataflows.vendors.akshare import fundamentals, market, news, symbols

# ---- A-share detection ----------------------------------------------------


def test_is_a_share_accepts_shanghai_yahoo_suffix():
    assert symbols.is_a_share("600009.SS") is True


def test_is_a_share_accepts_shenzhen_yahoo_suffix():
    assert symbols.is_a_share("000001.SZ") is True


def test_is_a_share_accepts_sh_alias_for_shanghai():
    assert symbols.is_a_share("600519.SH") is True


def test_is_a_share_accepts_bare_six_digit_code():
    assert symbols.is_a_share("600009") is True


def test_is_a_share_rejects_us_ticker():
    assert symbols.is_a_share("AAPL") is False


def test_is_a_share_rejects_hong_kong_ticker():
    assert symbols.is_a_share("0700.HK") is False


def test_is_a_share_rejects_crypto_pair():
    assert symbols.is_a_share("BTC-USD") is False


def test_is_a_share_rejects_six_digit_code_under_foreign_suffix():
    """A six-digit local code listed elsewhere is not an A-share."""
    assert symbols.is_a_share("600009.HK") is False


# ---- Bare code ------------------------------------------------------------


def test_bare_code_strips_exchange_suffix():
    assert symbols.bare_code("600009.SS") == "600009"


def test_bare_code_leaves_bare_input_alone():
    assert symbols.bare_code("600009") == "600009"


# ---- Market-prefixed code (statement endpoints) ---------------------------


def test_prefixed_code_uses_sh_for_shanghai_suffix():
    assert symbols.prefixed_code("600009.SS") == "SH600009"


def test_prefixed_code_uses_sz_for_shenzhen_suffix():
    assert symbols.prefixed_code("000001.SZ") == "SZ000001"


def test_prefixed_code_infers_sh_from_bare_six_prefix():
    assert symbols.prefixed_code("600009") == "SH600009"


def test_prefixed_code_infers_sz_from_bare_zero_prefix():
    assert symbols.prefixed_code("000001") == "SZ000001"


def test_prefixed_code_infers_bj_from_bare_four_prefix():
    assert symbols.prefixed_code("430047") == "BJ430047"


# ---- akshare frame normalisation -----------------------------------------
#
# stock_zh_a_hist answers with Chinese column names and a string date column.
# The rest of the codebase keys off Date/Open/High/Low/Close/Volume, so the
# translation is pinned against a fixture shaped like the real response.

AK_HIST = pd.DataFrame(
    {
        "日期": ["2026-10-28", "2026-10-29", "2026-10-30"],
        "股票代码": ["600009", "600009", "600009"],
        "开盘": [23.80, 23.85, 23.79],
        "收盘": [23.79, 23.79, 24.12],
        "最高": [23.95, 24.00, 24.20],
        "最低": [23.70, 23.75, 23.78],
        "成交量": [11586216, 11408111, 15264364],
    }
)


def test_parse_hist_renames_chinese_columns():
    parsed = market.parse_hist(AK_HIST, "600009.SS")
    assert list(parsed.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_parse_hist_keeps_row_order_and_values():
    parsed = market.parse_hist(AK_HIST, "600009.SS")
    assert parsed["Close"].tolist() == [23.79, 23.79, 24.12]
    assert parsed["Volume"].tolist() == [11586216, 11408111, 15264364]


def test_parse_hist_indexes_by_date():
    parsed = market.parse_hist(AK_HIST, "600009.SS")
    assert parsed.index.name == "Date"
    assert parsed.index[-1].strftime("%Y-%m-%d") == "2026-10-30"


def test_parse_hist_rejects_empty_frame():
    with pytest.raises(NoMarketDataError):
        market.parse_hist(AK_HIST.iloc[0:0], "600009.SS")


# ---- OHLCV text format ----------------------------------------------------
#
# The agents read this as plain text, so the header the yfinance vendor emits is
# reproduced exactly; a silent format change would degrade every market analyst
# report without failing anything.


def test_format_ohlcv_states_symbol_and_window_in_header():
    parsed = market.parse_hist(AK_HIST, "600009.SS")
    text = market.format_ohlcv("600009.SS", parsed, "2026-10-28", "2026-10-30")
    assert text.startswith("# Stock data for 600009.SS from 2026-10-28 to 2026-10-30")


def test_format_ohlcv_reports_total_records():
    parsed = market.parse_hist(AK_HIST, "600009.SS")
    text = market.format_ohlcv("600009.SS", parsed, "2026-10-28", "2026-10-30")
    assert "# Total records: 3" in text


def test_format_ohlcv_includes_each_trading_day():
    parsed = market.parse_hist(AK_HIST, "600009.SS")
    text = market.format_ohlcv("600009.SS", parsed, "2026-10-28", "2026-10-30")
    assert "2026-10-30" in text
    assert "24.12" in text


# ---- Decline path for non-A-share symbols ---------------------------------
#
# The router walks a vendor chain, so the A-share vendor must decline foreign
# symbols loudly and cheaply rather than answer with an empty frame — and must do
# so before touching the network.


def test_get_stock_data_declines_non_a_share_without_network():
    with pytest.raises(NoMarketDataError):
        market.get_stock_data("AAPL", "2026-10-01", "2026-10-30")


def test_parse_hist_is_reached_only_for_a_shares():
    """A foreign symbol is rejected by the guard, not by an empty response."""
    with pytest.raises(NoMarketDataError):
        market.get_stock_data("0700.HK", "2026-10-01", "2026-10-30")


# ---- Company news ---------------------------------------------------------
#
# Yahoo carries no news for most A-share symbols, which is what left the news
# analyst with an empty brief. stock_news_em is the replacement, so its column
# names and the window filter are pinned here.

AK_NEWS = pd.DataFrame(
    {
        "关键词": ["600009", "600009", "600009"],
        "新闻标题": [
            "上海机场：10月旅客吞吐量同比增长",
            "上海机场免税业务招标落地",
            "旧闻：2026年半年报点评",
        ],
        "新闻内容": ["内容一", "内容二", "内容三"],
        "发布时间": [
            "2026-10-30 09:15:00",
            "2026-10-29 18:02:00",
            "2026-09-01 10:00:00",
        ],
        "文章来源": ["证券时报", "上海证券报", "某财经"],
        "新闻链接": ["https://a", "https://b", "https://c"],
    }
)


def test_parse_articles_keeps_the_fields_the_agent_reads():
    articles = news.parse_articles(AK_NEWS)
    assert articles[0]["title"] == "上海机场：10月旅客吞吐量同比增长"
    assert articles[0]["source"] == "证券时报"
    assert articles[0]["url"] == "https://a"


def test_filter_window_drops_articles_outside_the_period():
    articles = news.parse_articles(AK_NEWS)
    kept = news.filter_window(articles, "2026-10-26", "2026-10-30")
    assert [a["title"] for a in kept] == [
        "上海机场：10月旅客吞吐量同比增长",
        "上海机场免税业务招标落地",
    ]


def test_format_articles_names_title_source_and_date():
    articles = news.filter_window(news.parse_articles(AK_NEWS), "2026-10-26", "2026-10-30")
    text = news.format_articles("600009.SS", articles)
    assert "上海机场：10月旅客吞吐量同比增长" in text
    assert "证券时报" in text
    assert "2026-10-30" in text


def test_format_articles_reports_an_empty_window():
    text = news.format_articles("600009.SS", [])
    assert "No news" in text


def test_get_news_declines_non_a_share():
    with pytest.raises(NoMarketDataError):
        news.get_news("AAPL", "2026-10-01", "2026-10-30")


# ---- Fundamentals ---------------------------------------------------------


AK_PROFILE = pd.DataFrame(
    {
        "item": ["股票代码", "股票简称", "行业", "上市时间", "总市值", "流通市值"],
        "value": ["600009", "上海机场", "机场航运", "1998-02-18", 6.0e10, 6.0e10],
    }
)


def test_format_profile_surfaces_company_identity():
    text = fundamentals.format_profile("600009.SS", AK_PROFILE)
    assert "上海机场" in text
    assert "机场航运" in text


def test_get_fundamentals_declines_non_a_share():
    with pytest.raises(NoMarketDataError):
        fundamentals.get_fundamentals("AAPL")


def test_get_balance_sheet_declines_non_a_share():
    with pytest.raises(NoMarketDataError):
        fundamentals.get_balance_sheet("0700.HK")


def test_get_income_statement_declines_non_a_share():
    with pytest.raises(NoMarketDataError):
        fundamentals.get_income_statement("BTC-USD")


# ---- Multi-upstream fallback and cooldown --------------------------------
#
# Eastmoney, Sina and Tencent are separate hosts behind separate IP limits, so the
# chain is what keeps an A-share run alive when one of them throttles. The cooldown
# exists because Eastmoney's own message warns that retrying extends the penalty:
# a run makes dozens of tool calls, so re-probing a throttled host each time would
# drag a five-minute outage out to forty.

SINA_HIST = pd.DataFrame(
    {
        "date": ["2026-10-28", "2026-10-29", "2026-10-30"],
        "open": [23.80, 23.85, 23.79],
        "high": [23.95, 24.00, 24.20],
        "low": [23.70, 23.75, 23.78],
        "close": [23.79, 23.79, 24.12],
        "volume": [11586216, 11408111, 15264364],
    }
)

TX_HIST = pd.DataFrame(
    {
        "date": ["2026-10-28", "2026-10-29", "2026-10-30"],
        "open": [23.80, 23.85, 23.79],
        "close": [23.79, 23.79, 24.12],
        "high": [23.95, 24.00, 24.20],
        "low": [23.70, 23.75, 23.78],
        "volume": [11586216, 11408111, 15264364],
    }
)

_RATE_LIMIT_TEXT = "东方财富 push2 行情服务器断开了连接，当前 IP 被临时限流"


def test_parse_hist_sina_normalises_english_columns():
    parsed = market.parse_hist_sina(SINA_HIST, "600009.SS")
    assert list(parsed.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert parsed["Close"].tolist() == [23.79, 23.79, 24.12]


def test_parse_hist_tx_normalises_its_own_column_order():
    """Tencent emits open,close,high,low,volume — close before high."""
    parsed = market.parse_hist_tx(TX_HIST, "600009.SS")
    assert list(parsed.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert parsed["High"].tolist() == [23.95, 24.00, 24.20]


def _stub_upstream(name, behaviour, calls, parse=None):
    """An Upstream whose fetch records its name and replays a canned outcome."""

    def fetch(code, start_date, end_date):
        calls.append(name)
        outcome = behaviour()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return market.Upstream(name=name, fetch=fetch, parse=parse or market.parse_hist_sina)


def test_fetch_history_falls_back_to_the_next_upstream():
    market.reset_cooldowns()
    calls = []
    chain = [
        _stub_upstream("eastmoney", lambda: RuntimeError("boom"), calls),
        _stub_upstream("sina", lambda: SINA_HIST, calls),
    ]
    frame = market.fetch_history("600009.SS", "2026-10-28", "2026-10-30", upstreams=chain)
    assert calls == ["eastmoney", "sina"]
    assert frame["Close"].tolist()[-1] == 24.12


def test_rate_limited_upstream_is_skipped_on_the_next_call():
    market.reset_cooldowns()
    calls = []
    chain = [
        _stub_upstream("eastmoney", lambda: RuntimeError(_RATE_LIMIT_TEXT), calls),
        _stub_upstream("sina", lambda: SINA_HIST, calls),
    ]
    market.fetch_history("600009.SS", "2026-10-28", "2026-10-30", upstreams=chain)
    calls.clear()
    market.fetch_history("600009.SS", "2026-10-28", "2026-10-30", upstreams=chain)
    assert calls == ["sina"], "a throttled upstream must not be re-probed while cooling"


def test_ordinary_failure_does_not_start_a_cooldown():
    """Only rate limits cool down; a transient error should still be retried."""
    market.reset_cooldowns()
    calls = []
    chain = [
        _stub_upstream("eastmoney", lambda: RuntimeError("connection reset"), calls),
        _stub_upstream("sina", lambda: SINA_HIST, calls),
    ]
    market.fetch_history("600009.SS", "2026-10-28", "2026-10-30", upstreams=chain)
    calls.clear()
    market.fetch_history("600009.SS", "2026-10-28", "2026-10-30", upstreams=chain)
    assert calls == ["eastmoney", "sina"]


def test_every_upstream_failing_names_each_reason():
    market.reset_cooldowns()
    calls = []
    chain = [
        _stub_upstream("eastmoney", lambda: RuntimeError("em down"), calls),
        _stub_upstream("sina", lambda: RuntimeError("sina down"), calls),
    ]
    with pytest.raises(VendorUnavailableError) as info:
        market.fetch_history("600009.SS", "2026-10-28", "2026-10-30", upstreams=chain)
    message = str(info.value)
    assert "eastmoney" in message
    assert "sina" in message


def test_empty_frame_from_one_upstream_does_not_abort_the_chain():
    """An empty answer is this host's problem, not the instrument's.

    Sina returns an empty frame for some ranges. Treating that as "no data for the
    symbol" would abort the chain and lose Tencent's perfectly good answer — and
    the router swallows NoMarketDataError without a log line, so the run would look
    like the symbol had no data at all.
    """
    market.reset_cooldowns()
    calls = []
    chain = [
        _stub_upstream("eastmoney", lambda: RuntimeError(_RATE_LIMIT_TEXT), calls),
        _stub_upstream("sina", lambda: SINA_HIST.iloc[0:0], calls),
        _stub_upstream("tencent", lambda: TX_HIST, calls, parse=market.parse_hist_tx),
    ]
    frame = market.fetch_history("600009.SS", "2026-10-28", "2026-10-30", upstreams=chain)
    assert calls == ["eastmoney", "sina", "tencent"]
    assert frame["Close"].tolist()[-1] == 24.12


def test_every_upstream_empty_raises_no_market_data():
    """Only when no host has rows is the symbol itself without data."""
    market.reset_cooldowns()
    calls = []
    chain = [
        _stub_upstream("sina", lambda: SINA_HIST.iloc[0:0], calls),
        _stub_upstream("tencent", lambda: TX_HIST.iloc[0:0], calls, parse=market.parse_hist_tx),
    ]
    with pytest.raises(NoMarketDataError):
        market.fetch_history("600009.SS", "2026-10-28", "2026-10-30", upstreams=chain)


# ---- Router registration --------------------------------------------------
#
# With no explicit data_vendors setting the router walks every registered vendor
# in dict order, so being first in the table is what makes akshare the primary
# source. These pin both halves: akshare ahead, and the existing vendors still
# present so non-A-share symbols keep working.

AKSHARE_PRIMARY_METHODS = (
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
)


@pytest.mark.parametrize("method", AKSHARE_PRIMARY_METHODS)
def test_akshare_is_registered_first_for(method):
    assert list(router.VENDOR_METHODS[method])[0] == "akshare"


@pytest.mark.parametrize("method", AKSHARE_PRIMARY_METHODS)
def test_yfinance_still_registered_as_fallback_for(method):
    assert "yfinance" in router.VENDOR_METHODS[method]


def test_us_fundamentals_keep_sec_edgar_in_the_chain():
    """A-share data must not displace the SEC vendor US filings rely on."""
    assert "sec_edgar" in router.VENDOR_METHODS["get_balance_sheet"]


# ---- Request economy: one load per symbol per TTL ------------------------
#
# A nine-indicator pass used to issue nine identical full-history downloads — the
# same window, nine times — which is what kept tripping the upstreams' rate limits.
# The yfinance path loads years of data once per symbol and caches it; these tests
# pin the same behaviour here, plus the escape hatch: an explicit upstream chain
# bypasses caching entirely, so the fallback and cooldown tests above stay honest.

_TTL = 900


def _history_frame(periods: int = 1300) -> pd.DataFrame:
    index = pd.bdate_range(end=pd.Timestamp("2026-10-30"), periods=periods, name="Date")
    return pd.DataFrame(
        {"Open": 24.0, "High": 24.3, "Low": 23.7, "Close": 24.1, "Volume": 12_000_000},
        index=index,
    )


def _counting_upstream(calls):
    def fetch(code, start_date, end_date):
        calls.append((code, start_date, end_date))
        return _history_frame()

    # The frame is already normalised, so the parse step is a no-op.
    return market.Upstream(name="stub", fetch=fetch, parse=lambda raw, symbol: raw)


def _isolate_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "_cache_path", lambda symbol: tmp_path / f"{symbol}.csv")
    market.reset_history_cache()


def test_indicators_share_one_history_load(monkeypatch, tmp_path):
    calls = []
    _isolate_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(market, "_UPSTREAMS", (_counting_upstream(calls),))

    for indicator in ("rsi", "macd", "atr", "close_200_sma"):
        market.get_indicators("600009.SS", indicator, "2026-11-02", 60)

    assert len(calls) == 1, f"expected one download for the whole pass, got {len(calls)}"


def test_repeat_request_within_ttl_does_not_refetch(monkeypatch, tmp_path):
    calls = []
    _isolate_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(market, "_UPSTREAMS", (_counting_upstream(calls),))

    market.fetch_history("600009.SS", "2026-10-01", "2026-10-30")
    market.fetch_history("600009.SS", "2026-10-01", "2026-10-30")

    assert len(calls) == 1


def test_history_cache_survives_a_process_restart(monkeypatch, tmp_path):
    calls = []
    _isolate_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(market, "_UPSTREAMS", (_counting_upstream(calls),))

    market.fetch_history("600009.SS", "2026-10-01", "2026-10-30")
    market.reset_history_cache()  # simulate a new process, same cache directory
    market.fetch_history("600009.SS", "2026-10-01", "2026-10-30")

    assert len(calls) == 1, "the file on disk should serve the second process"


def test_stale_history_cache_triggers_a_refetch(monkeypatch, tmp_path):
    calls = []
    _isolate_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(market, "_UPSTREAMS", (_counting_upstream(calls),))

    market.fetch_history("600009.SS", "2026-10-01", "2026-10-30")
    path = market._cache_path("600009.SS")
    old = time.time() - (_TTL + 60)
    os.utime(path, (old, old))
    market.reset_history_cache()
    market.fetch_history("600009.SS", "2026-10-01", "2026-10-30")

    assert len(calls) == 2


def test_fetch_history_returns_only_the_requested_window(monkeypatch, tmp_path):
    calls = []
    _isolate_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(market, "_UPSTREAMS", (_counting_upstream(calls),))

    frame = market.fetch_history("600009.SS", "2026-10-02", "2026-10-30")

    assert frame.index.min() >= pd.Timestamp("2026-10-02")
    assert frame.index.max() <= pd.Timestamp("2026-10-30")
    assert 0 < len(frame) < 25


def test_explicit_upstreams_bypass_the_cache(monkeypatch, tmp_path):
    """The fallback and cooldown tests above rely on every call reaching the chain."""
    calls = []
    _isolate_cache(monkeypatch, tmp_path)
    chain = (_counting_upstream(calls),)

    market.fetch_history("600009.SS", "2026-10-01", "2026-10-30", upstreams=chain)
    market.fetch_history("600009.SS", "2026-10-01", "2026-10-30", upstreams=chain)

    assert len(calls) == 2


# ---- Default vendor configuration ----------------------------------------
#
# Registering akshare in VENDOR_METHODS is not enough. ``route_to_vendor`` treats
# an explicit data_vendors value as the ENTIRE chain (#988/#289) and never falls
# back to the registry order, so a default naming only yfinance leaves the A-share
# vendor registered and still never called. That is precisely the bug that left
# live runs with no data while every other test here passed: they asserted registry
# order, which explicit configuration bypasses entirely.


@pytest.mark.parametrize(
    "category",
    ("core_stock_apis", "technical_indicators", "news_data"),
)
def test_default_chain_reaches_akshare_first(category):
    from tradingagents.default_config import DEFAULT_CONFIG

    chain = [v.strip() for v in DEFAULT_CONFIG["data_vendors"][category].split(",")]
    assert chain[0] == "akshare", f"{category} chain starts with {chain}"


def test_statements_keep_sec_edgar_ahead_of_akshare():
    """SEC EDGAR stays first for US filings, which is what it is there for.

    ``get_fundamentals`` is unaffected: sec_edgar is not registered for that method,
    so the router filters it out and akshare leads the profile request.
    """
    from tradingagents.default_config import DEFAULT_CONFIG

    chain = [
        v.strip()
        for v in DEFAULT_CONFIG["data_vendors"]["fundamental_data"].split(",")
    ]
    assert chain[0] == "sec_edgar", chain
    assert "akshare" in chain
    assert "yfinance" in chain


def test_default_chain_still_names_a_fallback_for_other_markets():
    """akshare declines foreign symbols, so the chain must continue past it."""
    from tradingagents.default_config import DEFAULT_CONFIG

    chain = [
        v.strip()
        for v in DEFAULT_CONFIG["data_vendors"]["core_stock_apis"].split(",")
    ]
    assert "yfinance" in chain