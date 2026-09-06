import pytest
import time
import concurrent.futures as cf

from src.utils.resilience import RetryConfig, RetryableError, retry_call


def test_retry_call_success_no_retry(monkeypatch):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    cfg = RetryConfig(max_retries=3, base_delay_sec=0.01, max_delay_sec=0.01, timeout_sec=None)

    # Avoid sleep delays
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    out = retry_call(fn, cfg=cfg, op_name="t", logger=_DummyLogger())
    assert out == "ok"
    assert calls["n"] == 1


def test_retry_call_retry_then_success(monkeypatch):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableError("transient")
        return "ok"

    cfg = RetryConfig(max_retries=5, base_delay_sec=0.01, max_delay_sec=0.01, timeout_sec=None)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    out = retry_call(fn, cfg=cfg, op_name="t", logger=_DummyLogger())
    assert out == "ok"
    assert calls["n"] == 3


def test_retry_call_exhausted(monkeypatch):
    def fn():
        raise RetryableError("always")

    cfg = RetryConfig(max_retries=2, base_delay_sec=0.01, max_delay_sec=0.01, timeout_sec=None)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    with pytest.raises(RetryableError):
        retry_call(fn, cfg=cfg, op_name="t", logger=_DummyLogger())


def test_retry_call_timeout_becomes_retryable(monkeypatch):
    # Uses cfg.timeout_sec -> _call_with_timeout -> can raise RetryableError on timeout (per your current code)
    def fn():
        time.sleep(0.2)
        return "late"

    cfg = RetryConfig(max_retries=0, timeout_sec=0.01, base_delay_sec=0.01, max_delay_sec=0.01)
    monkeypatch.setattr(time, "sleep", lambda *_: None)  # do not actually sleep long

    # We need to force cf.TimeoutError behavior; easiest is to patch ThreadPoolExecutor/future
    class FakeFuture:
        def result(self, timeout=None):
            raise cf.TimeoutError()

    class FakeExecutor:
        def submit(self, *_args, **_kwargs): return FakeFuture()
        def shutdown(self, *args, **kwargs): pass

    import src.utils.resilience as res
    monkeypatch.setattr(res.cf, "ThreadPoolExecutor", lambda **kwargs: FakeExecutor())

    with pytest.raises(RetryableError):
        retry_call(fn, cfg=cfg, op_name="t", logger=_DummyLogger())


class _DummyLogger:
    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass
