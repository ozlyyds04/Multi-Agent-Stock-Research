import io
import os
import json
import zipfile
from pathlib import Path
from typing import Optional, Dict, Any

import requests
import streamlit as st

# -----------------------------
# 配置
# -----------------------------
DEFAULT_API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")
ANALYZE_ENDPOINT = f"{DEFAULT_API_URL}/analyze"
HEALTH_ENDPOINT = f"{DEFAULT_API_URL}/health"


# -----------------------------
# 辅助函数
# -----------------------------
def _validate_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        raise ValueError("请输入股票代码（例如 AAPL）。")
    if not s.replace("-", "").replace(".", "").isalnum():
        raise ValueError("股票代码包含无效字符。只能使用字母/数字、'.' 或 '-'。")
    if len(s) > 12:
        raise ValueError("股票代码过长，请核实代码。")
    return s


def _validate_days(days: int) -> int:
    if days < 5 or days > 10:
        raise ValueError("天数必须在 5 到 10 之间。")
    return int(days)


def _call_api(symbol: str, days: int, human: bool = False) -> Dict[str, Any]:
    payload = {"symbol": symbol, "days": days, "human": human}
    r = requests.post(ANALYZE_ENDPOINT, json=payload)

    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"后端返回了非 JSON 响应（HTTP {r.status_code}）。")

    if r.status_code >= 400 or data.get("status") == "error":
        reason = data.get("reason") or f"请求失败（HTTP {r.status_code}）。"
        suggested = data.get("suggested_action")
        msg = reason + (f"\n\n建议操作：{suggested}" if suggested else "")
        raise RuntimeError(msg)

    return data


def _call_approve(run_id: str, action: str, text: str) -> Dict[str, Any]:
    payload = {"run_id": run_id, "action": action, "comment": text, "edited_text": text}
    r = requests.post(f"{DEFAULT_API_URL}/analyze/approve", json=payload)

    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"后端返回了非 JSON 响应（HTTP {r.status_code}）。")

    if r.status_code >= 400 or data.get("status") == "error":
        reason = data.get("reason") or f"请求失败（HTTP {r.status_code}）。"
        raise RuntimeError(reason)

    return data


def _safe_read_bytes(path: Optional[str]) -> Optional[bytes]:
    try:
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        return p.read_bytes()
    except Exception:
        return None


def _safe_read_text(path: Optional[str]) -> Optional[str]:
    try:
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def _make_zip_bundle(files: Dict[str, Optional[bytes]]) -> Optional[bytes]:
    """
    files：{文件名: 字节数据} 的字典
    """
    usable = {k: v for k, v in files.items() if v}
    if not usable:
        return None

    buff = io.BytesIO()
    with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, data in usable.items():
            zf.writestr(filename, data)
    buff.seek(0)
    return buff.read()


