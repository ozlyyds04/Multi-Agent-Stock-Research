import logging
import time as _time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import pytest

from src.tools import news_tool
from src.tools.news_tool import fetch_news_feeds


def test_fetch_news_feeds_handles_empty_gracefully(monkeypatch):
    # If your implementation uses feedparser, patch it; if it uses requests, patch that instead.
    # This test is intentionally tolerant: it asserts "no crash" and list output.
    try:
        monkeypatch.setattr("src.tools.news_tool._fetch_bytes", lambda *a, **k: b"")
        monkeypatch.setattr("src.tools.news_tool.feedparser.parse", lambda *_args, **_kwargs: type("X", (), {"entries": []})())
    except Exception:
        pass

    out = fetch_news_feeds("AAPL", ["https://example.com/rss"], max_items=5)
    assert isinstance(out, list)


def test_fetch_news_feeds_log_lines_format_ok(monkeypatch):
    """在 DEBUG 级别下触发日志格式化，防止 %s/%d 参数错位导致崩溃。"""
    logger = news_tool.logger
    old_levels = [(logger, logger.level)]
    old_levels += [(h, h.level) for h in logger.handlers]
    logger.setLevel(logging.DEBUG)
    for h in logger.handlers:
        h.setLevel(logging.DEBUG)

    try:
        class FakeEntry:
            def __init__(self, link):
                self.title = "头条"
                self.link = link
                self.published = "2026-01-01"
                self.summary = ""

        class FakeFeed:
            entries = [FakeEntry("https://example.com/1"), FakeEntry("https://example.com/2")]

        monkeypatch.setattr("src.tools.news_tool._fetch_bytes", lambda *_a, **_k: b"<rss></rss>")
        monkeypatch.setattr("src.tools.news_tool.feedparser.parse", lambda *_a, **_k: FakeFeed())

        out = fetch_news_feeds("AAPL", ["https://example.com/rss?s={{symbol}}"], max_items=5)
        assert len(out) == 2
    finally:
        for obj, lvl in old_levels:
            obj.setLevel(lvl)


def test_fetch_news_feeds_hk_uses_dedicated_source(monkeypatch):
    seen = {}

    def fake_hk(symbol, max_items):
        seen["symbol"] = symbol
        return [{"title": "腾讯新闻", "link": "https://news.google.com/1", "published": "2026-08-16", "summary": "", "source": "Google 新闻"}]

    monkeypatch.setattr("src.tools.news_tool._fetch_hk_news", fake_hk)
    out = fetch_news_feeds("00700", ["https://example.com/rss?s={{symbol}}"], max_items=5)
    assert out[0]["source"] == "Google 新闻"
    assert seen["symbol"] == "00700"


def test_fetch_hk_news_builds_google_news_url(monkeypatch):
    captured = {}

    def fake_fetch(url, **kwargs):
        captured["url"] = url
        return b"<rss></rss>"

    monkeypatch.setattr("src.tools.news_tool._fetch_bytes", fake_fetch)
    monkeypatch.setattr("src.tools.news_tool._fetch_hk_company_name", lambda bare: "腾讯控股")
    monkeypatch.setattr("src.tools.news_tool.feedparser.parse", lambda *_a, **_k: type("X", (), {"entries": []})())

    out = news_tool._fetch_hk_news("00700", max_items=3)
    assert isinstance(out, list)
    assert "news.google.com" in captured["url"]
    assert "腾讯控股" in unquote(captured["url"])


def test_fetch_hk_company_name(monkeypatch):
    import pandas as pd

    df = pd.DataFrame({"公司名称": ["腾讯控股有限公司"]})
    monkeypatch.setattr("akshare.stock_hk_company_profile_em", lambda symbol: df)
    assert news_tool._fetch_hk_company_name("00700") == "腾讯"
    # 第二次命中进程内缓存
    assert news_tool._fetch_hk_company_name("00700") == "腾讯"


def test_fetch_bytes_429_fails_fast(monkeypatch):
    class Resp:
        status_code = 429
        content = b""

    monkeypatch.setattr("src.tools.news_tool.requests.get", lambda *a, **k: Resp())
    with pytest.raises(news_tool.NonRetryableHTTPError):
        news_tool._fetch_bytes("https://example.com", retry_cfg=news_tool.RetryConfig(max_retries=3))


def test_fetch_news_feeds_uses_google_fallback_when_rss_empty(monkeypatch):
    seen = {}

    def fake_fetch(url, **kwargs):
        if "news.google.com" in url:
            seen["google"] = url
            return b"<rss>google</rss>"
        return b"<rss>other</rss>"

    class Entry:
        title = "NVDA 头条"
        link = "https://news.google.com/1"
        published = "2026-08-18"
        summary = ""

    def fake_parse(content):
        class Feed:
            entries = [Entry()] if b"google" in content else []

        return Feed()

    monkeypatch.setattr("src.tools.news_tool._fetch_bytes", fake_fetch)
    monkeypatch.setattr("src.tools.news_tool.feedparser.parse", fake_parse)

    out = fetch_news_feeds("NVDA", ["https://example.com/rss"], max_items=5)

    assert len(out) == 1
    assert out[0]["source"] == "Google 新闻"
    assert "news.google.com" in seen["google"]


def test_fetch_google_news_prefers_recent_and_sorts_newest_first(monkeypatch):
    def fake_fetch(url, **kwargs):
        return b"<rss></rss>"

    class Entry:
        def __init__(self, days_ago, title):
            dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
            stamp = dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
            self.title = title
            self.link = "https://news.google.com/" + title
            self.published = stamp
            self.published_parsed = _time.strptime(stamp, "%a, %d %b %Y %H:%M:%S GMT")
            self.summary = ""

    class Feed:
        entries = [
            Entry(400, "old-news"),
            Entry(3, "new-3d"),
            Entry(2, "new-2d"),
            Entry(1, "new-1d"),
            Entry(4, "new-4d"),
        ]

    monkeypatch.setattr("src.tools.news_tool._fetch_bytes", fake_fetch)
    monkeypatch.setattr("src.tools.news_tool.feedparser.parse", lambda *_a, **_k: Feed())

    out = news_tool._fetch_google_news("测试", max_items=5)
    titles = [it["title"] for it in out]

    assert titles == ["new-1d", "new-2d", "new-3d", "new-4d"]
    assert "old-news" not in titles  # 近 90 天内已有足够条目，旧闻被过滤
    assert all(it["source"] == "Google 新闻" for it in out)
