import feedparser
import re
import requests
import calendar
import time as _time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlencode
from jinja2 import Template

from src.utils.logger import get_logger
from src.utils.resilience import RetryConfig, retry_call, RetryableError
from src.tools.hk_tool import is_hk_symbol, to_hk_bare

logger = get_logger(__name__)

_CN_RE = re.compile(r"^\d{6}(\.(SHH|SHZ|SS|SZ))?$", re.IGNORECASE)
_EM_ANNOUNCE_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_HK_COMPANY_CACHE: Dict[str, str] = {}


class NonRetryableHTTPError(RuntimeError):
    """不应重试的 HTTP 错误（例如 404/401/403）。"""
    pass


def _bare_symbol(symbol: str) -> str:
    return symbol.strip().upper().split(".")[0]


def _fetch_em_announcements(symbol: str, max_items: int = 6) -> List[Dict]:
    """
    通过东方财富公告接口获取 A 股个股公告，作为新闻头条来源。
    """
    bare = _bare_symbol(symbol)
    try:
        r = requests.get(
            _EM_ANNOUNCE_URL,
            params={
                "sr": -1,
                "page_size": max_items,
                "page_index": 1,
                "ann_type": "A",
                "client_source": "web",
                "stock_list": bare,
            },
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        items = r.json().get("data", {}).get("list", []) or []
        out = []
        for it in items:
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            code = str(it.get("art_code") or "")
            link = f"https://data.eastmoney.com/notices/detail/{bare}/{code}.html" if code else ""
            out.append({
                "title": title,
                "link": link,
                "published": str(it.get("display_time") or it.get("notice_date") or "")[:19],
                "summary": "",
                "source": "东方财富公告",
            })
        return out[:max_items]
    except Exception as e:
        logger.warning("获取 %s 的东方财富公告失败：%s", bare, e)
        return []


def _fetch_bytes(
        url: str,
        *,
        timeout: Tuple[float, float] = (3.05, 12.0),
        retry_cfg: Optional[RetryConfig] = None,
) -> bytes:
    cfg = retry_cfg or RetryConfig()

    def _do():
        r = requests.get(url, timeout=timeout)
        # 429 是配额/频率限制，短时间重试无意义，快速失败由调用方处理
        if r.status_code == 429:
            raise NonRetryableHTTPError("HTTP 状态码 429（限流）")
        # 重试瞬时状态码
        if r.status_code in cfg.retry_statuses:
            raise RetryableError(f"瞬时 HTTP 状态码 {r.status_code}")
        # 非瞬时错误 -> 快速失败（由调用方处理）
        if r.status_code >= 400:
            raise NonRetryableHTTPError(f"HTTP 状态码 {r.status_code}")
        return r.content

    return retry_call(
        _do,
        cfg=cfg,
        op_name="rss_http_get",
        logger=logger,
        retry_exceptions=(requests.RequestException, TimeoutError, OSError),
    )


def _fetch_hk_company_name(bare: str) -> str:
    """通过东方财富港股 F10 获取公司名称（用于新闻搜索），带进程内缓存。"""
    if bare in _HK_COMPANY_CACHE:
        return _HK_COMPANY_CACHE[bare]
    try:
        import akshare as ak

        df = ak.stock_hk_company_profile_em(symbol=bare)
        if df is not None and not getattr(df, "empty", True) and "公司名称" in df.columns:
            name = str(df.iloc[0]["公司名称"]).strip()
            name = re.sub(r"(控股|集团)?有限公司$", "", name).strip()
            name = re.sub(r"集团$", "", name).strip()
            _HK_COMPANY_CACHE[bare] = name
            return name
    except Exception as e:
        logger.warning("获取 %s 的港股公司名称失败：%s", bare, e)
    _HK_COMPANY_CACHE[bare] = ""
    return ""


def _fetch_google_news(
        query: str,
        max_items: int = 6,
        *,
        hl: str = "en-US",
        gl: str = "US",
        ceid: str = "US:en",
) -> List[Dict]:
    """用关键词检索 Google News RSS，作为免费新闻源的稳定兜底。"""
    url = "https://news.google.com/rss/search?" + urlencode({
        "q": query,
        "hl": hl,
        "gl": gl,
        "ceid": ceid,
    })
    try:
        content = _fetch_bytes(url, retry_cfg=RetryConfig(max_retries=1, base_delay_sec=0.5, max_delay_sec=1.0))
        feed = feedparser.parse(content)
        items = []
        for e in feed.entries[:50]:
            pub_dt = None
            pp = getattr(e, "published_parsed", None)
            if pp:
                try:
                    pub_dt = datetime.fromtimestamp(calendar.timegm(pp), tz=timezone.utc)
                except Exception:
                    pub_dt = None
            items.append({
                "title": getattr(e, "title", ""),
                "link": getattr(e, "link", ""),
                "published": getattr(e, "published", ""),
                "summary": getattr(e, "summary", ""),
                "source": "Google 新闻",
                "_dt": pub_dt,
            })

        # 优先返回近期新闻：近 90 天内有足够条目时过滤旧闻，否则全部保留并新→旧排序
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        recent = [it for it in items if it["_dt"] is not None and it["_dt"] >= cutoff]
        threshold = max(1, max_items // 2)
        pool = recent if len(recent) >= threshold else items
        pool = sorted(
            pool,
            key=lambda it: it["_dt"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:max_items]
        out = [{k: v for k, v in it.items() if k != "_dt"} for it in pool]
        logger.info("已从 Google News 获取 %s 的新闻：%d 条（近 90 天内 %d 条）", query, len(out), len(recent))
        return out
    except Exception as e:
        logger.warning("获取 %s 的 Google News 新闻失败：%s", query, e)
        return []


def _fetch_hk_news(symbol: str, max_items: int = 6) -> List[Dict]:
    """
    获取港股新闻：用公司名称 + 港股 关键词检索 Google News RSS。
    Yahoo/东财公告不覆盖港股，且 Google News 对 429 更稳定。
    """
    bare = to_hk_bare(symbol)
    name = _fetch_hk_company_name(bare)
    query = f"{name} 港股" if name else f"{bare} 港股"
    return _fetch_google_news(query, max_items, hl="zh-CN", gl="CN", ceid="CN:zh-Hans")


def fetch_news_feeds(
        symbol: str,
        rss_templates: List[str],
        max_items: int = 6,
        timeout: Tuple[float, float] = (3.05, 12.0),
        retry_cfg: Optional[RetryConfig] = None,
) -> List[Dict]:
    # A 股：使用东方财富公告接口（RSS 源均为美股源）
    if _CN_RE.match((symbol or "").strip().upper()):
        items = _fetch_em_announcements(symbol, max_items)
        if not items:
            # 东财公告不可用时，用 Google News 中文检索兜底
            items = _fetch_google_news(
                f"{_bare_symbol(symbol)} 股票",
                max_items,
                hl="zh-CN",
                gl="CN",
                ceid="CN:zh-Hans",
            )
        logger.info("%s 的公告条数：%d", symbol, len(items))
        return items

    # 港股：Yahoo RSS 常限流、Benzinga 无港股源，改用 Google News 检索公司名
    if is_hk_symbol(symbol):
        items = _fetch_hk_news(symbol, max_items)
        logger.info("%s 的港股新闻条数：%d", symbol, len(items))
        return items

    logger.debug("正在从 %d 个源获取 %s 的新闻", len(rss_templates), symbol)
    items: List[Dict] = []
    cfg = retry_cfg or RetryConfig()

    for tmpl in rss_templates:
        url = Template(tmpl).render(symbol=symbol)
        try:
            content = _fetch_bytes(url, timeout=timeout, retry_cfg=cfg)
            feed = feedparser.parse(content)

            for e in feed.entries[:max_items]:
                items.append({
                    "title": getattr(e, "title", ""),
                    "link": getattr(e, "link", ""),
                    "published": getattr(e, "published", ""),
                    "summary": getattr(e, "summary", ""),
                })

            logger.debug("已从 %s 获取 %d 条新闻", url, min(len(feed.entries), max_items))

        except Exception as e:
            logger.warning("获取 %s 的新闻失败（股票代码=%s）：%s", url, symbol, e)
            continue

    if not items:
        # RSS 源全部失败（Yahoo 429 / Benzinga 404 等）时，用 Google News 兜底
        items = _fetch_google_news(f"{symbol} stock", max_items)

    dedup: Dict[str, Dict] = {}
    for it in items:
        if it.get("link") and it["link"] not in dedup:
            dedup[it["link"]] = it

    final = list(dedup.values())[:max_items]
    logger.info("%s 的新闻总条数：%d", symbol, len(final))
    return final