def _inject_css():
    st.markdown(
        """
        <style>
        /* 布局 */
        .block-container { padding-top: 1.3rem; max-width: 1200px; }
        header { visibility: hidden; }
        footer { visibility: hidden; }

        /* 背景：白色 */
        [data-testid="stAppViewContainer"] {
          background: radial-gradient(1200px 600px at 30% 0%, rgba(56,189,248,0.08), transparent 60%),
                      radial-gradient(1000px 600px at 80% 20%, rgba(249,115,22,0.05), transparent 60%),
                      linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
        }

        /* 卡片 */
        .card {
          background: #ffffff;
          border: 1px solid #e2e8f0;
          border-radius: 16px;
          padding: 16px 16px;
          box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
        }
        .card-title {
          font-size: 0.95rem;
          font-weight: 600;
          color: #0f172a;
          margin-bottom: 6px;
        }
        .muted {
          color: #64748b;
          font-size: 0.85rem;
        }
        .code-value {
          font-size: 1.35rem;
          font-weight: 700;
          color: #0f172a;
          letter-spacing: 0.02em;
          margin-top: 4px;
        }

        /* 徽章 */
        .badge {
          display: inline-block;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 0.78rem;
          border: 1px solid #e2e8f0;
          background: #f1f5f9;
          color: #334155;
          margin-right: 8px;
        }

        /* 按钮 */
        div.stButton > button {
          border-radius: 12px !important;
          padding: 0.65rem 1.0rem !important;
          font-weight: 600 !important;
        }

        /* 侧边栏 */
        [data-testid="stSidebar"] {
          background: #ffffff;
          border-right: 1px solid #e2e8f0;
        }

        /* 报告标题层级：一/二/三级字号递减、间距紧凑 */
        [data-testid="stMarkdownContainer"] h1 {
          font-size: 1.6rem !important;
          margin: 0.5rem 0 0.6rem !important;
        }
        [data-testid="stMarkdownContainer"] h2 {
          font-size: 1.3rem !important;
          margin: 0.7rem 0 0.4rem !important;
        }
        [data-testid="stMarkdownContainer"] h3 {
          font-size: 1.1rem !important;
          margin: 0.55rem 0 0.3rem !important;
        }
        [data-testid="stMarkdownContainer"] h4 {
          font-size: 1rem !important;
          margin: 0.4rem 0 0.25rem !important;
        }
        [data-testid="stMarkdownContainer"] blockquote {
          margin: 0.35rem 0 !important;
          padding: 0.15rem 0.75rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _init_state():
    if "symbol" not in st.session_state:
        st.session_state.symbol = ""
    if "days" not in st.session_state:
        st.session_state.days = 10

    # 持久化的结果
    if "result" not in st.session_state:
        st.session_state.result = None

    # 持久化的产物字节
    if "artifact_bytes" not in st.session_state:
        st.session_state.artifact_bytes = {}

    # 人工审批草稿
    if "pending_approval" not in st.session_state:
        st.session_state.pending_approval = None


def _render_agent_flow():
    st.markdown(
        """
        <div class="card">
          <div class="card-title">智能体流程</div>
          <div class="muted">
            数据智能体 → 分析智能体 → 合规智能体 → 主管智能体
          </div>
          <div style="margin-top:10px; display:flex; gap:10px; flex-wrap:wrap;">
            <span class="badge">价格（Alpha Vantage）</span>
            <span class="badge">基本面（AKShare）</span>
            <span class="badge">新闻（RSS）</span>
            <span class="badge">中性过滤器</span>
            <span class="badge">Markdown + PDF</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_backend_status():
    with st.sidebar:

        # 非阻塞健康检查
        status_txt = "未知"
        try:
            r = requests.get(HEALTH_ENDPOINT)
            if r.status_code == 200:
                status_txt = "在线"
            else:
                status_txt = f"HTTP {r.status_code}"
        except Exception:
            status_txt = "无法连接"

        st.write(f"状态：**{status_txt}**")
        st.write("启动方式：")
        st.code("uvicorn src.api:app --reload --port 8000", language="bash")

        st.divider()
        st.subheader("提示")
        st.write("- 美股：AAPL、MSFT、META 等；A 股：6 位代码（如 600519）；港股：4 位代码（如 1810）或 5 位（如 00700）。")
        st.write("- 如果基本面获取失败：可能是数据源接口暂不可用。")
        st.write("- PDF 导出使用内置渲染，无需额外安装 pandoc/wkhtmltopdf。")


def _persist_artifacts(result: Dict[str, Any]):
    """
    从 API 返回的路径读取产物，并将字节数据存入 session_state，
    这样下载不会依赖“生成”按钮的状态。
    """
    report_path = result.get("report") or result.get("report_path")
    pdf_path = result.get("pdf") or result.get("pdf_path")
    plot_path = result.get("plot") or result.get("plot_path")
    raw_path = result.get("raw") or result.get("raw_data")

    md_bytes = _safe_read_bytes(report_path)
    pdf_bytes = _safe_read_bytes(pdf_path)
    chart_bytes = _safe_read_bytes(plot_path)
    raw_bytes = _safe_read_bytes(raw_path)

    stored = {
        "symbol": result.get("symbol"),
        "report.md": md_bytes,
        "report.pdf": pdf_bytes,
        "chart.png": chart_bytes,
        "raw.json": raw_bytes,
        "_paths": {
            "md": report_path,
            "pdf": pdf_path,
            "chart": plot_path,
            "raw": raw_path,
        }
    }
    st.session_state.artifact_bytes = stored


def _render_markdown_sections(md_text: str):
    """
    把 Markdown 按二级标题拆成可折叠章节：
      - 每个 "## " 章节放进 st.expander（标题前加 ▸ 小三角，默认展开、可点击收起）；
      - H1 标题等正文前的行保持原样渲染在最上方。
    """
    preamble = []
    sections = []  # (标题, 正文行列表)
    current = None
    body = []

    for line in (md_text or "").splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            if current is not None or body:
                sections.append((current or "", body))
            current = line[3:].strip()
            body = []
        elif current is None:
            preamble.append(line)
        else:
            body.append(line)

    if current is not None or body:
        sections.append((current or "", body))
    elif not sections:
        # 没有二级标题时保持原样
        st.markdown(md_text)
        return

    if preamble:
        st.markdown("\n".join(preamble).strip())
    for heading, body_lines in sections:
        with st.expander(f"▸ {heading}", expanded=True):
            st.markdown("\n".join(body_lines).strip())


def _render_results():
    """
    如果存在持久化的结果则进行渲染。
    """
    result = st.session_state.result
    if not result:
        return

    st.success("报告生成成功。")
    with st.expander("API 响应数据", expanded=False):
        st.json(result)

    paths = st.session_state.artifact_bytes.get("_paths", {})
    md_path = paths.get("md")
    pdf_path = paths.get("pdf")
    chart_path = paths.get("chart")
    raw_path = paths.get("raw")

    tabs = st.tabs(["概览", "报告", "图表", "原始数据", "下载"])

    # --- 概览 ---
    with tabs[0]:
        cols = st.columns(2)
        with cols[0]:
            code = (result.get("symbol") or "").upper()
            st.markdown(
                f'<div class="card"><div class="card-title">股票代码</div>'
                f'<div class="code-value">{code}</div></div>',
                unsafe_allow_html=True,
            )

        with cols[1]:
            available = []
            if md_path and Path(md_path).exists(): available.append("Markdown")
            if pdf_path and Path(pdf_path).exists(): available.append("PDF")
            if chart_path and Path(chart_path).exists(): available.append("图表")
            if raw_path and Path(raw_path).exists(): available.append("原始 JSON")
            if available:
                badges = "".join(
                    f'<span class="badge">✓ {name}</span>' for name in available
                )
                body = f'<div style="margin-top:8px;">{badges}</div>'
            else:
                body = '<div class="muted" style="margin-top:8px;">未找到</div>'
            st.markdown(
                f'<div class="card"><div class="card-title">产物</div>{body}</div>',
                unsafe_allow_html=True,
            )

        st.write("")
        _render_agent_flow()

    # --- 报告 ---
    with tabs[1]:
        md_text = _safe_read_text(md_path)
        if md_text:
            _render_markdown_sections(md_text)
        else:
            st.warning("未找到 Markdown 报告。请确保 API 和 UI 运行在同一台机器上，或通过 API 提供文件服务。")

    # --- 图表 ---
    with tabs[2]:
        if chart_path and Path(chart_path).exists():
            st.image(chart_path, caption="价格走势图", width='stretch')
        else:
            st.warning("未找到图表。")

    # --- 原始数据 ---
    with tabs[3]:
        raw_text = _safe_read_text(raw_path)
        if raw_text:
            try:
                st.json(json.loads(raw_text))
            except Exception:
                st.code(raw_text)
        else:
            st.warning("未找到原始 JSON。")

    # --- 下载 ---
    with tabs[4]:
        stored = st.session_state.artifact_bytes or {}
        md_b = stored.get("report.md")
        pdf_b = stored.get("report.pdf")
        raw_b = stored.get("raw.json")
        chart_b = stored.get("chart.png")
        symbol = stored.get("symbol")

        colA, colB, colC, colD = st.columns(4)
        with colA:
            if md_b:
                st.download_button(
                    "下载 .md",
                    data=md_b,
                    file_name=Path(md_path).name if md_path else "report.md",
                    mime="text/markdown",
                    key="dl_md",
                )
        with colB:
            if pdf_b:
                st.download_button(
                    "下载 .pdf",
                    data=pdf_b,
                    file_name=Path(pdf_path).name if pdf_path else "report.pdf",
                    mime="application/pdf",
                    key="dl_pdf",
                )
        with colC:
            if raw_b:
                st.download_button(
                    "下载原始 .json",
                    data=raw_b,
                    file_name=Path(raw_path).name if raw_path else "raw.json",
                    mime="application/json",
                    key="dl_raw",
                )
        with colD:
            if chart_b:
                st.download_button(
                    "下载图表 .png",
                    data=chart_b,
                    file_name=Path(chart_path).name if chart_path else "chart.png",
                    mime="image/png",
                    key="dl_chart",
                )

        st.write("")
        bundle_zip = _make_zip_bundle({
            Path(md_path).name if md_path else "report.md": md_b,
            Path(pdf_path).name if pdf_path else "report.pdf": pdf_b,
            Path(raw_path).name if raw_path else "raw.json": raw_b,
            Path(chart_path).name if chart_path else "chart.png": chart_b,
        })
        if bundle_zip:
            st.download_button(
                "下载全部（zip 压缩包）",
                data=bundle_zip,
                file_name=f"{symbol}.zip",
                mime="application/zip",
                key="dl_zip",
            )

    st.write("")
    if st.button("清除结果", type="secondary"):
        st.session_state.result = None
        st.session_state.artifact_bytes = {}
        st.session_state.pending_approval = None
        st.rerun()


# -----------------------------
# 应用
# -----------------------------
def main():
    st.set_page_config(
        page_title="多智能体股票研究",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_css()
    _init_state()
    _render_backend_status()

    # 主标题 / 头部
    st.markdown(
        """
        <div style="padding: 10px 0 14px 0;">
          <div style="font-size: 2.3rem; font-weight: 800; color: #0f172a;">
            多智能体股票研究
          </div>
          <div class="muted" style="margin-top:6px;">
            使用 LangGraph 工作流生成研究快照：
            数据 → 分析 → 合规 → 主管。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    # st.image("docs/_Multiagent.svg", width='stretch')

    # 输入表单（防止重跑噪声）
    with st.form("run_form", clear_on_submit=False):
        c1, c2 = st.columns([3, 1])
        with c1:
            symbol = st.text_input("股票代码", value=st.session_state.symbol, placeholder="如 AAPL / 600519 / 00700")
        with c2:
            days = st.number_input("天数", min_value=5, max_value=10, value=int(st.session_state.days), step=1)
            human = st.checkbox("人工审批", value=False, help="生成草稿后暂停，等待人工批准/驳回/修改")

        submitted = st.form_submit_button("生成报告", type="primary")

    # 提交操作
    if submitted:
        try:
            symbol_clean = _validate_symbol(symbol)
            days_clean = _validate_days(int(days))
        except Exception as ve:
            st.error(str(ve))
        else:
            st.session_state.symbol = symbol_clean
            st.session_state.days = days_clean

            with st.spinner(f"正在为 {symbol_clean} 生成快照（{days_clean} 天）…"):
                try:
                    result = _call_api(symbol_clean, days_clean, human)
                except Exception as e:
                    st.error(str(e))
                    st.info(
                        "如果基本面获取失败，可能是数据源接口暂不可用。可稍后重试，或考虑 strict_mode: false。")
                else:
                    if result.get("status") == "pending_approval":
                        st.session_state.pending_approval = result
                    else:
                        st.session_state.result = result
                        _persist_artifacts(result)
                        # 概览卡片显示的是规范化后的代码，同步回输入框（如 0700.HK -> 00700）
                        st.session_state.symbol = (result.get("symbol") or symbol_clean).upper()
                    st.rerun()

    # 人工审批面板（HITL）：流水线在审批边暂停时展示草稿
    pending = st.session_state.get("pending_approval")
    if pending:
        st.subheader("人工审批")
        st.markdown(pending.get("draft") or "（暂无草稿内容）")
        action = st.radio(
            "审批决定",
            ["approve", "reject", "edit"],
            format_func=lambda x: {"approve": "批准", "reject": "驳回并反馈", "edit": "修改后发布"}[x],
            horizontal=True,
        )
        edit_text = st.text_area(
            "意见或修改后的正文",
            height=220,
            placeholder="驳回时填写修改意见；修改时直接粘贴修改后的正文",
        )
        if st.button("提交审批", type="primary"):
            try:
                with st.spinner("正在继续生成最终报告（主管智能体写作中），请稍候…"):
                    data = _call_approve(pending["run_id"], action, edit_text)
            except Exception as e:
                st.error(str(e))
            else:
                if data.get("status") == "pending_approval":
                    st.session_state.pending_approval = data
                    st.info("报告再次进入审批，请继续审阅。")
                else:
                    st.session_state.pending_approval = None
                    st.session_state.result = data
                    _persist_artifacts(data)
                st.rerun()

    # 即使经过下载/重跑，仍渲染持久化的结果
    _render_results()

    # 页脚说明
    st.caption("免责声明：仅供参考，不构成投资建议。不保证数据准确性。")


if __name__ == "__main__":
    main()
