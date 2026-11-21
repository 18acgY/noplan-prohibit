"""A-share company news from akshare's Eastmoney feed.

Yahoo Finance tags no articles for most A-share symbols, which left the news
analyst reporting an information vacuum. ``stock_news_em`` fills that gap; it
needs no key and returns Chinese-language company news, which is what the
analysts and the reader both want for a mainland listing.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorUnavailableError

from .guards import ensure_domestic_no_proxy, require_a_share

logger = logging.getLogger(__name__)

# Eastmoney's column names, mapped onto the vocabulary the prompt uses.
_NEWS_COLUMNS = {
    "新闻标题": "title",
    "新闻内容": "content",
    "发布时间": "published",
    "文章来源": "source",
    "新闻链接": "url",
}

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _parse_stamp(value: str):
    """The calendar date of a publication stamp, or None when unreadable."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_articles(raw: pd.DataFrame) -> list[dict]:
    """Turn a ``stock_news_em`` frame into plain records."""
    if raw is None or raw.empty:
        return []

    missing = [name for name in _NEWS_COLUMNS if name not in raw.columns]
    if missing:
        raise VendorUnavailableError(
            f"akshare news response is missing column(s) {missing}; got {list(raw.columns)}"
        )

    frame = raw.rename(columns=_NEWS_COLUMNS)
    records = []
    for _, row in frame.iterrows():
        records.append(
            {
                field: ("" if pd.isna(row[field]) else str(row[field]))
                for field in _NEWS_COLUMNS.values()
            }
        )
    return records


def filter_window(articles: list[dict], start_date: str, end_date: str) -> list[dict]:
    """Keep the articles published inside ``start_date``..``end_date`` inclusive.

    An article whose stamp cannot be read is dropped: putting it in the window
    would assert a date the vendor did not state.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    kept = []
    for article in articles:
        stamp = _parse_stamp(article.get("published", ""))
        if stamp is None or not (start <= stamp <= end):
            continue
        kept.append(article)
    return kept


def format_articles(ticker: str, articles: list[dict]) -> str:
    """The text the news analyst reads."""
    if not articles:
        return f"No news articles for {ticker.upper()} in the requested window.\n"

    lines = [f"# News for {ticker.upper()}: {len(articles)} article(s)\n"]
    for article in articles:
        lines.append(f"## {article['title']}")
        lines.append(f"- Published: {article['published']}")
        if article.get("source"):
            lines.append(f"- Source: {article['source']}")
        if article.get("url"):
            lines.append(f"- Link: {article['url']}")
        body = article.get("content") or ""
        if body:
            lines.append("")
            lines.append(body)
        lines.append("")
    return "\n".join(lines)


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Router entry point for ``get_news``."""
    code = require_a_share(ticker)
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    ensure_domestic_no_proxy()

    import akshare as ak

    try:
        raw = ak.stock_news_em(symbol=code)
    except Exception as exc:  # noqa: BLE001 - a scrape failure is a vendor outage
        raise VendorUnavailableError(
            f"akshare news request for {code} failed: {exc}"
        ) from exc

    articles = filter_window(parse_articles(raw), start_date, end_date)

    limit = get_config().get("news_article_limit") or 20
    return format_articles(ticker, articles[: int(limit)])