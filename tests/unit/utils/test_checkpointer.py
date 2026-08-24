from langgraph.checkpoint.memory import MemorySaver

from src.utils.checkpointer import get_checkpointer, reset_checkpointer


def test_checkpointer_falls_back_to_memory_when_forced(monkeypatch):
    monkeypatch.setenv("CHECKPOINTER", "memory")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@127.0.0.1:3333/multiagent")
    reset_checkpointer()
    try:
        saver = get_checkpointer()
        assert isinstance(saver, MemorySaver)
    finally:
        reset_checkpointer()
