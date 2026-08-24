from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from fpdf import FPDF

from src.utils.logger import get_logger

logger = get_logger(__name__)

PAGE_W_MM = 210.0
PAGE_H_MM = 297.0
MARGIN_MM = 20.0
CONTENT_W_MM = PAGE_W_MM - 2 * MARGIN_MM

_FONT_CANDIDATES = [
    ("msyh", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),      # 微软雅黑
    ("simhei", r"C:\Windows\Fonts\simhei.ttf", None),                            # 黑体
    ("simsun", r"C:\Windows\Fonts\simsun.ttc", None),                            # 宋体
    ("noto", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", None),
    ("pingfang", "/System/Library/Fonts/PingFang.ttc", None),
]

_IMG_HTML_RE = re.compile(r'<img[^>]*src="([^"]+)"[^>]*>', re.IGNORECASE)
_CAPTION_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


class _ReportPDF(FPDF):
    def __init__(self, font_family: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.font_family = font_family
        self.set_auto_page_break(auto=True, margin=18.0)
        self.set_margins(MARGIN_MM, MARGIN_MM, MARGIN_MM)
        self.add_font(font_family, "", _font_regular())
        bold = _font_bold()
        if bold:
            self.add_font(font_family, "B", bold)

    def footer(self):
        self.set_y(-14)
        self.set_font(self.font_family, "", 9)
        self.set_text_color(140, 140, 140)
        self.cell(0, 10, f"第 {self.page_no()} 页", align="C")
        self.set_text_color(0, 0, 0)


_REGULAR_PATH: Optional[str] = None
_BOLD_PATH: Optional[str] = None


def _discover_fonts() -> Tuple[Optional[str], Optional[str]]:
    global _REGULAR_PATH, _BOLD_PATH
    if _REGULAR_PATH:
        return _REGULAR_PATH, _BOLD_PATH
    for _family, reg, bold in _FONT_CANDIDATES:
        if os.path.exists(reg):
            _REGULAR_PATH = reg
            _BOLD_PATH = bold if bold and os.path.exists(bold) else None
            return _REGULAR_PATH, _BOLD_PATH
    return None, None


def _font_regular() -> str:
    reg, _ = _discover_fonts()
    if not reg:
        raise RuntimeError("未找到可用的中文字体（C:\\Windows\\Fonts\\msyh.ttc 等）")
    return reg


def _font_bold() -> Optional[str]:
    _, bold = _discover_fonts()
    return bold


def _split_blocks(md: str) -> List[Tuple[str, str]]:
    """
    把 Markdown 拆成 (类型, 内容) 块：
    h1/h2/h3 / bullet / num / para / image / hr / skip
    """
    blocks: List[Tuple[str, str]] = []
    for raw_line in md.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # 图片：HTML <img> 块或 Markdown ![alt](path)
        img_match = _IMG_HTML_RE.search(line)
        md_img = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line)
        if img_match or md_img:
            src = img_match.group(1) if img_match else md_img.group(2)
            cap = ""
            cap_m = _CAPTION_RE.search(line)
            if cap_m:
                cap = _HTML_TAG_RE.sub("", cap_m.group(1)).strip()
            elif md_img:
                cap = md_img.group(1)
            blocks.append(("image", f"{src}|{cap}"))
            continue

        if line == "---":
            continue
        if line.startswith("####"):
            blocks.append(("h4", line[4:].strip()))
        elif line.startswith("###"):
            blocks.append(("h3", line[3:].strip()))
        elif line.startswith("##"):
            blocks.append(("h2", line[2:].strip()))
        elif line.startswith("#"):
            blocks.append(("h1", line[1:].strip()))
        elif re.match(r"^\d+\.\s", line):
            blocks.append(("num", re.sub(r"^\d+\.\s*", "", line)))
        elif line.startswith("- ") or line.startswith("* "):
            blocks.append(("bullet", line[2:].strip()))
        else:
            blocks.append(("para", _HTML_TAG_RE.sub("", line)))
    return blocks


def _rich_segments(text: str):
    """
    把行内 **加粗** 和 [链接](url) 拆成 (样式, 文本, url) 片段。
    """
    pos = 0
    for m in re.finditer(r"\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)]+)\)", text):
        if m.start() > pos:
            yield ("normal", text[pos:m.start()], None)
        if m.group(1) is not None:
            yield ("bold", m.group(1), None)
        else:
            yield ("link", m.group(2), m.group(3))
        pos = m.end()
    if pos < len(text):
        yield ("normal", text[pos:], None)


def _render_rich(pdf: _ReportPDF, text: str, size: float, line_h: float, indent: float = 0.0):
    pdf.set_x(MARGIN_MM + indent)
    for style, seg, url in _rich_segments(text):
        if style == "bold":
            pdf.set_font(pdf.font_family, "B", size)
            pdf.set_text_color(20, 20, 30)
        elif style == "link":
            pdf.set_font(pdf.font_family, "", size)
            pdf.set_text_color(0, 90, 200)
        else:
            pdf.set_font(pdf.font_family, "", size)
            pdf.set_text_color(20, 20, 20)
        pdf.write(line_h, seg, link=url)
    pdf.ln(line_h)
    pdf.set_text_color(20, 20, 20)


def export_report_to_pdf(md_path: str, pdf_path: str) -> str:
    """
    用 fpdf2 把 Markdown 研究报告渲染为 PDF（纯 Python，无需 pandoc/wkhtmltopdf）。
    支持标题、列表、加粗、链接、价格图表嵌入和页脚页码。
    """
    with open(md_path, "r", encoding="utf-8") as f:
        md = f.read()

    pdf = _ReportPDF("CJK")
    pdf.add_page()

    for kind, content in _split_blocks(md):
        if kind == "h1":
            pdf.set_font(pdf.font_family, "B", 20)
            pdf.set_text_color(26, 26, 26)
            pdf.multi_cell(0, 10, content)
            pdf.ln(2)
        elif kind == "h2":
            pdf.ln(2)
            pdf.set_font(pdf.font_family, "B", 15)
            pdf.set_text_color(26, 26, 26)
            pdf.multi_cell(0, 9, content)
            pdf.ln(1)
        elif kind in ("h3", "h4"):
            pdf.set_font(pdf.font_family, "B", 13 if kind == "h3" else 12)
            pdf.set_text_color(40, 40, 40)
            pdf.multi_cell(0, 8, content)
            pdf.ln(1)
        elif kind == "bullet":
            _render_rich(pdf, content, 11, 6.5, indent=6)
        elif kind == "num":
            _render_rich(pdf, content, 11, 6.5, indent=6)
        elif kind == "para":
            _render_rich(pdf, content, 11, 6.5)
        elif kind == "image":
            src, caption = content.split("|", 1)
            src = src.strip()
            if os.path.exists(src):
                pdf.ln(3)
                w = min(CONTENT_W_MM, 130.0)
                pdf.image(src, x=(PAGE_W_MM - w) / 2, w=w)
                if caption:
                    pdf.set_font(pdf.font_family, "B", 10)
                    pdf.set_text_color(90, 90, 90)
                    pdf.cell(0, 8, caption, align="C")
                    pdf.ln(3)
                pdf.set_text_color(20, 20, 20)
            else:
                logger.warning("PDF 图表文件不存在，跳过：%s", src)
        pdf.set_text_color(20, 20, 20)

    out_dir = os.path.dirname(pdf_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    pdf.output(pdf_path)
    logger.info("PDF 已生成：%s", pdf_path)
    return pdf_path
