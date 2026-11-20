"""Tests for the ETF/LOF fund-level data helpers.

Only parsing/formatting is unit-tested: the network fetchers are thin wrappers
around akshare and are exercised by the integration smoke run instead.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.vendors.akshare import fund

NAV = pd.DataFrame(
    {
        "净值日期": ["2026-11-12", "2026-11-13", "2026-11-14"],
        "单位净值": [1.2674, 1.2329, 1.2301],
        "累计净值": [1.2674, 1.2329, 1.2301],
        "日增长率": [-0.37, -2.72, -0.23],
        "申购状态": ["场内买入"] * 3,
        "赎回状态": ["场内卖出"] * 3,
    }
)

HOLDINGS = pd.DataFrame(
    {
        "股票代码": ["688041", "688256", "688795", "688981", "688111"],
        "股票名称": ["海光信息", "寒武纪", "摩尔线程", "中芯国际", "金山办公"],
        "占净值比例": [4.98, 4.23, 2.68, 2.10, 1.90],
        "持股数": [10.11, 1.84, 2.05, 3.0, 0.5],
        "持仓市值": [2125.73, 1804.69, 1142.94, 900.0, 700.0],
        "季度": ["2026年2季度股票投资明细"] * 5,
    }
)

PRICES = pd.DataFrame(
    {"Close": [1.266, 1.235, 1.230]},
    index=pd.to_datetime(["2026-11-12", "2026-11-13", "2026-11-14"]),
)


def test_parse_nav_renames_and_sorts_by_date():
    parsed = fund.parse_nav(NAV.iloc[::-1], "589660.SS")
    assert list(parsed.columns) == ["Nav", "AccNav", "DailyPct", "Purchase", "Redeem"]
    assert parsed.index.is_monotonic_increasing
    assert parsed["Nav"].iloc[-1] == pytest.approx(1.2301)


def test_format_nav_states_the_latest_value():
    text = fund.format_nav(fund.parse_nav(NAV, "589660.SS"), "589660.SS")
    assert text.startswith("# ETF fund NAV for 589660.SS")
    assert "latest NAV date: 2026-11-14" in text
    assert "1.2301" in text


def test_parse_nav_rejects_an_empty_frame():
    with pytest.raises(NoMarketDataError):
        fund.parse_nav(NAV.iloc[0:0], "589660.SS")


def test_parse_nav_rejects_a_response_missing_columns():
    with pytest.raises(VendorUnavailableError):
        fund.parse_nav(NAV.drop(columns=["单位净值"]), "589660.SS")


def test_parse_holdings_sorts_the_newest_quarter_by_weight():
    older = HOLDINGS.assign(季度="2026年1季度股票投资明细")
    parsed = fund.parse_holdings(pd.concat([older, HOLDINGS]), "589660.SS")
    assert parsed["Quarter"].iloc[0] == "2026年2季度股票投资明细"
    assert parsed["Weight"].tolist()[:3] == [4.98, 4.23, 2.68]


def test_format_holdings_reports_concentration():
    text = fund.format_holdings(fund.parse_holdings(HOLDINGS, "589660.SS"), "589660.SS")
    assert "top3" in text and "top10" in text
    assert "海光信息" in text
    assert "quarter: 2026年2季度股票投资明细" in text


def test_premium_report_uses_dates_present_in_both_series():
    text = fund.build_premium_report(PRICES, fund.parse_nav(NAV, "589660.SS"), "589660.SS")
    assert "premium" in text
    # 2026-11-13: 1.235 / 1.2329 - 1 = +0.17%
    assert "+0.17%" in text


def test_premium_report_warns_when_nav_lags_the_close():
    stale_nav = fund.parse_nav(NAV.iloc[:2], "589660.SS")  # NAV ends 11-13
    text = fund.build_premium_report(PRICES, stale_nav, "589660.SS")
    assert "WARNING" in text
    assert "predates the latest close" in text


def test_premium_report_requires_a_common_date():
    nav = fund.parse_nav(NAV, "589660.SS")
    prices = pd.DataFrame(
        {"Close": [1.0]}, index=pd.to_datetime(["2026-10-01"])
    )
    with pytest.raises(NoMarketDataError):
        fund.build_premium_report(prices, nav, "589660.SS")


def test_market_analyst_offers_the_fund_tools():
    from tradingagents.agents.analysts import market_analyst

    names = {tool.name for tool in market_analyst.TOOLS}
    assert {"get_etf_nav", "get_etf_holdings", "get_etf_premium"} <= names