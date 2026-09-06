# src/graph/orchestrator.py
from __future__ import annotations

import uuid
import yaml
import textwrap
import re
import os
import time
import concurrent.futures as cf
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from langgraph.errors import GraphBubbleUp
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from src.guardrails.inputs import validate_request
from src.guardrails.outputs import enforce_neutrality
from src.utils.checkpointer import get_checkpointer
from src.utils.helper import safe_num
from src.utils.log_context import set_log_context
from src.utils.logger import get_logger, log_structured
from src.observability import metrics
from src.observability.llm import InstrumentedLLM
from src.runtime.backends import get_short_term_memory
from src.runtime.memory import get_memory_store
from src.runtime.embedder import get_embedder
from src.runtime.summarizer import get_summarizer
from src.tools.math_tool import basic_return_stats
from src.tools.plot_tool import save_price_plot
from src.tools.storage_tool import save_json, save_markdown, render_filename
from src.tools.quote_tool import fetch_quote
from src.tools.price_tool import fetch_price_history
from src.tools.fundamentals_tool import fetch_income_statement, fetch_key_metrics

from src.agents.data_agent import DataAgent
from src.agents.analyst_agent import AnalystAgent
from src.agents.compliance_agent import ComplianceAgent
from src.agents.supervisor_agent import SupervisorAgent

logger = get_logger(__name__)


def _state_warn(state: dict, msg: str):
    """将非致命警告累积起来，用于报告和日志中。"""
    logger.warning(msg)
    state.setdefault("__warnings__", []).append(msg)


def _bundle_error_summary(bundle: dict) -> list[str]:
    """
    收集 DataAgent 输出中存在的错误（如果存在）。
    支持：
      - bundle["__errors__"]：dict/str 列表
      - bundle["prices"]["__error__"]
      - bundle["fundamentals"]["__errors__"]
      - 带 "__error__" 标记的新闻条目
    """
    msgs: list[str] = []

    for e in bundle.get("__errors__", []) or []:
        if isinstance(e, dict):
            where = e.get("where", "未知")
            message = e.get("message", "") or e.get("text", "")
            msgs.append(f"{where}: {message}".strip())
        else:
            msgs.append(str(e))

    p_err = (bundle.get("prices") or {}).get("__error__")
    if p_err:
        if isinstance(p_err, dict):
            msgs.append(f"价格：{p_err.get('message', '')}".strip())
        else:
            msgs.append(f"价格：{p_err}")

    f = bundle.get("fundamentals") or {}
    for e in f.get("__errors__", []) or []:
        if isinstance(e, dict):
            msgs.append(f"{e.get('where', '未知')}: {e.get('message', '')}".strip())
        else:
            msgs.append(str(e))

    news = bundle.get("news")
    if isinstance(news, list):
        for n in news:
            if isinstance(n, dict) and n.get("__error__"):
                src = n.get("source", "")
                msgs.append(f"新闻：{n.get('__error__')}{' | ' + src if src else ''}")

    out: list[str] = []
    seen = set()
    for m in msgs:
        m = str(m).strip()
        if m and m not in seen:
            out.append(m)
            seen.add(m)
    return out


def _critical_missing(bundle: Dict[str, Any]) -> list:
    """返回关键数据中缺失的项（prices / income_statement / key_metrics_ttm）。"""
    missing = []
    prices = bundle.get("prices", {}) or {}
    if not bool(prices.get("data")):
        missing.append("prices")

    fundamentals = bundle.get("fundamentals", {}) or {}
    inc_list = fundamentals.get("income_statement")
    met_list = fundamentals.get("key_metrics_ttm")
    if isinstance(inc_list, dict):
        inc_list = inc_list.get("income_statement", [])
    if isinstance(met_list, dict):
        met_list = met_list.get("key_metrics_ttm", [])

    if not (inc_list and isinstance(inc_list, list)):
        missing.append("income_statement")
    if not (met_list and isinstance(met_list, list)):
        missing.append("key_metrics_ttm")
    return missing


def _clear_fund_error(bundle: Dict[str, Any], where: str) -> None:
    """修复成功后移除对应的错误标记，避免报告的"数据质量说明"误报已修复的问题。"""
    fundamentals = bundle.get("fundamentals") or {}
    fundamentals["__errors__"] = [
        e for e in (fundamentals.get("__errors__") or []) if not (isinstance(e, dict) and e.get("where") == where)
    ]
    # 兼容旧结构：单条 __error__ dict
    for key in ("__error__",):
        err = fundamentals.get(key)
        if isinstance(err, dict) and err.get("where") == where:
            fundamentals.pop(key, None)


_TOP_SECTION_NAMES = {
    "概览",
    "价格走势",
    "基本面",
    "估值与技术面",
    "估值和技术面",
    "估值/技术面",
    "新闻头条及解读",
    "新闻头条",
    "关键关注事项",
    "风险",
    "短期展望",
    "数据来源",
}


def _heading_markdown(text: str) -> str:
    """顶层章节归为三级标题，其余小节归为四级标题。"""
    normalized = re.sub(r"\s+", "", text)
    level = 3 if normalized in _TOP_SECTION_NAMES else 4
    return "#" * level + " " + text


