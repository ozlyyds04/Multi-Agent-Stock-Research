from src.guardrails.inputs import normalize_symbol, sanitize_symbol


def test_normalize_hk_codes():
    assert normalize_symbol("1810") == "01810"
    assert normalize_symbol("1810.HK") == "01810"
    assert normalize_symbol("9988") == "09988"
    assert normalize_symbol("0700") == "00700"
    assert normalize_symbol("00700") == "00700"
    assert normalize_symbol("0700.HK") == "00700"
    assert normalize_symbol("00700.HK") == "00700"
    assert normalize_symbol("9988.HK") == "09988"


def test_normalize_cn_codes_unchanged():
    assert normalize_symbol("600519") == "600519.SHH"
    assert normalize_symbol("002185") == "002185.SHZ"


def test_normalize_us_codes_unchanged():
    assert normalize_symbol("AAPL") == "AAPL"
    assert normalize_symbol("BRK.B") == "BRK.B"


def test_sanitize_hk():
    assert sanitize_symbol("0700.HK") == "00700"
