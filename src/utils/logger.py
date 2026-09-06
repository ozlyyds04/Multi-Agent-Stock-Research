import logging
import os
import json
import threading
from logging.handlers import RotatingFileHandler

from src.utils.log_context import get_run_id, get_symbol

_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs"
)
_LOG_FILE = os.path.join(_LOG_DIR, "app.log")

# 每个模块的 logger 若各自挂一个 RotatingFileHandler 写同一个文件，
# Windows 上任一 handler 触发轮转（rename）时其他 handler 仍持有句柄，
# 会抛 PermissionError。改为：handler 只挂在 root logger 上挂一次，
# 各模块 logger propagate 到 root。
_configured = False
_config_lock = threading.Lock()


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = get_run_id()
        record.symbol = get_symbol()
        return True


class JsonFormatter(logging.Formatter):
    """把日志记录序列化为 JSON（结构化日志，供 ELK/Loki 等采集）。"""

    _EXTRA_FIELDS = (
        "event",
        "node",
        "latency",
        "status",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "cost",
        "duration",
        "source",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", "-"),
            "symbol": getattr(record, "symbol", "-"),
        }
        for field in self._EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, ensure_ascii=False)


def log_structured(logger, event: str, level: int = logging.INFO, **fields):
    """
    记录一条结构化事件：控制台显示可读文本，文件写入 JSON 并带 event + 各字段。
    """
    msg = f"[{event}] " + " ".join(f"{k}={v}" for k, v in fields.items())
    logger.log(level, msg, extra={"event": event, **fields})


def _configure_root() -> None:
    global _configured
    with _config_lock:
        if _configured:
            return
        os.makedirs(_LOG_DIR, exist_ok=True)
        level = os.getenv("LOG_LEVEL", "INFO").upper()

        root = logging.getLogger()
        root.setLevel(level)

        ch = logging.StreamHandler()
        ch.setLevel(level)

        fh = RotatingFileHandler(_LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setLevel(level)

        text_formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] run=%(run_id)s sym=%(symbol)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        ch.addFilter(ContextFilter())
        fh.addFilter(ContextFilter())

        # 控制台保持人类可读；文件默认写结构化 JSON（可用 LOG_FORMAT=text 改回纯文本）
        ch.setFormatter(text_formatter)
        if os.getenv("LOG_FORMAT", "json").lower() == "text":
            fh.setFormatter(text_formatter)
        else:
            fh.setFormatter(JsonFormatter())

        root.addHandler(ch)
        root.addHandler(fh)
        _configured = True


def get_logger(name: str) -> logging.Logger:
    """
    返回模块 logger。文件/控制台 handler 由 root logger 统一持有（只挂一次），
    模块 logger 通过 propagate 汇聚到 root，避免多 handler 争抢同一日志文件。
    """
    _configure_root()
    return logging.getLogger(name)
