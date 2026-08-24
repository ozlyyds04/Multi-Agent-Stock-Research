import os

import pytest

from src.tools.pdf_tool import export_report_to_pdf


@pytest.mark.skipif(
    not os.path.exists(r"C:\Windows\Fonts\msyh.ttc"),
    reason="需要 Windows 微软雅黑字体才能渲染中文 PDF",
)
def test_export_report_to_pdf_renders_markdown(tmp_path):
    md = tmp_path / "report.md"
    md.write_text(
        "# 测试报告\n"
        "## 1. 概览\n"
        "- 股票代码：00700\n"
        "1. **加粗文本** 与 [链接](https://example.com)\n"
        "---\n"
        "正文段落。\n",
        encoding="utf-8",
    )
    pdf_path = str(tmp_path / "report.pdf")
    out = export_report_to_pdf(str(md), pdf_path)
    assert out == pdf_path
    assert os.path.getsize(pdf_path) > 0
    with open(pdf_path, "rb") as f:
        assert f.read(5) == b"%PDF-"
