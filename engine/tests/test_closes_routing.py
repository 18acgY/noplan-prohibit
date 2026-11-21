"""Settlement closes: A-shares from akshare, everything else from Yahoo."""

import pandas as pd
import pytest

from tradingagents.dataflows import closes


def test_a_share_closes_come_from_akshare(monkeypatch):
    idx = pd.to_datetime(["2026-11-09", "2026-11-10"])
    frame = pd.DataFrame({"Close": [24.3, 24.5]}, index=idx)
    seen = {}

    def fake_fetch(symbol, start, end):
        seen["call"] = (symbol, start, end)
        return frame

    monkeypatch.setattr(closes.akshare_market, "fetch_history", fake_fetch)
    monkeypatch.setattr(closes, "yahoo_get_closes", lambda *a, **k: pytest.fail("yahoo must not be used"))

    series = closes.get_closes("600009.SS", "2026-11-01", "2026-11-13")

    assert seen["call"][0] == "600009.SS"
    assert list(series.index) == list(idx)
    assert series.tolist() == [24.3, 24.5]


def test_us_closes_come_from_yahoo(monkeypatch):
    idx = pd.to_datetime(["2026-11-09"])
    monkeypatch.setattr(closes, "yahoo_get_closes", lambda sym, s, e: pd.Series([680.0], index=idx))
    monkeypatch.setattr(closes.akshare_market, "fetch_history", lambda *a, **k: pytest.fail("akshare must not be used"))

    series = closes.get_closes("SPY", "2026-11-01", "2026-11-13")

    assert series.iloc[0] == 680.0


def test_non_positive_closes_are_dropped(monkeypatch):
    idx = pd.to_datetime(["2026-11-09", "2026-11-10"])
    frame = pd.DataFrame({"Close": [24.3, 0.0]}, index=idx)
    monkeypatch.setattr(closes.akshare_market, "fetch_history", lambda *a, **k: frame)

    series = closes.get_closes("600009.SS", "2026-11-01", "2026-11-13")

    assert series.tolist() == [24.3]


def test_a_share_index_is_refused_instead_of_fetching_a_lookalike_stock():
    # 000001.SS 是上证综指；akshare 股票接口会把它当 000001 平安银行取回来
    with pytest.raises(ValueError, match="A-share index"):
        closes.get_closes("000001.SS", "2026-11-01", "2026-11-13")
    with pytest.raises(ValueError, match="A-share index"):
        closes.get_closes("399001.SZ", "2026-11-01", "2026-11-13")


def test_a_share_etf_is_not_treated_as_an_index(monkeypatch):
    idx = pd.to_datetime(["2026-11-09"])
    frame = pd.DataFrame({"Close": [3.95]}, index=idx)
    monkeypatch.setattr(closes.akshare_market, "fetch_history", lambda *a, **k: frame)

    assert closes.get_closes("510300.SS", "2026-11-01", "2026-11-13").iloc[0] == 3.95