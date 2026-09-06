import os
import json
from datetime import datetime, timezone
from jinja2 import Template
from src.utils.logger import get_logger

logger = get_logger(__name__)


def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)
    logger.debug("已确保目录存在：%s", p)


def _safe_filename(filename: str) -> str:
    """库层面兜底：调用方传入的文件名不允许携带路径分隔符。"""
    base = os.path.basename(str(filename))
    if base != filename or base in ("", ".", ".."):
        raise ValueError(f"非法的文件名：{filename!r}")
    return base


def save_json(obj, outdir: str, filename: str):
    ensure_dir(outdir)
    path = os.path.join(outdir, _safe_filename(filename))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    logger.info("JSON 已保存：%s", path)
    return path


def save_markdown(content: str, outdir: str, filename: str):
    ensure_dir(outdir)
    path = os.path.join(outdir, _safe_filename(filename))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("Markdown 已保存：%s", path)
    return path


def render_filename(template: str, symbol: str, date: str | None = None):
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fname = Template(template).render(symbol=symbol, date=date)
    logger.debug("已渲染文件名：%s", fname)
    return fname
