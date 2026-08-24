from src.utils.helper import is_rate_limit_error


def test_rate_limit_detection():
    assert is_rate_limit_error(RuntimeError("HTTP 429 Too Many Requests")) is True
    assert is_rate_limit_error(RuntimeError("rate limit")) is True
    assert is_rate_limit_error(ValueError("Alpha Vantage: 25 requests per day")) is True
    assert is_rate_limit_error(RuntimeError("connection reset")) is False
