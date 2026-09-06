"""覆盖 fundamentals_tool.py 的解析/行构建/fetch 分支（mock 数据源）。"""

from src.tools import fundamentals_tool as ft


def test_bare_symbol_and_market():
    assert ft._bare_symbol("600519.SHH") == "600519"
    assert ft._market("600519") == "cn"
    assert ft._market("00700.HK") == "hk"
    assert ft._market("AAPL") == "us"


def test_num_pct_and_cn_number():
    assert ft._num(None) is None
    assert ft._num(False) is None
    assert ft._num("nan") is None
    assert ft._num("12.5") == 12.5
    assert ft._pct(27.13) == 0.2713
    assert ft._pct(None) is None

    assert ft._parse_cn_number("6.28亿") == 6.28e8
    assert ft._parse_cn_number("3.5万") == 35000.0
    assert ft._parse_cn_number("15.5%") == 0.155
    assert ft._parse_cn_number("-") is None
    assert ft._parse_cn_number("False") is None


def test_parse_cn_ratio():
    assert ft._parse_cn_ratio("15.5") == 0.155
    assert ft._parse_cn_ratio("15.5%") == 0.155
    assert ft._parse_cn_ratio(None) is None


def test_fiscal_year_and_sort():
    assert ft._fiscal_year({"REPORT_DATE": "2025-06-30"}) == 2025
    assert ft._fiscal_year({"报告期": "2024-12-31"}) == 2024
    assert ft._fiscal_year({}) is None
    rows = [{"REPORT_DATE": "2024"}, {"REPORT_DATE": "2025"}]
    assert ft._sort_newest_first(rows)[0]["REPORT_DATE"] == "2025"


def test_income_rows_cn_hk_us(monkeypatch):
    cn = [
        {"REPORT_DATE": "2025-06-30", "TOTALOPERATEREVE": 1000, "PARENTNETPROFIT": 200, "EPSXS": 5, "CURRENCY": "CNY"}
    ]
    hk = [{"REPORT_DATE": "2025-12-31", "OPERATE_INCOME": 900, "HOLDER_PROFIT": 150, "DILUTED_EPS": 2}]
    us = [{"REPORT_DATE": "2025-09-30", "OPERATE_INCOME": 800, "PARENT_HOLDER_NETPROFIT": 120, "DILUTED_EPS": 3}]
    monkeypatch.setattr(ft, "_fetch_indicators", lambda s: cn if s == "600519" else (hk if s == "00700" else us))

    assert ft._income_rows("600519", 5)[0]["revenue"] == 1000
    assert ft._income_rows("00700", 5)[0]["reportedCurrency"] == "HKD"
    assert ft._income_rows("AAPL", 5)[0]["reportedCurrency"] == "USD"


def test_metric_rows_cn_hk_us(monkeypatch):
    cn = [{"REPORT_DATE": "2025-06-30", "ROEJQ": 31, "XSMLL": 40}]
    hk = [{"REPORT_DATE": "2025-12-31", "ROE_AVG": 20, "GROSS_PROFIT_RATIO": 30}]
    us = [{"REPORT_DATE": "2025-09-30", "ROE_AVG": 25, "GROSS_PROFIT_RATIO": 35}]
    table = {"600519": cn, "00700": hk, "AAPL": us, "EMPTY": []}
    monkeypatch.setattr(ft, "_fetch_indicators", lambda s: table.get(s, us))

    assert ft._metric_rows("600519")[0]["returnOnEquityTTM"] == 0.31
    assert ft._metric_rows("600519")[0]["grossMarginTTM"] == 0.40
    assert ft._metric_rows("00700")[0]["currency"] == "HKD"
    assert ft._metric_rows("AAPL")[0]["currency"] == "USD"
    assert ft._metric_rows("EMPTY") == []


def test_error_dict_status_parsing():
    class _Resp:
        status_code = 402

    class _Exc:
        response = _Resp()

    assert ft._error_dict("AAPL", "income_statement", _Exc())["__error__"]["status"] == 402
    assert ft._error_dict("AAPL", "income_statement", ValueError("HTTP 429"))["__error__"]["status"] == 429
    assert ft._error_dict("AAPL", "income_statement", ValueError("boom"))["__error__"]["status"] is None


def test_fetch_income_and_metrics_success_and_error(monkeypatch):
    monkeypatch.setattr(
        ft,
        "_fetch_indicators",
        lambda s: [
            {
                "REPORT_DATE": "2025-06-30",
                "TOTALOPERATEREVE": 1000,
                "PARENTNETPROFIT": 200,
                "EPSXS": 5,
                "CURRENCY": "CNY",
            }
        ],
    )
    out = ft.fetch_income_statement("600519")
    assert out["income_statement"][0]["revenue"] == 1000

    def _boom(s):
        raise RuntimeError("HTTP 429")

    monkeypatch.setattr(ft, "_fetch_indicators", _boom)
    assert ft.fetch_income_statement("AAPL")["__error__"]["status"] == 429
    assert ft.fetch_key_metrics("AAPL")["__error__"]["status"] == 429


def test_fetch_cn_indicators_and_fallback(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": {"data": [{"REPORT_DATE": "2025-06-30"}]}}

    monkeypatch.setattr(ft, "check_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(ft.requests, "get", lambda *a, **k: _Resp())
    assert ft._fetch_cn_indicators("600519")[0]["REPORT_DATE"] == "2025-06-30"

    # 东财 A 股失败 -> 回退同花顺备用源
    def _boom(*a, **k):
        raise RuntimeError("fail")

    monkeypatch.setattr(ft, "_fetch_cn_indicators", _boom)
    monkeypatch.setattr(ft, "_fetch_ths_indicators", lambda bare: [{"报告期": "2025-06-30"}])
    rows = ft._fetch_indicators("600519")
    assert rows and rows[0]["报告期"] == "2025-06-30"
