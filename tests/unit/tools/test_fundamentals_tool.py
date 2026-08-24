from src.tools.fundamentals_tool import (
    fetch_income_statement,
    fetch_key_metrics,
    _market,
    _parse_cn_number,
    _fetch_hk_indicators,
)


def test_market_detection():
    assert _market("600519.SHH") == "cn"
    assert _market("002594.SZ") == "cn"
    assert _market("1810") == "hk"
    assert _market("00700") == "hk"
    assert _market("AAPL") == "us"


def test_fetch_hk_indicators_zero_pads_symbol(monkeypatch):
    import pandas as pd

    captured = {}

    def fake_indicator(symbol, indicator):
        captured["symbol"] = symbol
        captured["indicator"] = indicator
        return pd.DataFrame([{"REPORT_DATE": "2025-12-31 00:00:00", "OPERATE_INCOME": 1.0}])

    monkeypatch.setattr("akshare.stock_financial_hk_analysis_indicator_em", fake_indicator)
    rows = _fetch_hk_indicators("1810")
    assert captured["symbol"] == "01810"
    assert captured["indicator"] == "年度"
    assert len(rows) == 1


def test_parse_cn_number():
    assert _parse_cn_number("6.28亿") == 628000000.0
    assert _parse_cn_number("1.5万") == 15000.0
    assert round(_parse_cn_number("54.27%") or 0.0, 6) == 0.5427
    assert _parse_cn_number("False") is None
    assert _parse_cn_number("-") is None


def _cn_rows():
    return [
        {
            "REPORT_DATE": "2026-03-31 00:00:00",
            "REPORT_YEAR": 2026,
            "TOTALOPERATEREVE": 4799534766.23,
            "PARENTNETPROFIT": 86786407.33,
            "EPSXS": 0.026,
            "ROEJQ": 0.48,
            "XSMLL": 11.3241186411,
            "CURRENCY": "CNY",
        },
        {
            "REPORT_DATE": "2025-12-31 00:00:00",
            "REPORT_YEAR": 2025,
            "TOTALOPERATEREVE": 18000000000.0,
            "PARENTNETPROFIT": 3000000000.0,
            "EPSXS": 1.0,
            "ROEJQ": 8.0,
            "XSMLL": 20.0,
            "CURRENCY": "CNY",
        },
    ]


def _us_rows():
    return [
        {
            "REPORT_DATE": "2025-09-27 00:00:00",
            "OPERATE_INCOME": 416161000000,
            "PARENT_HOLDER_NETPROFIT": 93736000000,
            "DILUTED_EPS": 6.29,
            "ROE_AVG": 150.0,
            "GROSS_PROFIT_RATIO": 46.2,
            "CURRENCY": "美元",
        },
        {
            "REPORT_DATE": "2024-09-28 00:00:00",
            "OPERATE_INCOME": 391035000000,
            "PARENT_HOLDER_NETPROFIT": 96995000000,
            "DILUTED_EPS": 6.31,
            "ROE_AVG": 145.0,
            "GROSS_PROFIT_RATIO": 45.9,
            "CURRENCY": "美元",
        },
    ]


def test_fetch_income_statement_cn(monkeypatch):
    monkeypatch.setattr(
        "src.tools.fundamentals_tool._fetch_indicators",
        lambda symbol: _cn_rows(),
    )
    inc = fetch_income_statement("002185.SHZ", limit=1)
    row = inc["income_statement"][0]
    assert row["revenue"] == 4799534766.23
    assert row["netIncome"] == 86786407.33
    assert row["epsDiluted"] == 0.026
    assert row["fiscalYear"] == 2026
    assert row["reportedCurrency"] == "CNY"


def test_fetch_income_statement_us(monkeypatch):
    monkeypatch.setattr(
        "src.tools.fundamentals_tool._fetch_indicators",
        lambda symbol: _us_rows(),
    )
    inc = fetch_income_statement("AAPL", limit=1)
    row = inc["income_statement"][0]
    assert row["revenue"] == 416161000000.0
    assert row["netIncome"] == 93736000000.0
    assert row["epsDiluted"] == 6.29
    assert row["fiscalYear"] == 2025


def test_fetch_key_metrics_cn(monkeypatch):
    monkeypatch.setattr(
        "src.tools.fundamentals_tool._fetch_indicators",
        lambda symbol: _cn_rows(),
    )
    km = fetch_key_metrics("002185.SHZ")
    row = km["key_metrics_ttm"][0]
    assert round(row["returnOnEquityTTM"] or 0.0, 6) == 0.0048
    assert round(row["grossMarginTTM"] or 0.0, 6) == 0.113241


def test_fetch_key_metrics_us(monkeypatch):
    monkeypatch.setattr(
        "src.tools.fundamentals_tool._fetch_indicators",
        lambda symbol: _us_rows(),
    )
    km = fetch_key_metrics("AAPL")
    row = km["key_metrics_ttm"][0]
    assert row["returnOnEquityTTM"] == 1.5
    assert row["grossMarginTTM"] == 0.462


def test_fundamentals_tools_do_not_raise_on_errors(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("数据源接口失败")

    monkeypatch.setattr("src.tools.fundamentals_tool._fetch_indicators", boom)

    inc = fetch_income_statement("AAPL", limit=2)
    km = fetch_key_metrics("AAPL")
    assert isinstance(inc, dict)
    assert isinstance(km, dict)
    assert "__error__" in inc
    assert "__error__" in km
