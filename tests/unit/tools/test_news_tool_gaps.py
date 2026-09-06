"""覆盖 news_tool.py 的低覆盖分支（mock requests / feedparser / akshare / 限流）。"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from src.tools import news_tool as nt
from src.utils.resilience import RetryConfig


def _resp(status=200, content=b"ok", json_payload=None):
    class _R:
        def __init__(self):
            self.status_code = status
            self.content = content

        def raise_for_status(self):
            if status >= 400:
                raise RuntimeError(f"http {status}")

        def json(self):
            return json_payload or {}

    return _R()


def test_bare_symbol():
    assert nt._bare_symbol(" 00700.HK ") == "00700"


def test_fetch_bytes_branches(monkeypatch):
    monkeypatch.setattr(nt, "check_rate_limit", lambda *a, **k: None)

    def _get(*a, **k):
        return _resp(200, b"abc")

    monkeypatch.setattr(nt.requests, "get", _get)
    assert nt._fetch_bytes("http://x", retry_cfg=RetryConfig(max_retries=0)) == b"abc"

    # 429 -> NonRetryable 快速失败
    monkeypatch.setattr(nt.requests, "get", lambda *a, **k: _resp(429))
    with pytest.raises(nt.NonRetryableHTTPError, match="429"):
        nt._fetch_bytes("http://x", retry_cfg=RetryConfig(max_retries=0))

    # 404 -> 非重试
    monkeypatch.setattr(nt.requests, "get", lambda *a, **k: _resp(404))
    with pytest.raises(nt.NonRetryableHTTPError, match="404"):
        nt._fetch_bytes("http://x", retry_cfg=RetryConfig(max_retries=0))

    # 本地限流提前失败
    def _rate(*a, **k):
        from src.runtime.ratelimit import RateLimitExceeded

        raise RateLimitExceeded("too fast")

    monkeypatch.setattr(nt, "check_rate_limit", _rate)
    with pytest.raises(nt.NonRetryableHTTPError, match="本地限流"):
        nt._fetch_bytes("http://x")


def test_fetch_em_announcements(monkeypatch):
    monkeypatch.setattr(nt, "check_rate_limit", lambda *a, **k: None)
    payload = {"data": {"list": [{"title": "公告A", "art_code": "x1", "display_time": "2026-01-01 10:00:00"}]}}
    monkeypatch.setattr(nt.requests, "get", lambda *a, **k: _resp(json_payload=payload))
    out = nt._fetch_em_announcements("600519")
    assert out and out[0]["title"] == "公告A" and out[0]["source"] == "东方财富公告"

    # 无有效标题被过滤
    monkeypatch.setattr(nt.requests, "get", lambda *a, **k: _resp(json_payload={"data": {"list": [{"title": ""}]}}))
    assert nt._fetch_em_announcements("600519") == []

    # 本地限流 -> []
    def _rate(*a, **k):
        from src.runtime.ratelimit import RateLimitExceeded

        raise RateLimitExceeded("x")

    monkeypatch.setattr(nt, "check_rate_limit", _rate)
    assert nt._fetch_em_announcements("600519") == []

    # HTTP 异常 -> []
    monkeypatch.setattr(nt, "check_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(nt.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert nt._fetch_em_announcements("600519") == []


def test_fetch_hk_company_name(monkeypatch):
    # 缓存命中
    nt._HK_COMPANY_CACHE["01810"] = "小米集团"
    assert nt._fetch_hk_company_name("01810") == "小米集团"
    nt._HK_COMPANY_CACHE.clear()

    # 本地限流 -> 空缓存
    def _rate(*a, **k):
        from src.runtime.ratelimit import RateLimitExceeded

        raise RateLimitExceeded("x")

    monkeypatch.setattr(nt, "check_rate_limit", _rate)
    assert nt._fetch_hk_company_name("01810") == ""
    assert nt._HK_COMPANY_CACHE["01810"] == ""
    nt._HK_COMPANY_CACHE.clear()

    # AKShare 成功，去掉后缀
    monkeypatch.setattr(nt, "check_rate_limit", lambda *a, **k: None)

    class _DF:
        empty = False
        columns = ["公司名称"]

        class _iloc:
            def __getitem__(self, i):
                return {"公司名称": "小米集团有限公司"}

        iloc = _iloc()

    monkeypatch.setattr("akshare.stock_hk_company_profile_em", lambda symbol=None: _DF())
    assert nt._fetch_hk_company_name("01810") == "小米"
    nt._HK_COMPANY_CACHE.clear()


def _fake_feed(entries):
    class _E:
        pass

    class _Feed:
        pass

    feed = _Feed()
    feed.entries = []
    for title, link, published, parsed in entries:
        e = _E()
        e.title = title
        e.link = link
        e.published = published
        e.summary = ""
        e.published_parsed = parsed
        feed.entries.append(e)
    return feed


def test_fetch_google_news(monkeypatch):
    now = datetime.now(timezone.utc)
    recent_ts = time.gmtime((now - timedelta(days=1)).timestamp())
    old_ts = time.gmtime((now - timedelta(days=200)).timestamp())
    feed = _fake_feed(
        [
            ("近期新闻", "https://e.com/1", "2026-01-02", recent_ts),
            ("过时新闻", "https://e.com/2", "2025-01-01", old_ts),
        ]
    )
    monkeypatch.setattr(nt, "_fetch_bytes", lambda *a, **k: b"<rss></rss>")
    monkeypatch.setattr(nt.feedparser, "parse", lambda *a, **k: feed)
    out = nt._fetch_google_news("AAPL", max_items=2)
    assert len(out) == 1 and out[0]["title"] == "近期新闻"  # 近 90 天过滤

    # 异常 -> []
    monkeypatch.setattr(nt, "_fetch_bytes", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    assert nt._fetch_google_news("AAPL") == []


def test_fetch_news_feeds_branches(monkeypatch):
    # A 股 -> 东财公告
    monkeypatch.setattr(nt, "_fetch_em_announcements", lambda s, m: [{"title": "公告", "link": "a"}])
    assert nt.fetch_news_feeds("600519", []) == [{"title": "公告", "link": "a"}]

    # 港股 -> 港股新闻
    monkeypatch.setattr(nt, "_fetch_hk_news", lambda s, m: [{"title": "港股", "link": "h"}])
    assert nt.fetch_news_feeds("00700.HK", []) == [{"title": "港股", "link": "h"}]

    # 美股 -> RSS源 成功 + 去重
    feed = _fake_feed([("t1", "http://x/1", "2026-01-01", None), ("t2", "http://x/2", "2026-01-01", None)])
    monkeypatch.setattr(nt, "_fetch_bytes", lambda *a, **k: b"<rss/>")
    monkeypatch.setattr(nt.feedparser, "parse", lambda *a, **k: feed)
    monkeypatch.setattr(nt, "_fetch_google_news", lambda *a, **k: [])
    out = nt.fetch_news_feeds("AAPL", ["http://x/feed?s={{symbol}}"], max_items=5)
    assert len(out) == 2

    # RSS 全失败 -> Google 兜底
    monkeypatch.setattr(nt, "_fetch_bytes", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail")))
    monkeypatch.setattr(nt, "_fetch_google_news", lambda *a, **k: [{"title": "兜底", "link": "g"}])
    out = nt.fetch_news_feeds("AAPL", ["http://x/feed?s={{symbol}}"], max_items=5)
    assert out == [{"title": "兜底", "link": "g"}]
