from __future__ import annotations
import contextvars

_run_id = contextvars.ContextVar("run_id", default="-")
_symbol = contextvars.ContextVar("symbol", default="-")


def set_log_context(*, run_id: str = "-", symbol: str = "-") -> None:
    _run_id.set(run_id or "-")
    _symbol.set(symbol or "-")


def get_run_id() -> str:
    return _run_id.get()


def get_symbol() -> str:
    return _symbol.get()
