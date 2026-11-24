"""akshare 在 1.20.41 / 1.20.46 改过一批函数名，旧名直接删除。

引擎写的是旧名，装上新版 akshare 后 ETF 净值取不到（AttributeError），
股票链则静默降级。这里钉住兼容层的契约：先用新名，再退回旧名，都没有才报错。
"""

import sys
import types

import pytest

from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors.akshare import _compat, fund, market


def install_fake_akshare(monkeypatch, **functions):
    """把 sys.modules['akshare'] 换成只带指定函数的假模块。"""
    module = types.ModuleType("akshare")
    module.__version__ = "fake"
    for name, fn in functions.items():
        setattr(module, name, fn)
    monkeypatch.setitem(sys.modules, "akshare", module)
    return module


def test_resolve_prefers_the_new_name(monkeypatch):
    calls = []

    install_fake_akshare(
        monkeypatch,
        fund_exchange_nav_hist_em=lambda **kw: calls.append("new") or "new",
        fund_etf_fund_info_em=lambda **kw: calls.append("old") or "old",
    )

    result = _compat.resolve("fund_exchange_nav_hist_em", "fund_etf_fund_info_em")(fund="510300")

    assert result == "new"
    assert calls == ["new"], "新名存在时不应回退到旧名"


def test_resolve_falls_back_to_the_old_name(monkeypatch):
    install_fake_akshare(monkeypatch, fund_etf_fund_info_em=lambda **kw: "old-name-works")

    result = _compat.resolve("fund_exchange_nav_hist_em", "fund_etf_fund_info_em")(fund="510300")

    assert result == "old-name-works"


def test_resolve_raises_a_vendor_error_when_no_name_exists(monkeypatch):
    install_fake_akshare(monkeypatch)

    with pytest.raises(VendorUnavailableError) as excinfo:
        _compat.resolve("fund_exchange_nav_hist_em", "fund_etf_fund_info_em")

    assert "fund_exchange_nav_hist_em" in str(excinfo.value)


def test_fetch_nav_works_on_akshare_that_only_knows_the_new_name(monkeypatch):
    seen = {}

    def new_nav(fund, start_date, end_date):
        seen.update(fund=fund, start=start_date, end=end_date)
        return "frame"

    install_fake_akshare(monkeypatch, fund_exchange_nav_hist_em=new_nav)

    assert fund.fetch_nav("510300", "2026-11-05", "2026-11-13") == "frame"
    assert seen == {"fund": "510300", "start": "20261105", "end": "20261113"}


def test_fetch_nav_works_on_akshare_that_only_knows_the_old_name(monkeypatch):
    install_fake_akshare(monkeypatch, fund_etf_fund_info_em=lambda fund, start_date, end_date: "frame")

    assert fund.fetch_nav("510300", "2026-11-05", "2026-11-13") == "frame"


def test_eastmoney_history_works_on_akshare_that_only_knows_the_new_name(monkeypatch):
    seen = {}

    def new_hist(symbol, period, start_date, end_date, adjust):
        seen.update(symbol=symbol, period=period, adjust=adjust, start=start_date)
        return "frame"

    install_fake_akshare(monkeypatch, stock_zh_a_hist_em=new_hist)

    assert market._fetch_eastmoney("600009", "2026-11-05", "2026-11-13") == "frame"
    assert seen == {"symbol": "600009", "period": "daily", "adjust": "qfq", "start": "20261105"}


def test_eastmoney_history_works_on_akshare_that_only_knows_the_old_name(monkeypatch):
    install_fake_akshare(
        monkeypatch,
        stock_zh_a_hist=lambda symbol, period, start_date, end_date, adjust: "frame",
    )

    assert market._fetch_eastmoney("600009", "2026-11-05", "2026-11-13") == "frame"


def test_sina_history_works_on_akshare_that_only_knows_the_new_name(monkeypatch):
    seen = {}

    # 新函数 period 有默认值、旧函数没有这个参数，所以调用方一律不传 period。
    def new_sina(symbol, period="daily", start_date="", end_date="", adjust=""):
        seen.update(symbol=symbol, period=period, adjust=adjust)
        return "frame"

    install_fake_akshare(monkeypatch, stock_zh_a_hist_sina=new_sina)

    assert market._fetch_sina("600009", "2026-11-05", "2026-11-13") == "frame"
    assert seen == {"symbol": "sh600009", "period": "daily", "adjust": "qfq"}


def test_sina_history_works_on_akshare_that_only_knows_the_old_name(monkeypatch):
    install_fake_akshare(
        monkeypatch,
        stock_zh_a_daily=lambda symbol, start_date, end_date, adjust: "frame",
    )

    assert market._fetch_sina("600009", "2026-11-05", "2026-11-13") == "frame"