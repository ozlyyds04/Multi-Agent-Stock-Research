# Multi Agent Stock Research (Powered by LangChain + LangGraph)

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![LangChain](https://img.shields.io/badge/langchain-1.3-purple.svg)](https://github.com/langchain-ai/langchain)
![LLM](https://img.shields.io/badge/Orchestrator-Langgraph-red)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-ready-brightgreen.svg)](https://fastapi.tiangolo.com)
[![LLM](https://img.shields.io/badge/LLM-DeepSeek-black.svg)](https://www.deepseek.com)
![AIEngineering](https://img.shields.io/badge/Discipline-AI_Engineering-purple)
![LangChain](https://img.shields.io/badge/Framework-LangChain-orange)
![LLM](https://img.shields.io/badge/Model-LLM-black)
![LLM](https://img.shields.io/badge/Data-Agent-blue)
![LLM](https://img.shields.io/badge/Analyst-Agent-lightgreen)
![LLM](https://img.shields.io/badge/Compliance-Agent-red)
![LLM](https://img.shields.io/badge/Supervisor-Agent-orange)
---

## Overview

![Architecture Diagram](docs/Multiagent.svg)

![UI Screenshot](docs/Screenshots/UI.png)

> 架构图（SVG，GitHub 原生渲染）· UI 截图（PNG）｜Architecture diagram (SVG, natively rendered by GitHub) & UI screenshot (PNG). 详细说明见 [docs/architecture.md](docs/architecture.md).

---

## Why This Project Exists

The world of financial research has evolved — analysts now rely on automation, AI, and real-time data instead of Excel sheets and manual fundamentals.  
This project demonstrates a **Minimal-production-ready multi-agent research system** built using **LangChain + LangGraph**, designed to automate:

-  Historical stock price analysis  
-  Fundamental data fetching
-  News tracking
-  Analyst-style report generation
-  Compliance filtering
-  PDF + Markdown Research Reports with charts

Whether you're a developer, quant researcher, analyst, or AI enthusiast — this repo shows how to build **real-world LLM-enabled workflows** that generate institutional-quality research without requiring paid API data feeds.

---

##  Key Features

| Feature                        | Description |
|--------------------------------|------------|
| **4-Agent Architecture**       | DataAgent, AnalystAgent, ComplianceAgent, SupervisorAgent |
| **Agent Tool Calling**         | AnalystAgent 在数据缺口下自主调用行情/基本面工具补充数据 |
| **Conditional Routing + Repair** | 数据缺失自动进入修复节点补充，失败时按 strict_mode 中止或降级 |
| **Charts + Stats**             | Auto-generates return stats + matplotlib price chart |
| **Live News Feed Integration** | RSS + Google News fallback（美股/A 股/港股） |
| **PDF Report Generation**      | Produces clean markdown AND PDF research output |
| ️**Free Data Sources**        |Alpha Vantage + AKShare + Google News RSS/东方财富公告 |
| **FastAPI Endpoint**           | `/analyze` route returns report paths + JSON response |
| **Error Handling**             | Skips unknown symbols, logs edge cases, continues pipeline |
| **Human-In-Loop (Postgres)** | 审批边 interrupt：批准/驳回/修改，状态经 PostgreSQL 持久化可跨进程恢复 |

---

## Tech Stack

| Layer | Tech |
|-------|------|
| Orchestration | LangGraph, LangChain |
| Backend APIs | FastAPI |
| LLM Model | DeepSeek（deepseek-v4-flash）· 可切换 OpenAI |
| Data Tools | Alpha Vantage, AKShare, Google News RSS, 东方财富公告 |
| File Formats | Markdown, JSON, PDF |
| Logging | Rotating log file via `logging` + `TimedRotatingFileHandler` |
| Code Quality | Black, Ruff, Pytest |
| PDF Rendering | fpdf2 (pure Python, no external binaries) |
| Python | 3.10+ |

---
# Agent Responsibilities Matrix
| Agent                        | Primary Function                                                      | Input Dependencies           | Output Artifacts                                  | Tools Used                                        |
| ---------------------------- | --------------------------------------------------------------------- | ---------------------------- | ------------------------------------------------- | ------------------------------------------------- |
| **DataAgent**                | Fetches historical price data, key financial metrics, and recent news | Stock symbol, days           | `raw_data.json`, `prices`, `fundamentals`, `news` | Alpha Vantage, AKShare, Google News RSS/东方财富公告                 |
| **AnalystAgent**             | Interprets data and writes narrative summary                          | DataAgent output             | Analyst Note (text)                               | LLM (DeepSeek/OpenAI), tool calling, LangChain PromptTemplate |
| **ComplianceAgent**          | Validates phrasing and enforces neutral tone                          | Analyst Note                 | Compliant Final Note                              | LLM Compliance Filter, Rule-based Keyword Scanner |
| **SupervisorAgent**          | Merges all content, generates formatted Markdown & PDF report         | Compliant Note, Data Summary | `.md`, `.pdf`, `.png` artifacts                   | fpdf2, matplotlib                                 |
| **Orchestrator (LangGraph)** | Directs data flow and ensures orderly execution                       | All agents                   | End-to-end automated workflow                     | LangGraph + FastAPI integration                   |

---
## Agent Responsibility Model

Each agent in this system has a clearly defined, non-overlapping role:

- **DataAgent**
  - Collects raw market data, fundamentals, and news
  - Produces structured JSON only (no interpretation)

- **AnalystAgent**
  - Interprets price action, fundamentals, and news
  - Produces narrative investment analysis

- **ComplianceAgent**
  - Enforces neutral tone and regulatory-safe phrasing
  - Removes prohibited language and injects disclosures
  - Does not generate new analysis

- **SupervisorAgent**
  - Acts as Editor-in-Chief
  - Decides final report structure
  - Removes incomplete or low-quality sections
  - Produces the final institutional-grade research note
---

## Installation

```bash
git clone https://github.com/ozlyyds04/Multi-Agent-Stock-Research.git
cd Multi-Agent-Stock-Research
uv sync
cp .env.example .env
```
Edit `.env` and add your ALPHA_VANTAGE_API_KEY from [https://www.alphavantage.co/support/#api-key](Alpha Vantage).  
Optionally tune `config/settings.yaml` to select your preferred LLM provider, max_news, strict_mode.

### Run
```bash
# 1) Backend API
uv run uv run uvicorn src.api:app --reload --port 8000

# 2) Web UI
uv run uv run streamlit run src/ui/streamlit_app.py
```

    PDF export uses the built-in fpdf2 renderer (pure Python) — no need to install pandoc / wkhtmltopdf.
---
## ⚠️ API Quota & Strict Mode Behavior

Alpha Vantage free tier allows 25 requests per day (each quote / daily-price call costs 1).
When the limit is exceeded, requests return a rate-limit note; the pre-check skips validation
gracefully and the price cache (6h TTL) helps avoid repeated requests.
In strict mode (default), the pipeline will abort gracefully and log a message such as:

```bash
严格模式中止：缺少关键数据：prices（股票代码：AAPL）。可能原因：数据源接口暂不可用、请求受限，或该股票代码对应的数据缺失。
```
To continue testing even when quotas are reached:
```yaml
StrictMode:
  strict_mode: false
```
Fundamentals and A-share data come from AKShare (free, no key required).

---
## How It Works

Every time you run a stock analysis, four distinct agents collaborate:

1. Data Agent → Fetches prices, fundamentals, and news

2. Analyst Agent → Writes the narrative & market interpretation

3. Compliance Agent → Screens content for restricted words

4. Supervisor Agent → Merges, formats, and publishes outputs

They communicate via a LangGraph state machine and are orchestrated end-to-end through CLI or API.

---
## Architecture Overview

![Architecture Example](docs/Multiagent.svg)

This architecture diagram shows how a stock symbol request flows through a LangGraph-powered multi-agent pipeline — from data collection, AI analysis, and compliance filtering to final report generation and artifact export.
For a deeper explanation, see [`docs/architecture.md`](docs/architecture.md)

---
# PDF Rendering (fpdf2)

The report generator uses a built-in fpdf2 renderer (pure Python): headings, lists,
bold text, links, the embedded price chart and page numbers are laid out programmatically
with a CJK font (e.g. Microsoft YaHei). No pandoc or wkhtmltopdf installation is required.

The exporter is pure Python (fpdf2), so PDFs work on any machine without extra binaries.

---
### Usage (CLI)
```bash
python -m src.cli --symbol AAPL --days 10 --outdir artifacts
```
### Sample CLI Output:
CLI prints the generated report / plot / pdf paths to stdout.
---
## REST API (FastAPI)
### Start the server:
```bash
uv run uvicorn src.api:app --reload --port 8000
```
### Example call:
```
curl -X POST http://127.0.0.1:8000/analyze \
-H "Content-Type: application/json" \
-d '{"symbol":"META","days":10}'
```
### Sample API Output:
The API returns report paths + JSON (see the curl example above).
---
### Sample Chart Output:
Price charts are generated per run under `artifacts/<SYMBOL>/<SYMBOL>_chart.png` (see the UI Report tab).
---
## Results

The system has been tested on multiple tickers (AAPL, MSFT, META) across 7–10 day ranges, consistently generating complete reports that include:

1. Snapshots.
2. Fundamental Highlights
3. Recent News Headlines
4. Analyst Commentary
5. Methodology
6. Execution Metadata
7. Price chart and statistics.
8. Exported .pdf and .json artifacts

Each run executes with a live LLM API key, making it practical for near-real-time research report automation and  produces a complete report folder under artifacts / SYMBOL, containing:

```bash
AAPL_<date>_report.md
AAPL_<date>_report.pdf
AAPL_chart.png
AAPL_raw.json
```
---

## User Interface 
### Interactive web application using Streamlit
 - Streamlit frontend abstracts away backend complexity and calls FastAPI /analyze.
### Clear user guidance
- UI includes usage tips (example tickers, quota guidance, PDF export prerequisites).
### Error messaging
- Backend returns structured JSON errors (status=error, reason, optional suggested_action) surfaced to users.
### Run UI
```bash
uv run streamlit run src/ui/streamlit_app.py

```
### Sample UI Output:
![UI Example](docs/Screenshots/UI.png)
---
## Resilience & Monitoring 

### Retry logic with exponential backoff

- Implemented via src/utils/resilience.py and used in:
   - src/tools/fundamentals_tool.py (transient HTTP status retries)
   - src/tools/price_tool.py (transient Alpha Vantage failures)
### Timeout handling

 - Per-attempt timeouts enforced in retry wrapper.
 - Global workflow timeout enforced in orchestrator (ThreadPoolExecutor + fut.result(timeout=...)).
### Loop limits / iteration caps
- Orchestration graph is a finite DAG with fixed nodes (no unbounded loops by design).
### Graceful handling of agent failures
- AnalystAgent/ComplianceAgent failures fall back to safe defaults while preserving report generation.
- SupervisorAgent failures fall back to compliant text.
### Traceability
- Run-level context (run_id/symbol) is logged for correlating failures, retries, and fallback events.
---
## Logging, Maintenance, and Operations

- Logs are written to:
  - Console output (developer visibility)
  - Rotating file: logs/app.log (rotation enabled; UTF-8 encoding)

- Each pipeline run logs a unique run_id to support debugging across retries/failures.

- Recommended maintenance:
  - Keep RSS sources current (some feeds may return 404/429; retries are applied, but replace dead feeds).
  - If strict-mode is enabled, ensure your RSS sources and fundamentals provider are stable to avoid aborts.
  -  PDF export is built-in (fpdf2), so no external PDF tools are required.
---
## Tests & Quality Assurance
### Comprehensive Testing Suite
- Unit tests cover core tools and agent behavior (data fetchers, agent output handling, strict-mode failure paths).
- Integration tests validate the orchestrator flow (agent-to-agent state passing, strict-mode abort, graceful degradation).
- End-to-end smoke tests exercise complete workflows via CLI/API entrypoints using mocks (to avoid external API dependency).
- Coverage reporting is enabled via pytest-cov for core modules.
### Run tests + coverage
```bash
# 运行全部离线测试 + 覆盖率（阈值 70%，pytest.ini 已配置）
uv run pytest tests --ignore=tests/e2e
```
### Run a Single Test File
```bash
uv run pytest tests/unit/agents/test_analyst_agent.py -v
```
> `tests/e2e` 依赖真实外部 API（行情/新闻），默认不纳入离线套件。
---
## Safety & Security Guardrails 

#### Input validation / sanitization
 - Central request validation via validate_request() (symbol, days, outdir).
 - Ticker validity pre-check via Alpha Vantage before pipeline execution (rejects invalid/delisted symbols).
#### Output filtering / content safety
 - Neutrality enforcement via enforce_neutrality() and forbidden phrase filtering in the ComplianceAgent.
 - ComplianceAgent explicitly rewrites to remove prohibited claims and ensure regulatory-safe language.
#### Graceful error handling
 - Strict-mode abort produces structured, user-facing errors with suggested actions.
 - Non-strict mode proceeds with warnings and “N/A placeholders” where needed.
#### Logging for compliance/debug
 - Consistent structured logging across tools, agents, and orchestrator (including warnings/fallbacks).
---
## Contributing
PRs are welcome! Whether you're fixing a bug, improving PDF formatting, or adding a new tool — open a PR and let's build better agent workflows together.



---

# 中文版（全文翻译）| Chinese Version

# 多智能体股票研究（基于 LangChain + LangGraph）

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![LangChain](https://img.shields.io/badge/langchain-1.3-purple.svg)](https://github.com/langchain-ai/langchain)
![LLM](https://img.shields.io/badge/Orchestrator-Langgraph-red)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-ready-brightgreen.svg)](https://fastapi.tiangolo.com)
[![LLM](https://img.shields.io/badge/LLM-DeepSeek-black.svg)](https://www.deepseek.com)
![AIEngineering](https://img.shields.io/badge/Discipline-AI_Engineering-purple)
![LangChain](https://img.shields.io/badge/Framework-LangChain-orange)
![LLM](https://img.shields.io/badge/Model-LLM-black)
![LLM](https://img.shields.io/badge/Data-Agent-blue)
![LLM](https://img.shields.io/badge/Analyst-Agent-lightgreen)
![LLM](https://img.shields.io/badge/Compliance-Agent-red)
![LLM](https://img.shields.io/badge/Supervisor-Agent-orange)
---

## 这个项目为什么存在

金融研究的世界已经发生了演变——分析师现在依赖自动化、AI 和实时数据，而不是 Excel 表格和手工基本面分析。
本项目演示了一个**最小可投入生产的多智能体研究系统**，使用 **LangChain + LangGraph** 构建，旨在自动化：

- 历史股价分析
- 基本面数据获取
- 新闻跟踪
- 分析师风格的研究报告生成
- 合规过滤
- PDF + Markdown 研究报告（含图表）

无论你是开发者、量化研究员、分析师还是 AI 爱好者——本仓库展示了如何构建**真实的、由 LLM 驱动的工作流**，无需付费 API 数据源即可生成机构级研究报告。

---

## 主要功能

| 功能 | 说明 |
|------|------|
| **4 智能体架构** | DataAgent、AnalystAgent、ComplianceAgent、SupervisorAgent |
| **智能体工具调用** | 分析智能体在数据缺口下自主调用行情/基本面工具补充数据 |
| **条件路由 + 修复** | 数据缺失自动进入修复节点补充，失败时按 strict_mode 中止或降级 |
| **图表 + 统计** | 自动生成收益率统计 + matplotlib 股价图 |
| **实时新闻源集成** | RSS + Google News 兜底（美股/A 股/港股） |
| **PDF 报告生成** | 输出整洁的 Markdown 和 PDF 研究报告 |
| **免费数据源** | Alpha Vantage + AKShare + Google News RSS/东方财富公告 |
| **FastAPI 接口** | `/analyze` 路由返回报告路径 + JSON 响应 |
| **错误处理** | 跳过未知股票代码、记录边界情况、继续执行流水线 |
| **人在回路（Postgres）** | 审批边 interrupt：批准/驳回/修改，状态经 PostgreSQL 持久化可跨进程恢复 |

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 编排 | LangGraph、LangChain |
| 后端 API | FastAPI |
| LLM 模型 | DeepSeek（deepseek-v4-flash）· 可切换 OpenAI |
| 数据工具 | Alpha Vantage、AKShare、Google News RSS、东方财富公告 |
| 文件格式 | Markdown、JSON、PDF |
| 日志 | 通过 `logging` + `TimedRotatingFileHandler` 轮转日志文件 |
| 代码质量 | Black、Ruff、Pytest |
| PDF 渲染 | fpdf2（纯 Python，无外部依赖） |
| Python | 3.10+ |

---
# 智能体职责矩阵
| 智能体 | 主要职责 | 输入依赖 | 输出产物 | 使用工具 |
|--------|----------|----------|----------|----------|
| **DataAgent** | 获取历史价格数据、关键财务指标和最新新闻 | 股票代码、天数 | `raw_data.json`、`prices`、`fundamentals`、`news` | Alpha Vantage、AKShare、Google News RSS、东方财富公告 |
| **AnalystAgent** | 解读数据并撰写叙述性摘要 | DataAgent 输出 | 分析师备注（文本） | LLM（DeepSeek/OpenAI）、工具调用、LangChain PromptTemplate |
| **ComplianceAgent** | 校验表述并执行中性语气 | 分析师备注 | 合规最终备注 | LLM 合规过滤器、基于规则的关键词扫描器 |
| **SupervisorAgent** | 汇总所有内容，生成格式化 Markdown 和 PDF 报告 | 合规备注、数据摘要 | `.md`、`.pdf`、`.png` 产物 | fpdf2、matplotlib |
| **编排器（LangGraph）** | 指挥数据流并确保有序执行 | 所有智能体 | 端到端自动化工作流 | LangGraph + FastAPI 集成 |

---
## 智能体职责模型

系统中的每个智能体都有明确、不重叠的职责：

- **DataAgent**
  - 收集原始市场数据、基本面和新闻
  - 只生成结构化 JSON（不做解读）

- **AnalystAgent**
  - 解读价格走势、基本面和新闻
  - 生成叙述性的投资分析

- **ComplianceAgent**
  - 执行中性语气和符合监管的措辞
  - 删除被禁止的语言并注入披露声明
  - 不生成新的分析

- **SupervisorAgent**
  - 担任总编辑
  - 决定最终报告结构
  - 删除不完整或低质量的章节
  - 生成最终的机构级研究报告
---

## 安装

```bash
git clone https://github.com/ozlyyds04/Multi-Agent-Stock-Research.git
cd Multi-Agent-Stock-Research
uv sync
cp .env.example .env
```
编辑 `.env` 文件，从 [https://www.alphavantage.co/support/#api-key](Alpha Vantage) 获取并填入你的 ALPHA_VANTAGE_API_KEY。
也可以调整 `config/settings.yaml` 来选择偏好的 LLM 提供方、max_news、strict_mode。

### 启动
```bash
# 1) 后端 API
uv run uv run uvicorn src.api:app --reload --port 8000

# 2) Web UI
uv run uv run streamlit run src/ui/streamlit_app.py
```

    PDF 导出使用内置的 fpdf2 渲染引擎（纯 Python），无需安装 pandoc / wkhtmltopdf。
---
## API 配额与严格模式行为

Alpha Vantage 免费版每天限 25 次请求（每次行情/日线查询各计 1 次）。
超过限制后请求会返回限流提示；预检会优雅跳过校验，本地价格缓存（6 小时 TTL）可避免重复请求。
在严格模式（默认）下，流水线会优雅中止并记录如下消息：

```bash
严格模式中止：缺少关键数据：prices（股票代码：AAPL）。可能原因：数据源接口暂不可用、请求受限，或该股票代码对应的数据缺失。
```

即使配额用尽也想继续测试，可以设置：
```yaml
StrictMode:
  strict_mode: false
```
基本面和 A 股数据来自 AKShare（免费、无需 Key）。

---
## 工作原理

每次运行股票分析时，四个不同的智能体协同工作：

1. 数据智能体 → 获取价格、基本面和新闻
2. 分析智能体 → 撰写叙述和市场解读
3. 合规智能体 → 筛查内容中的受限词语
4. 主管智能体 → 合并、格式化并发布输出

它们通过 LangGraph 状态机通信，并通过 CLI 或 API 进行端到端编排。

---
## 架构概览

![架构示例](docs/Multiagent.svg)

该架构图展示了股票代码请求如何流经 LangGraph 驱动的多智能体流水线——从数据收集、AI 分析、合规过滤到最终报告生成和产物导出。
更深入的解释请参见 [`docs/architecture.md`](docs/architecture.md)

---
# PDF 渲染（fpdf2）

PDF 导出使用内置的 fpdf2 渲染引擎（纯 Python）：标题、列表、加粗、链接、价格图表和页码均由程序布局生成，并使用中文字体（如微软雅黑）。无需安装 pandoc / wkhtmltopdf。

PDF 导出为纯 Python 实现（fpdf2），任何机器上无需额外程序即可生成 PDF。

---
### 用法（CLI）
```bash
python -m src.cli --symbol AAPL --days 10 --outdir artifacts
```
### CLI 输出示例：
CLI 会在标准输出打印生成的报告 / 图表 / PDF 路径。
---
## REST API（FastAPI）
### 启动服务：
```bash
uv run uvicorn src.api:app --reload --port 8000
```
### 调用示例：
```
curl -X POST http://127.0.0.1:8000/analyze \
-H "Content-Type: application/json" \
-d '{"symbol":"META","days":10}'
```
### API 输出示例：
API 返回报告路径 + JSON（见上方 curl 示例）。
---
### 图表输出示例：
价格图表在每次运行时生成于 `artifacts/<SYMBOL>/<SYMBOL>_chart.png`（见 UI 的“报告”标签）。
---
## 结果

该系统已在多个股票代码（AAPL、MSFT、META）上、跨越 7–10 天的时间范围进行测试，持续生成包含以下内容的完整报告：

1. 概览。
2. 基本面亮点
3. 近期新闻头条
4. 分析师评论
5. 方法论
6. 执行元数据
7. 价格图表和统计。
8. 导出的 .pdf 和 .json 产物

每次运行都会在 artifacts / SYMBOL 下生成一个完整的报告文件夹，包含：

```bash
AAPL_<date>_report.md
AAPL_<date>_report.pdf
AAPL_chart.png
AAPL_raw.json
```
---

## 用户界面
### 使用 Streamlit 的交互式 Web 应用
 - Streamlit 前端屏蔽了后端复杂性，并调用 FastAPI /analyze。
### 清晰的用户引导
- UI 包含使用提示（示例股票代码、配额指引、PDF 导出前置条件）。
### 错误消息
- 后端返回结构化 JSON 错误（status=error、reason、可选的 suggested_action），并呈现给用户。
### 运行 UI
```bash
uv run streamlit run src/ui/streamlit_app.py

```
### UI 输出示例：
![UI 示例](docs/Screenshots/UI.png)
---
## 韧性与监控

### 带指数退避的重试逻辑

- 通过 src/utils/resilience.py 实现，用于：
   - src/tools/fundamentals_tool.py（瞬时 HTTP 状态码重试）
   - src/tools/price_tool.py（Alpha Vantage 瞬时失败）
### 超时处理

 - 重试包装器中强制执行每次尝试的超时。
 - 编排器中强制执行全局工作流超时（ThreadPoolExecutor + fut.result(timeout=...)）。
### 循环限制/迭代上限
- 编排图是一个节点固定的有限 DAG（设计上无无限循环）。
### 智能体失败的优雅处理
- AnalystAgent/ComplianceAgent 失败时回退到安全默认值，同时保留报告生成。
- SupervisorAgent 失败时回退到合规文本。
### 可追溯性
- 记录运行级上下文（run_id/symbol），用于关联失败、重试和回退事件。
---
## 日志、维护与运维

- 日志写入：
  - 控制台输出（便于开发者查看）
  - 轮转文件：logs/app.log（已启用轮转；UTF-8 编码）

- 每次流水线运行都会记录唯一的 run_id，以支持跨重试/失败的调试。

- 建议的维护：
  - 保持 RSS 源最新（部分源可能返回 404/429；已应用重试，但应替换失效的源）。
  - 如果启用了严格模式，请确保 RSS 源和基本面数据提供方稳定，以避免中止。
  - PDF 导出为内置功能（fpdf2），无需外部 PDF 工具。
---
## 测试与质量保证
### 全面测试套件
- 单元测试覆盖核心工具和智能体行为（数据获取器、智能体输出处理、严格模式失败路径）。
- 集成测试验证编排器流程（智能体间状态传递、严格模式中止、优雅降级）。
- 端到端冒烟测试通过 mock 经 CLI/API 入口执行完整工作流（避免外部 API 依赖）。
- 通过 pytest-cov 为核心模块启用覆盖率报告。
### 运行测试 + 覆盖率
```bash
# 运行全部离线测试 + 覆盖率（阈值 70%，pytest.ini 已配置）
uv run pytest tests --ignore=tests/e2e
```
### 运行单个测试文件
```bash
uv run pytest tests/unit/agents/test_analyst_agent.py -v
```
> `tests/e2e` 依赖真实外部 API（行情/新闻），默认不纳入离线套件。
---
## 安全与合规护栏

#### 输入校验/清洗
 - 通过 validate_request() 集中校验请求（symbol、days、outdir）。
 - 流水线执行前通过 Alpha Vantage 预检股票代码有效性（拒绝无效/已退市代码）。
#### 输出过滤/内容安全
 - 通过 enforce_neutrality() 和 ComplianceAgent 中的禁用短语过滤执行中性要求。
 - ComplianceAgent 明确改写以删除被禁止的主张，并确保符合监管的语言。
#### 优雅的错误处理
 - 严格模式中止会产生结构化、面向用户的错误并附带建议操作。
 - 非严格模式在需要处以警告和"N/A 占位符"继续执行。
#### 用于合规/调试的日志
 - 在工具、智能体和编排器中保持一致的结构化日志（包括警告/回退）。
---
## 参与贡献
欢迎提交 PR！无论你是在修复 bug、改进 PDF 格式，还是添加新工具——提交 PR，一起构建更好的智能体工作流。



