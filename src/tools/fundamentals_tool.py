from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import requests

from src.tools.hk_tool import to_hk_bare
from src.utils.logger import get_logger

logger = get_logger(__name__)

_CN_RE = re.compile(r"^\d{6}(\.(SHH|SHZ|SS|SZ))?$", re.IGNORECASE)
_HK_RE = re.compile(r"^\d{4,5}(\.HK)?$", re.IGNORECASE)
_EM_V1_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://emweb.securities.eastmoney.com/",
}


def _bare_symbol(symbol: str) -> str:
    """去掉交易所后缀，得到裸代码（如 600519.SHH -> 600519）。"""
    return symbol.strip().upper().split(".")[0]


def _market(symbol: str) -> str:
    s = symbol.strip().upper()
    if _CN_RE.match(s):
        return "cn"
    if _HK_RE.match(s):
        return "hk"
    return "us"


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or v is False or str(v).lower() in ("false", "nan", ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(v: Optional[float]) -> Optional[float]:
    """东财百分比数值（27.13 表示 27.13%）转为小数（0.2713）。"""
    return None if v is None else v / 100.0


def _parse_cn_number(v: Any) -> Optional[float]:
    """解析同花顺中文格式化数值：'6.28亿' -> 6.28e8；'False'/'-' -> None。"""
    if v is None or v is False:
        return None
    s = str(v).strip().replace(",", "")
    if s.lower() == "false" or s in ("", "-", "--"):
        return None
    mult = 1.0
    if s.endswith("亿"):
        mult, s = 1e8, s[:-1]
    elif s.endswith("万"):
        mult, s = 1e4, s[:-1]
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return None
    try:
        return float(s) * mult
    except ValueError:
        return None


def _fiscal_year(row: Dict[str, Any]) -> Optional[int]:
    for key in ("REPORT_YEAR", "REPORT_DATE", "报告期"):
        v = row.get(key)
        if v:
            m = re.search(r"(\d{4})", str(v))
            if m:
                return int(m.group(1))
    return None


def _sort_newest_first(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: str(r.get("REPORT_DATE") or r.get("报告期") or ""),
        reverse=True,
    )


def _fetch_cn_indicators(bare: str) -> List[Dict[str, Any]]:
    """东方财富 v1 接口：A 股财务主要指标（最新在前）。"""
    market_code = "SH" if bare[0] in ("6", "9") else ("BJ" if bare[0] in ("4", "8") else "SZ")
    secucode = f"{bare}.{market_code}"
    params = {
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": "ALL",
        "quoteColumns": "",
        "filter": f'(SECUCODE="{secucode}")',
        "pageNumber": "1",
        "pageSize": "10",
        "sortTypes": "-1",
        "sortColumns": "REPORT_DATE",
        "source": "HSF10",
        "client": "PC",
    }
    r = requests.get(_EM_V1_URL, params=params, headers=_EM_HEADERS, timeout=20)
    r.raise_for_status()
    data = (r.json().get("result") or {}).get("data") or []
    return _sort_newest_first(list(data))


def _fetch_ths_indicators(bare: str) -> List[Dict[str, Any]]:
    """同花顺接口（东财不可用时的备用源）。"""
    import akshare as ak

    df = ak.stock_financial_abstract_ths(symbol=bare, indicator="按报告期")
    if df is None or getattr(df, "empty", True):
        return []
    return _sort_newest_first(df.to_dict("records"))


def _fetch_hk_indicators(bare: str) -> List[Dict[str, Any]]:
    import akshare as ak

    # AKShare 港股财务接口需要 5 位补零代码（1810 -> 01810）
    symbol5 = to_hk_bare(bare)
    df = ak.stock_financial_hk_analysis_indicator_em(symbol=symbol5, indicator="年度")
    if df is None or getattr(df, "empty", True):
        return []
    return _sort_newest_first(df.to_dict("records"))


def _fetch_us_indicators(bare: str) -> List[Dict[str, Any]]:
    import akshare as ak

    df = ak.stock_financial_us_analysis_indicator_em(symbol=bare, indicator="年报")
    if df is None or getattr(df, "empty", True):
        return []
    return _sort_newest_first(df.to_dict("records"))


def _fetch_indicators(symbol: str) -> List[Dict[str, Any]]:
    """按市场获取财务指标行（最新在前）。"""
    market = _market(symbol)
    bare = _bare_symbol(symbol)
    if market == "cn":
        try:
            return _fetch_cn_indicators(bare)
        except Exception as e:
            logger.warning("东方财富 A 股指标获取失败，改用同花顺备用源：%s", e)
            return _fetch_ths_indicators(bare)
    if market == "hk":
        return _fetch_hk_indicators(bare)
    return _fetch_us_indicators(bare)


def _income_rows(symbol: str, limit: int) -> List[Dict[str, Any]]:
    rows = _fetch_indicators(symbol)
    out = []
    for row in rows[:limit]:
        market = _market(symbol)
        fy = _fiscal_year(row)
        if market == "cn":
            out.append({
                "date": row.get("REPORT_DATE") or row.get("报告期"),
                "fiscalYear": fy,
                "revenue": _num(row.get("TOTALOPERATEREVE")) or _parse_cn_number(row.get("营业总收入")),
                "netIncome": _num(row.get("PARENTNETPROFIT")) or _parse_cn_number(row.get("净利润")),
                "epsDiluted": _num(row.get("EPSXS")) or _parse_cn_number(row.get("基本每股收益")),
                "reportedCurrency": row.get("CURRENCY") or "CNY",
            })
        elif market == "hk":
            out.append({
                "date": row.get("REPORT_DATE"),
                "fiscalYear": fy,
                "revenue": _num(row.get("OPERATE_INCOME")),
                "netIncome": _num(row.get("HOLDER_PROFIT")),
                "epsDiluted": _num(row.get("DILUTED_EPS")),
                "reportedCurrency": row.get("CURRENCY") or "HKD",
            })
        else:
            out.append({
                "date": row.get("REPORT_DATE"),
                "fiscalYear": fy,
                "revenue": _num(row.get("OPERATE_INCOME")),
                "netIncome": _num(row.get("PARENT_HOLDER_NETPROFIT")),
                "epsDiluted": _num(row.get("DILUTED_EPS")),
                "reportedCurrency": row.get("CURRENCY") or "USD",
            })
    return out


def _metric_rows(symbol: str) -> List[Dict[str, Any]]:
    rows = _fetch_indicators(symbol)
    if not rows:
        return []
    row = rows[0]
    market = _market(symbol)
    if market == "cn":
        return [{
            "returnOnEquityTTM": _pct(_num(row.get("ROEJQ"))) or _parse_cn_number(row.get("净资产收益率")),
            "grossMarginTTM": _pct(_num(row.get("XSMLL"))) or _parse_cn_number(row.get("销售毛利率")),
            "date": row.get("REPORT_DATE") or row.get("报告期"),
            "currency": row.get("CURRENCY") or "CNY",
        }]
    if market == "hk":
        return [{
            "returnOnEquityTTM": _pct(_num(row.get("ROE_AVG"))),
            "grossMarginTTM": _pct(_num(row.get("GROSS_PROFIT_RATIO"))),
            "date": row.get("REPORT_DATE"),
            "currency": row.get("CURRENCY") or "HKD",
        }]
    return [{
        "returnOnEquityTTM": _pct(_num(row.get("ROE_AVG"))),
        "grossMarginTTM": _pct(_num(row.get("GROSS_PROFIT_RATIO"))),
        "date": row.get("REPORT_DATE"),
        "currency": row.get("CURRENCY") or "USD",
    }]


def _error_dict(symbol: str, where: str, exc: Exception) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        where: [],
        "__error__": {
            "where": where,
            "status": None,
            "message": str(exc),
        },
    }


def fetch_income_statement(symbol: str, api_key: Optional[str] = None, limit: int = 2) -> Dict[str, Any]:
    """获取利润表摘要（营收/净利润/每股收益），A 股/美股/港股通用。"""
    try:
        rows = _income_rows(symbol, limit)
        logger.info("已获取 %s 的利润表摘要（%d 条）", symbol, len(rows))
        return {"symbol": symbol, "income_statement": rows}
    except Exception as e:
        logger.error("获取 %s 的利润表摘要时出错：%s", symbol, e)
        return _error_dict(symbol, "income_statement", e)


def fetch_key_metrics(symbol: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """获取最新一期关键指标（ROE、毛利率等）。"""
    try:
        rows = _metric_rows(symbol)
        logger.info("已获取 %s 的关键指标", symbol)
        return {"symbol": symbol, "key_metrics_ttm": rows}
    except Exception as e:
        logger.error("获取 %s 的关键指标时出错：%s", symbol, e)
        return _error_dict(symbol, "key_metrics_ttm", e)