def _normalize_supervisor_markdown(md_text: str) -> tuple:
    """
    整理主管智能体输出的标题层级与空白：
      - 提取第一个 H1 作为报告标题；
      - 保留相对层级：最浅的章节归为 H3，更深的小节归为 H4 及以下；
        若源文本层级完全扁平（全是一级标题），则按已知顶层章节名归类，其余归 H4；
      - 丢弃孤立的 # / ## 等空标题行，折叠多余空行。
    返回 (标题, 正文)。
    """
    title = ""
    out_lines = []
    heading_infos = []  # (占位行索引, 原始级别, 标题文本)
    for line in (md_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append("")
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m:
            level = len(m.group(1))
            if level == 1 and not title:
                title = m.group(2).strip()
                continue
            heading_infos.append((len(out_lines), level, m.group(2).strip()))
            out_lines.append(None)  # 占位，待确定级别后填充
            continue
        # 孤立的 # / ## 等空标题行：直接丢弃
        if re.match(r"^#{1,6}$", stripped):
            continue
        out_lines.append(line)

    levels = [info[1] for info in heading_infos]
    if levels and len(set(levels)) > 1:
        # 源文本自带层级：整体重定位，让最浅的章节落在 H3
        offset = 3 - min(levels)
        for idx, level, text in heading_infos:
            target = max(3, min(6, level + offset))
            out_lines[idx] = "#" * target + " " + text
    else:
        # 源文本层级扁平：按已知顶层章节名归类
        for idx, _level, text in heading_infos:
            out_lines[idx] = _heading_markdown(text)

    body = "\n".join(out_lines).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    return title, body


def _daily_market_metrics(price_rows: List[Dict[str, Any]], inc: Dict[str, Any]) -> Dict[str, Any]:
    """
    从日线价格和最近一期利润表摘要计算日频指标：
      最新收盘价、区间涨跌幅、静态市盈率（最新收盘价 / 摊薄 EPS）。
    """
    closes = [r.get("Close") for r in price_rows if isinstance(r.get("Close"), (float, int))]
    dated_rows = [r for r in price_rows if r.get("Date")]
    out: Dict[str, Any] = {
        "latest_close": None,
        "period_return": None,
        "pe": None,
        "latest_date": None,
        "start_date": None,
    }
    if closes:
        out["latest_close"] = float(closes[-1])
        if len(closes) >= 2 and closes[0]:
            out["period_return"] = closes[-1] / closes[0] - 1.0
    if dated_rows:
        out["latest_date"] = str(dated_rows[-1].get("Date"))
        out["start_date"] = str(dated_rows[0].get("Date"))

    eps = inc.get("epsDiluted")
    if out["latest_close"] and isinstance(eps, (int, float)) and eps > 0:
        pe = out["latest_close"] / float(eps)
        # 过滤币种错配等导致的异常值（如 ADR 美元价格 ÷ 外币 EPS）
        if 0.05 <= pe <= 10000:
            out["pe"] = pe
    return out


def _retrieve_memory(symbol: str, query: str) -> list:
    """按符号/查询检索长期记忆（相似度 + 时间衰减），失败时安全返回空。"""
    try:
        store = get_memory_store()
        rows = store.search(get_embedder().embed_one(query), k=5, session_key=symbol)
        if not rows:
            rows = store.recent(symbol, k=3)
        return rows
    except Exception as e:
        logger.warning("记忆检索失败：%s", e)
        return []


def _memory_reference_block(rows: list) -> str:
    if not rows:
        return ""
    lines = []
    for r in rows[:5]:
        content = r.get("content")
        if not content:
            continue
        score = r.get("score")
        score_txt = f"（相关度 {score:.2f}）" if isinstance(score, (int, float)) else ""
        lines.append(f"- {content}{score_txt}")
    return "\n## 历史记忆参考\n" + ("\n".join(lines)) + "\n" if lines else ""


def _build_memory_text(symbol: str, daily: Dict[str, Any], inc: Dict[str, Any]) -> str:
    parts = [f"{symbol} 股价研究要点"]
    if daily.get("latest_close") is not None:
        parts.append(f"- 最新收盘价：{daily['latest_close']:.2f}")
    if daily.get("pe") is not None:
        parts.append(f"- 市盈率：{daily['pe']:.2f}")
    if inc.get("revenue"):
        parts.append(f"- 营收：{inc['revenue']}")
    if inc.get("netIncome"):
        parts.append(f"- 净利润：{inc['netIncome']}")
    return "\n".join(parts)


def _llm_from_cfg(cfg: Dict[str, Any]) -> ChatOpenAI | ChatDeepSeek:
    """
    根据 cfg["llm"]["provider"] 初始化聊天模型。
    - "deepseek"：ChatDeepSeek（从 .env 读取 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL）。
    - "openai"：ChatOpenAI；GPT-5 和 GPT-4o 系列仅接受默认温度（1.0）。
    """
    prov = cfg["llm"]["provider"]
    model = cfg["llm"]["model"]
    model = os.getenv("LLM_MODEL") or model
    temperature = cfg["llm"].get("temperature", 1.0)
    max_tokens = cfg["llm"].get("max_tokens", 3500)

    if prov == "deepseek":
        logger.info("正在初始化 LLM：provider=deepseek，模型=%s，温度=%.1f", model, temperature)
        kwargs: Dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        api_key = os.getenv("DEEPSEEK_API_KEY")
        base_url = os.getenv("DEEPSEEK_BASE_URL")
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        return InstrumentedLLM(ChatDeepSeek(**kwargs), provider="deepseek", model=model)

    # OpenAI 兼容路径（provider=openai 时）
    if any(tag in model for tag in ["gpt-5", "gpt-4o"]) and temperature != 1.0:
        logger.warning(
            "模型 %s 不支持自定义温度（%.1f），已覆盖为 1.0 以保持兼容。",
            model,
            temperature,
        )
        temperature = 1.0

    logger.info("正在初始化 LLM：provider=%s，模型=%s，温度=%.1f", prov, model, temperature)
    openai_kwargs: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    openai_key = os.getenv("OPENAI_API_KEY")
    openai_base = os.getenv("OPENAI_BASE_URL")
    if openai_key:
        openai_kwargs["api_key"] = openai_key
    if openai_base:
        openai_kwargs["base_url"] = openai_base
    return InstrumentedLLM(ChatOpenAI(**openai_kwargs), provider=prov, model=model)


def _instrument_node(name: str, fn):
    """给图节点加计时 + 节点上下文 + 结构化日志（供 /metrics 与 ELK 采集）。"""

    def wrapper(state):
        metrics.set_node_node(name)
        start = time.monotonic()
        try:
            result = fn(state)
        except GraphBubbleUp:
            # interrupt() 暂停是 HITL 的正常流程，不计入 error 指标
            metrics.observe_node(name, time.monotonic() - start, "interrupted")
            raise
        except Exception:
            metrics.observe_node(name, time.monotonic() - start, "error")
            raise
        duration = time.monotonic() - start
        metrics.observe_node(name, duration, "ok")
        log_structured(logger, "node_completed", node=name, duration=round(duration, 4))
        return result

    return wrapper


def build_graph(cfg: Dict[str, Any]):
    """
    构建连接全部 4 个智能体的 LangGraph 编排流水线。
    """
    llm = _llm_from_cfg(cfg)
    rss = cfg["news"]["sources"]
    max_news = cfg["orchestration"]["max_news"]

    data_agent = DataAgent(llm, rss_templates=rss, max_news=max_news)
    analyst = AnalystAgent(llm)
    compliance = ComplianceAgent(
        llm,
        forbidden=cfg["compliance"]["forbidden_phrases"],
        disclosure=cfg["compliance"]["disclosure"],
    )
    supervisor = SupervisorAgent(llm)

    g = StateGraph(dict)

    def node_collect_data(state: dict):
        symbol, days = state["symbol"], state["days"]
        logger.info("[运行:%s][节点: 收集数据] 正在为 %s 开始数据收集", state.get("run_id"), symbol)

        bundle = data_agent.run(symbol, days)
        state["bundle"] = bundle

        logger.info("[运行:%s][节点: 收集数据] 已完成 %s 的数据收集", state.get("run_id"), symbol)

        # 检测 fundamentals_tool 暴露的上游 API 错误（402 直接中止）
        # DataAgent 把错误统一收进 fundamentals["__errors__"]（list），
        # 每个 error dict 带 where/status/message 字段。
        fundamentals = bundle.get("fundamentals", {}) or {}
        fund_errors = [
            e
            for e in (fundamentals.get("__errors__") or [])
            if isinstance(e, dict) and isinstance(e.get("status"), int) and e["status"] == 402
        ]
        if fund_errors:
            where = fund_errors[0].get("where", "fundamentals")
            msg = (
                f"{symbol} 的上游 API 调用失败（{where}）：状态码=402 - {fund_errors[0].get('message')}。"
                "建议：检查数据源配置，或关闭 strict_mode。"
            )
            logger.error(msg)
            state["__fatal__"] = msg
            raise ValueError(msg)

        # 数据质量评估：缺失项交给条件路由，决定是否进入修复节点
        state["__missing__"] = _critical_missing(bundle)

        # 新闻为软性数据：缺失仅警告，不阻塞报告生成
        news_items = bundle.get("news", [])
        if isinstance(news_items, list):
            news_ok = any(isinstance(n, dict) and not n.get("__error__") for n in news_items)
        else:
            news_ok = False
        if not news_ok:
            _state_warn(state, f"{symbol}：新闻源为空/不可用，将在没有头条的情况下继续。")

        # 非致命的上游工具错误统一记录
        for m in _bundle_error_summary(bundle):
            _state_warn(state, f"{symbol} 的数据警告：{m}")

        # 长期记忆：检索与本次股票相关的历史事实（供报告引用，可观测）
        try:
            memory_rows = _retrieve_memory(symbol, f"{symbol} 股票研究报告")
            state["memory_read"] = memory_rows
            if memory_rows:
                logger.info("[运行:%s] 检索到 %d 条长期记忆（%s）", state.get("run_id"), len(memory_rows), symbol)
        except Exception:
            state["memory_read"] = []

        return state

    def route_after_collect(state: dict):
        missing = state.get("__missing__") or _critical_missing(state.get("bundle", {}))
        return "repair_data" if missing else "analyze"

    def node_repair_data(state: dict):
        symbol, days = state["symbol"], state["days"]
        bundle = state.get("bundle", {})
        missing = _critical_missing(bundle)
        logger.info("[运行:%s][节点: 修复] %s 缺少关键数据 %s，尝试补充", state.get("run_id"), symbol, missing)

        if "prices" in missing:
            res = fetch_price_history(symbol, days)
            if res.get("data"):
                bundle["prices"] = res
                logger.info("[运行:%s][节点: 修复] %s 的价格数据已补充", state.get("run_id"), symbol)
        if "income_statement" in missing:
            res = fetch_income_statement(symbol, limit=2)
            if res.get("income_statement"):
                bundle.setdefault("fundamentals", {})["income_statement"] = res["income_statement"]
                _clear_fund_error(bundle, "income_statement")
                logger.info("[运行:%s][节点: 修复] %s 的利润表摘要已补充", state.get("run_id"), symbol)
        if "key_metrics_ttm" in missing:
            res = fetch_key_metrics(symbol)
            if res.get("key_metrics_ttm"):
                bundle.setdefault("fundamentals", {})["key_metrics_ttm"] = res["key_metrics_ttm"]
                _clear_fund_error(bundle, "key_metrics_ttm")
                logger.info("[运行:%s][节点: 修复] %s 的关键指标已补充", state.get("run_id"), symbol)

        state["bundle"] = bundle
        still_missing = _critical_missing(bundle)
        logger.info("[运行:%s][节点: 修复] %s 修复完成，仍缺失：%s", state.get("run_id"), symbol, still_missing)
        return state

    def route_after_repair(state: dict):
        missing = _critical_missing(state.get("bundle", {}))
        return "validate_data" if missing else "analyze"

    def node_validate_data(state: dict):
        symbol = state["symbol"]
        bundle = state.get("bundle", {})
        missing = _critical_missing(bundle)
        strict = bool(cfg.get("StrictMode", {}).get("strict_mode", True))

        if strict and missing:
            hint = ""
            if "income_statement" in missing or "key_metrics_ttm" in missing:
                hint = " 可能原因：数据源接口暂不可用、请求受限，或该股票代码对应的数据缺失。"
            metrics.inc_strict_abort()
            reason = f"严格模式中止：缺少关键数据：{', '.join(missing)}（股票代码：{symbol}）。{hint}"
            logger.error("%s", reason)
            state["__fatal__"] = reason
            raise ValueError(reason)

        # 非严格模式：缺失项记录警告后继续
        if "prices" in missing:
            _state_warn(state, f"{symbol}：价格序列缺失/为空，报告生成可能失败。")
        if "income_statement" in missing:
            _state_warn(state, f"{symbol}：基本面 income_statement 缺失，将使用 N/A 占位符。")
        if "key_metrics_ttm" in missing:
            _state_warn(state, f"{symbol}：基本面 key_metrics_ttm 缺失，将使用 N/A 占位符。")
        return state

    def node_analyze(state: dict):
        symbol = state["symbol"]
        logger.info("[运行:%s][节点: 分析] 正在为 %s 生成分析师备注", state.get("run_id"), symbol)

        try:
            feedback = state.get("approval_feedback") or ""
            # 短期记忆：叠加本轮之前的历史反馈（HITL 驳回循环上下文）
            try:
                history = get_short_term_memory().recent(str(state.get("run_id", "")), 3)
                if history:
                    ctx = "\n".join(
                        f"- 第{i + 1}轮：{h.get('comment') or h.get('action', '')}" for i, h in enumerate(history)
                    )
                    feedback = (feedback + f"\n\n历史反馈上下文：\n{ctx}").strip()
            except Exception:
                pass
            note = analyst.run(state["bundle"], state["days"], feedback=feedback)
            if not isinstance(note, str) or not note.strip():
                raise ValueError("AnalystAgent 返回了空输出。")
            state["analyst_note"] = note
        except Exception as e:
            _state_warn(state, f"{symbol}：AnalystAgent 失败，正在使用回退备注。（{e}）")
            state["analyst_note"] = "由于上游生成错误，分析师备注暂不可用。" "报告将继续使用可用的市场数据和披露信息。"

        logger.info("[运行:%s][节点: 分析] %s 已完成", state.get("run_id"), symbol)
        return state

    def node_compliance(state: dict):
        symbol = state["symbol"]
        logger.info("[运行:%s][节点: 合规] 正在对 %s 执行合规检查", state.get("run_id"), symbol)

        try:
            final_note = compliance.run(state["analyst_note"])
            if not isinstance(final_note, str) or not final_note.strip():
                raise ValueError("ComplianceAgent 返回了空输出。")
        except Exception as e:
            _state_warn(state, f"{symbol}：ComplianceAgent 失败，回退使用分析师备注。（{e}）")
            final_note = state.get("analyst_note", "")

        final_note = enforce_neutrality(final_note, cfg["compliance"]["forbidden_phrases"])
        if "[REDACTED]" in final_note:
            logger.warning("[运行:%s] 已对 %s 应用合规删改", state.get("run_id"), symbol)

        state["final_note"] = final_note
        logger.info("[运行:%s][节点: 合规] %s 已完成", state.get("run_id"), symbol)
        return state

    def route_after_compliance(state: dict):
        # 开启人工审批时才进入审批边，否则直接发布
        return "approval" if state.get("require_approval") else "supervisor"

    def node_approval(state: dict):
        if not state.get("require_approval"):
            return state

        symbol = state["symbol"]
        feedback = interrupt(
            {
                "symbol": symbol,
                "draft": state.get("final_note", ""),
                "analyst_note": state.get("analyst_note", ""),
                "round": int(state.get("reject_count", 0)) + 1,
            }
        )

        if isinstance(feedback, str):
            action, comment, edited_text = feedback, "", ""
        elif isinstance(feedback, dict):
            action = str(feedback.get("action") or "approve")
            comment = str(feedback.get("comment") or "")
            edited_text = str(feedback.get("edited_text") or "")
        else:
            action, comment, edited_text = "approve", "", ""

        # 短期记忆：记录本轮审批反馈（供分析智能体在驳回后参考）
        try:
            get_short_term_memory().record(
                str(state.get("run_id", "")),
                {"action": action, "comment": comment, "edited_text": edited_text},
            )
        except Exception:
            pass

        state["approval_feedback"] = comment or ""
        if action == "edit" and edited_text.strip():
            # 人工编辑文本同样要过确定性合规过滤，否则审批 UI 成为违规词注入入口
            state["final_note"] = enforce_neutrality(edited_text.strip(), cfg["compliance"]["forbidden_phrases"])
        if action == "reject":
            state["reject_count"] = int(state.get("reject_count", 0)) + 1
        state["__approval_action__"] = action
        logger.info("[运行:%s][节点: 审批] %s 审批结果：%s", state.get("run_id"), symbol, action)
        return state

    def route_after_approval(state: dict):
        action = state.get("__approval_action__", "approve")
        if action == "reject" and int(state.get("reject_count", 0)) < 3:
            return "analyze"
        return "supervisor"

    def node_supervise(state: dict):
        symbol, outdir = state["symbol"], state["outdir"]
        bundle = state["bundle"]

        # 数据质量说明
        dq_msgs = []
        dq_msgs.extend(state.get("__warnings__", []) or [])
        dq_msgs.extend(_bundle_error_summary(bundle))

        dq_block = ""
        if dq_msgs:
            uniq = []
            seen = set()
            for m in dq_msgs:
                m = str(m).strip()
                if m and m not in seen:
                    uniq.append(m)
                    seen.add(m)
            dq_lines = "\n".join([f"- {m}" for m in uniq[:8]])
            dq_block = f"\n## 数据质量说明\n{dq_lines}\n"

        logger.info("[运行:%s][节点: 发布] 正在完善并发布 %s 的统一报告", state.get("run_id"), symbol)

        price_rows = bundle.get("prices", {}).get("data", [])
        if not price_rows:
            error_msg = f"{symbol} 没有可用的价格数据。股票代码可能无效或已退市，" "跳过报告生成。"
            logger.error(error_msg)
            state["error"] = error_msg
            return state

        dates = [r.get("Date") for r in price_rows if r.get("Date")]
        closes = [r.get("Close") for r in price_rows if isinstance(r.get("Close"), (float, int))]

        stats = basic_return_stats(closes)
        mean_ret = float(stats.get("mean") or 0.0)
        vol_ret = float(stats.get("vol") or 0.0)
        min_ret = float(stats.get("min") or 0.0)
        max_ret = float(stats.get("max") or 0.0)

        fundamentals = bundle.get("fundamentals", {})
        inc_list = fundamentals.get("income_statement", [])
        met_list = fundamentals.get("key_metrics_ttm", [])

        inc = inc_list[0] if isinstance(inc_list, list) and inc_list else {}
        reported_currency = inc.get("reportedCurrency")

        daily = _daily_market_metrics(price_rows, inc)
        daily_block = ""
        if daily.get("latest_close") is not None:
            daily_lines = [
                f"- 最新收盘价：{daily['latest_close']:.2f} {reported_currency or ''}".rstrip()
                + (f"（{daily.get('latest_date') or 'N/A'}）")
            ]
            if daily.get("period_return") is not None:
                daily_lines.append(
                    f"- 区间涨跌幅：{daily['period_return'] * 100:.2f}%"
                    f"（{daily.get('start_date') or 'N/A'} → {daily.get('latest_date') or 'N/A'}）"
                )
            if daily.get("pe") is not None:
                daily_lines.append(f"- 市盈率（最新收盘价 ÷ 最近一期摊薄 EPS）：{daily['pe']:.2f}")
            daily_block = "\n" + "\n".join(daily_lines)

        # 图表生成（安全）
        try:
            plot_path = save_price_plot(dates, closes, reported_currency, os.path.join(outdir, f"{symbol}_chart.png"))
        except Exception as e:
            _state_warn(state, f"{symbol}：图表生成失败。（{e}）")
            plot_path = ""

        # --- 新闻头条（跳过错误标记）---
        news_items = bundle.get("news", [])
        cleaned_news = []
        if isinstance(news_items, list) and news_items:
            for n in news_items[:8]:
                if isinstance(n, dict) and n.get("__error__"):
                    continue  # 重要提示：不要将错误标记作为头条展示

                title = (n.get("title", "无标题") if isinstance(n, dict) else "无标题").strip()
                title = re.sub(r"[\[\]\n\r]+", " ", title)
                link = (n.get("link", "") if isinstance(n, dict) else "").strip()
                date_str = (n.get("published", "") if isinstance(n, dict) else "")[:16] or "N/A"

                # 只保留 http(s) 链接：javascript:/data: 等伪协议不允许进入报告渲染面
                if link and re.match(r"^https?://", link, re.IGNORECASE):
                    cleaned_news.append(f"- [{title}]({link}) ({date_str})")
                else:
                    cleaned_news.append(f"- {title} ({date_str})")

        if cleaned_news:
            news_summary = "\n" + "\n".join(cleaned_news)
        else:
            news_summary = "本期未发现重要的新闻头条。"

        # SupervisorAgent 综合汇总（安全）
        try:
            # 传入真实数据内容而不是只有统计行数：supervisor prompt 要求产出
            # 走势/基本面/新闻解读等章节，信息不足会导致模型编造细节
            price_block = "\n".join(
                f"- {r.get('Date')}: 收盘 {r.get('Close')}"
                for r in price_rows[-10:]  # 只给最近 10 天，控制上下文长度
            )
            fundamentals_summary = ""
            if isinstance(inc, dict) and inc:
                fundamentals_summary = (
                    f"最近财年 {inc.get('fiscalYear', 'N/A')}："
                    f"营收 {safe_num(inc.get('revenue'), reported_currency)}，"
                    f"净利润 {safe_num(inc.get('netIncome'), reported_currency)}，"
                    f"EPS {safe_num(inc.get('epsDiluted'), reported_currency, decimals=2)}"
                )
            news_lines = "\n".join(cleaned_news[:5]) or "无"
            supervisor_input_summary = (
                f"价格数据（{len(price_rows)} 行，最近 10 个交易日）：\n{price_block}\n\n"
                f"基本面：{fundamentals_summary or '不可用'}\n\n"
                f"新闻头条：\n{news_lines}"
            )

            supervisor_text = supervisor.run(
                symbol=symbol,
                data_summary=supervisor_input_summary,
                final_note=state.get("final_note", ""),
            ).strip()

            # 移除悬空的 "## Compliant Note\nFinal Note" 或 "## 合规备注\n最终备注"
            supervisor_text = re.sub(
                r"(?is)\n##\s*(Compliant Note|合规备注)\s*\n\s*(final note|最终备注)?\s*$",
                "",
                supervisor_text,
            ).strip()
            # 提取 H1 标题，其余标题降一级，保证一/二/三级标题层级一致
            report_title, supervisor_body = _normalize_supervisor_markdown(supervisor_text)

        except Exception as e:
            logger.warning("SupervisorAgent 在 %s 上失败，回退使用原始合规文本。（%s）", symbol, e)
            supervisor_text = state.get("final_note", "").strip()
            report_title, supervisor_body = _normalize_supervisor_markdown(supervisor_text)

        stats_block = (
            f"- 均值：{mean_ret:.4f}（{mean_ret * 100:.2f}%）\n"
            f"- 波动率：{vol_ret:.4f}（{vol_ret * 100:.2f}%）\n"
            f"- 最小值：{min_ret:.4f}（{min_ret * 100:.2f}%）\n"
            f"- 最大值：{max_ret:.4f}（{max_ret * 100:.2f}%）\n"
        )

        chart_block = ""
        if plot_path:
            chart_block = f"""
<div style="text-align: center; margin-top: 25px; margin-bottom: 25px;">
  <img src="{os.path.abspath(plot_path)}" alt="价格走势图" style="width: 70%; margin: auto; display: block;">
  <p style="font-weight: bold; margin-top: 10px;">价格走势图</p>
</div>
""".strip()

        model_name = cfg["llm"]["model"]
        report_md = f"""
# {report_title or f"{symbol} 研究报告"}

## 1. 市场概览
- 股票代码：{symbol}
- 数据覆盖：{len(price_rows)} 行（{max(len(price_rows) - 1, 0)} 个收益率）
{daily_block}
{stats_block}- 已处理新闻条目：{len(cleaned_news)}

## 2. 基本面亮点
- 财年：{int(inc.get('fiscalYear')) if inc.get('fiscalYear') else "N/A"}。
- 营收：{safe_num(inc.get('revenue'), reported_currency)}。
- 净利润：{safe_num(inc.get('netIncome'), reported_currency)}。
- 每股收益（摊薄）：{safe_num(inc.get('epsDiluted'), reported_currency, decimals=2)}。

## 3. 近期新闻头条
{news_summary}

## 4. 分析师评论
{supervisor_body}

## 5. 方法论
- 价格数据来自 Alpha Vantage（美股/A 股）与 AKShare（港股）。
- 基本面数据来自 AKShare（免费开源接口）。
- 新闻来自 RSS 源。
- 波动率和收益率使用 numpy 和 pandas 计算。
- 报告通过 LangGraph 多智能体编排生成：
  - 数据智能体：市场和基本面数据收集。
  - 分析智能体：叙述内容生成。
  - 合规智能体：披露和措辞检查。
  - 主管智能体：最终综合和报告结构整理。
{dq_block}
## 6. 执行元数据
- 模型：{model_name}。
- 运行日期：{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}。
- 覆盖天数：{state['days']}。
- 输出目录：{outdir}。
- 数据来源：Alpha Vantage、AKShare、公共 RSS 源。
- 流水线：LangGraph 编排框架。

{chart_block}

## 7. 合规免责声明
本自动化报告由多智能体 AI 研究框架（LangGraph + LangChain + {model_name}）生成。
本报告仅供教育和信息用途，不构成投资建议。
过往表现不代表未来结果，不保证数据准确性。

由自动股票研究多智能体系统生成。
"""

        report_md = textwrap.dedent(report_md).strip()

        # 长期记忆：写入本次要点，并在报告中召回历史（跨运行可观测）
        try:
            memory_rows = state.get("memory_read") or []
            ref_block = _memory_reference_block(memory_rows)
            if ref_block:
                report_md = report_md.replace("\n## 7. 合规免责声明", ref_block + "\n## 7. 合规免责声明")
            mem_store = get_memory_store()
            mem_store.add(
                symbol,
                _build_memory_text(symbol, daily, inc),
                kind="fact",
                importance=0.6,
                meta={"symbol": symbol},
            )
            state["memory_recalled"] = len(memory_rows)  # 召回条数
            state["memory_written"] = 1  # 实际写入 1 条
            logger.info(
                "[运行:%s] 长期记忆：召回 %d 条，写入 1 条（%s）", state.get("run_id"), len(memory_rows), symbol
            )
            # 记忆压缩：超出阈值时 LLM 摘要旧条目，失败按重要度裁剪
            try:
                max_entries = int(os.getenv("MEMORY_MAX_ENTRIES", "50"))
                compacted = mem_store.compact(symbol, max_entries=max_entries, summarizer=get_summarizer())
                state["memory_compacted"] = compacted.get("compacted", 0)
                if compacted.get("compacted"):
                    logger.info("[运行:%s] 长期记忆压缩：%s", state.get("run_id"), compacted)
            except Exception as e2:
                logger.warning("记忆压缩失败（%s）：%s", symbol, e2)
                state["memory_compacted"] = 0
        except Exception as e:
            logger.warning("%s 长期记忆写入失败：%s", symbol, e)
            state["memory_written"] = 0
            state["memory_recalled"] = 0
            state["memory_compacted"] = 0

        file_name = render_filename(cfg["report"]["filename_template"], symbol=symbol)
        save_json(bundle, outdir, f"{symbol}_raw.json")
        save_markdown(report_md, outdir, file_name)

        state["report_path"] = os.path.join(outdir, file_name)
        state["plot_path"] = plot_path
        logger.info(
            "[运行:%s][节点: 发布] %s 的统一报告已就绪 -> %s",
            state.get("run_id"),
            symbol,
            state["report_path"],
        )

        # PDF 导出（安全）：fpdf2 纯 Python 渲染，无需 pandoc/wkhtmltopdf
        pdf_path = os.path.join(outdir, file_name.replace(".md", ".pdf"))
        try:
            from src.tools.pdf_tool import export_report_to_pdf

            export_report_to_pdf(state["report_path"], pdf_path)

            logger.info("[运行:%s][节点: 发布] 已为 %s 生成 PDF 版本 -> %s", state.get("run_id"), symbol, pdf_path)
            state["pdf_path"] = pdf_path

        except Exception as e:
            logger.warning("%s 的 PDF 导出失败：%s", symbol, e)

        return state

    g.add_node("collect_data", _instrument_node("collect_data", node_collect_data))
    g.add_node("repair_data", _instrument_node("repair_data", node_repair_data))
    g.add_node("validate_data", _instrument_node("validate_data", node_validate_data))
    g.add_node("analyze", _instrument_node("analyze", node_analyze))
    g.add_node("compliance", _instrument_node("compliance", node_compliance))
    g.add_node("approval", _instrument_node("approval", node_approval))
    g.add_node("supervisor", _instrument_node("supervisor", node_supervise))

    g.add_edge(START, "collect_data")
    g.add_conditional_edges(
        "collect_data",
        route_after_collect,
        {"analyze": "analyze", "repair_data": "repair_data"},
    )
    g.add_conditional_edges(
        "repair_data",
        route_after_repair,
        {"analyze": "analyze", "validate_data": "validate_data"},
    )
    g.add_edge("validate_data", "analyze")
    g.add_edge("analyze", "compliance")
    g.add_conditional_edges(
        "compliance",
        route_after_compliance,
        {"approval": "approval", "supervisor": "supervisor"},
    )
    g.add_conditional_edges(
        "approval",
        route_after_approval,
        {"analyze": "analyze", "supervisor": "supervisor"},
    )
    g.add_edge("supervisor", END)

    logger.info("LangGraph 流水线构建成功。")
    return g.compile(checkpointer=get_checkpointer())


def _load_cfg() -> Dict[str, Any]:
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config", "settings.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _invoke_with_timeout(app, invoke_input: Any, config: dict, timeout_sec: float):
    """
    在单线程 executor 中执行图，超时后不再等待线程结束。

    注意不能用 `with ThreadPoolExecutor(...)`：with 块退出时
    shutdown(wait=True) 会继续阻塞到任务真正结束，超时形同虚设。
    这里超时后 shutdown(wait=False) 让挂起的线程自行消亡（进程退出时回收）。
    """
    ex = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="graph-invoke")
    try:
        fut = ex.submit(app.invoke, invoke_input, config=config)
        return fut.result(timeout=timeout_sec)
    except cf.TimeoutError:
        raise
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def run_pipeline(symbol: str, days: int, outdir: str, human: bool = False) -> Dict[str, Any]:
    """
    端到端运行多智能体流水线，并返回生成的产物路径。
    """
    req = validate_request(symbol=symbol, days=days, outdir=outdir)
    symbol_uppercase = req.symbol
    run_id = str(uuid.uuid4())
    set_log_context(run_id=run_id, symbol=symbol_uppercase)
    logger.info("运行 ID：%s", run_id)

    days = req.days
    outdir = os.path.join(req.outdir, symbol_uppercase)

    load_dotenv()
    logger.info("=== 正在为 %s 启动流水线（天数=%d）===", symbol_uppercase, days)

    # 按市场预检（港股走 AKShare，其余走 Alpha Vantage）
    # 预检任何异常（如缺 API key）都转为结构化错误，不向上穿透
    try:
        quote = fetch_quote(symbol_uppercase)
    except Exception as e:
        logger.error("预检失败：%s", e)
        quote = {"ok": False, "rate_limited": False, "message": str(e)}
    if quote.get("ok") and not quote.get("valid"):
        error_msg = f"股票代码 {symbol_uppercase} 无效或没有当前市场数据" "（可能已退市或不活跃）。"
        logger.error(error_msg)
        return {
            "status": "error",
            "symbol": symbol_uppercase,
            "reason": error_msg,
            "suggested_action": "请核实股票代码或尝试其他股票。",
        }
    if not quote.get("ok"):
        skip_on_limit = os.getenv("SKIP_PRECHECK_ON_RATELIMIT", "true").lower() not in ("0", "false", "no")
        if quote.get("rate_limited") and skip_on_limit:
            logger.warning(
                "%s 预检因行情源限流失败，跳过预检继续执行：%s",
                symbol_uppercase,
                quote.get("message"),
            )
        else:
            error_msg = f"无法验证股票代码 {symbol_uppercase}：{quote.get('message')}"
            logger.error(error_msg)
            return {"status": "error", "symbol": symbol_uppercase, "reason": error_msg}

    # 加载 YAML 配置
    cfg = _load_cfg()
    logger.debug("已加载配置。")

    # 全局超时
    timeout_sec = int(cfg.get("orchestration", {}).get("timeout_sec", 90))

    try:
        app = build_graph(cfg)
        state = {
            "symbol": symbol_uppercase,
            "days": days,
            "outdir": outdir,
            "run_id": run_id,
            "require_approval": human or str(os.getenv("HUMAN_IN_LOOP", "false")).lower() == "true",
        }
        config = {"configurable": {"thread_id": run_id}}

        # ---- 全局工作流超时 ----
        result = _invoke_with_timeout(app, state, config, timeout_sec)

        if not result.get("report_path"):
            if result.get("__interrupt__"):
                interrupts = result.get("__interrupt__") or []
                payload = interrupts[0].value if interrupts and hasattr(interrupts[0], "value") else {}
                draft = payload.get("draft") if isinstance(payload, dict) else ""
                logger.info("[运行:%s] 流水线在审批边暂停，等待人工审批。", run_id)
                return {
                    "status": "pending_approval",
                    "run_id": run_id,
                    "symbol": symbol_uppercase,
                    "draft": draft or "",
                    "analyst_note": (result or {}).get("analyst_note", ""),
                    "message": "报告草稿已生成，等待人工审批。",
                }
            reason = result.get("error") or "由于缺少必需数据，报告未能生成。"
            return {
                "status": "error",
                "symbol": symbol_uppercase,
                "reason": reason,
                "suggested_action": ("请尝试其他股票代码或调整天数。如果 strict_mode=true，可考虑禁用它。"),
            }

    except cf.TimeoutError:
        msg = f"工作流在 {timeout_sec} 秒后超时（股票代码：{symbol_uppercase}）。"
        logger.error("[运行:%s] %s", run_id, msg)
        return {
            "status": "error",
            "symbol": symbol_uppercase,
            "reason": msg,
            "suggested_action": "请减少天数、稍后重试，或在 settings.yaml 中调大 orchestration.timeout_sec。",
        }

    except ValueError as e:
        msg = str(e)
        logger.error("%s 的流水线执行失败：%s", symbol_uppercase, msg)

        suggestion = None
        if "402" in msg or "Payment Required" in msg:
            suggestion = "请稍后重试或检查数据源配置，也可在 config/settings.yaml 中设置 strict_mode: false。"

        return {
            "status": "error",
            "symbol": symbol_uppercase,
            "reason": msg,
            **({"suggested_action": suggestion} if suggestion else {}),
        }

    except Exception as e:
        logger.exception("%s 的流水线执行失败：%s", symbol_uppercase, e)
        return {
            "status": "error",
            "symbol": symbol_uppercase,
            "reason": f"意外的流水线错误：{e}",
        }

    return {
        "report": result.get("report_path"),
        "plot": result.get("plot_path"),
        "raw": os.path.join(outdir, f"{symbol_uppercase}_raw.json"),
        "pdf": result.get("pdf_path"),
    }


