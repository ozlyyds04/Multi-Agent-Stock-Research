import logging
import os
from logging.handlers import RotatingFileHandler

from src.utils.log_context import get_run_id, get_symbol


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = get_run_id()
        record.symbol = get_symbol()
        return True


def get_logger(name: str):
    """
    创建或返回同时带控制台和轮转文件处理器的已配置日志器。
    日志存储在 logs/app.log（最大 5 MB，3 个备份）。
    """
    log_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "app.log")

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # 避免重复添加处理器

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(level)

    # 控制台处理器
    ch = logging.StreamHandler()
    ch.setLevel(level)

    # 轮转文件处理器
    fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] run=%(run_id)s sym=%(symbol)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    logger.addFilter(ContextFilter())
    ch.addFilter(ContextFilter())
    fh.addFilter(ContextFilter())

    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.propagate = False

    return logger