def resume_pipeline(run_id: str, feedback: Any, timeout_sec: Optional[int] = None) -> Dict[str, Any]:
    """
    从审批边恢复执行（HITL）。
    feedback 支持：
      - "approve" / "reject"（字符串）
      - {"action": "approve"|"reject"|"edit", "comment": ..., "edited_text": ...}
    """
    load_dotenv()
    cfg = _load_cfg()
    timeout_sec = timeout_sec or int(cfg.get("orchestration", {}).get("timeout_sec", 90))

    try:
        app = build_graph(cfg)
        config = {"configurable": {"thread_id": run_id}}
        result = _invoke_with_timeout(app, Command(resume=feedback), config, timeout_sec)
    except cf.TimeoutError:
        logger.error("审批恢复执行超时（run_id=%s）", run_id)
        return {"status": "error", "run_id": run_id, "reason": f"审批恢复执行在 {timeout_sec} 秒后超时。"}
    except ValueError as e:
        logger.error("审批恢复执行失败（run_id=%s）：%s", run_id, e)
        return {"status": "error", "run_id": run_id, "reason": str(e)}
    except Exception as e:
        logger.exception("审批恢复执行异常（run_id=%s）：%s", run_id, e)
        return {"status": "error", "run_id": run_id, "reason": f"意外的审批恢复错误：{e}"}

    if result.get("report_path"):
        symbol = result.get("symbol", "")
        outdir = result.get("outdir", "")
        raw_path = os.path.join(outdir, f"{symbol}_raw.json") if outdir and symbol else result.get("raw_path")
        return {
            "status": "success",
            "run_id": run_id,
            "symbol": symbol,
            "report": result.get("report_path"),
            "plot": result.get("plot_path"),
            "raw": raw_path,
            "pdf": result.get("pdf_path"),
        }

    if result.get("__interrupt__"):
        interrupts = result.get("__interrupt__") or []
        payload = interrupts[0].value if interrupts and hasattr(interrupts[0], "value") else {}
        draft = payload.get("draft") if isinstance(payload, dict) else ""
        return {
            "status": "pending_approval",
            "run_id": run_id,
            "symbol": result.get("symbol", ""),
            "draft": draft or "",
            "message": "报告再次进入审批，等待人工审批。",
        }

    reason = result.get("error") or "审批通过后报告未能生成。"
    return {"status": "error", "run_id": run_id, "symbol": result.get("symbol", ""), "reason": reason}
