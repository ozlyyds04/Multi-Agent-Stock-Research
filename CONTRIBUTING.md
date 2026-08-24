# Contributing to Multi-Agent Stock Research Pipeline

Thank you for your interest in contributing to Multi-Agent Stock Research Pipeline!
This project demonstrates a production-ready multi-agent LangGraph workflow for end-to-end equity research — integrating retrieval, compliance filtering, analysis generation, and automated reporting.
We welcome contributions in the form of bug fixes, new features, agent improvements, documentation, and research content enhancements.
---

# How to Get Started

1. **Read the README.md**

   The README covers all the essentials, including:

   - Environment setup (venv, dependencies, .env variables)

   - Running agents (Data, Analyst, Compliance, Supervisor, and Publisher)

   - Building and querying reports via the CLI and FastAPI server

   - Regenerating artifacts (PDF, Markdown, JSON, and Charts)

   - Logging, monitoring, and error handling
   

2. **Fork and Clone**
```bash
git clone https://github.com/ozlyyds04/Multi-Agent-Stock-Research.git
cd Multi-Agent-Stock-Research
```

3. **Create a branch**
```bash
git checkout -b feature/my-feature
```
---
### Guidelines

- Follow PEP8 code style. Run black src tests before committing.

- Keep commits descriptive, e.g.:
     - feat: add sentiment-analysis agent
   
     - fix: handle missing AKShare API response
   
     - docs: update orchestration diagram
   
     - chore: refactor logging with RotatingFileHandler
   
     - Add/update tests for any new feature or bug fix.

- Documentation

  - Update the architecture or flow diagrams in /docs if you modify orchestration logic.

  - Include docstrings for every class, function, and agent.

  - Keep the README and CONTRIBUTING files consistent with your changes.
---
### Typical Areas for Contribution

- Agent Enhancements

  - Improve LangGraph orchestration logic.

  - Add new agents (e.g., “Risk Analyzer Agent,” “Valuation Agent”).

- Performance Optimization

  - Reduce latency in data retrieval (Alpha Vantage, AKShare).

  - Enhance caching or memory management in multi-agent runs.

- Reporting Improvements

  - Extend Report Publisher to support HTML or DOCX exports.

  - Enhance charts or add AI commentary summaries.

- Compliance & Logging

  - Improve keyword filters, redaction logic, or term matching.

  - Refine logging and error alerting mechanisms.

- Documentation & Demos

  - Add architecture diagrams or example runs.

  - Create short video demos or Jupyter notebooks showing workflows.
---
### Submitting Your Work
1. Push your branch to your fork:
```bash
git push origin feature/add-new-agent
```
2. Open a Pull Request (PR) to the main branch.

3. In your PR, include:

   - A summary of your change

   - Screenshots or logs (if relevant)

   - Confirmation that all tests pass locally
---
### Code of Conduct
[CODE OF CONDUCT](CODE_OF_CONDUCT.md).

---

# 中文版（全文翻译）| Chinese Version

# 为多智能体股票研究流水线做贡献

感谢你有兴趣为多智能体股票研究流水线做贡献！
本项目演示了一个可投入生产的多智能体 LangGraph 工作流，用于端到端股票研究——整合检索、合规过滤、分析生成和自动化报告。
我们欢迎以 bug 修复、新功能、智能体改进、文档和研究内容增强等形式做出的贡献。
---

# 如何开始

1. **阅读 README.md**

   README 涵盖了所有要点，包括：

   - 环境搭建（venv、依赖、.env 变量）

   - 运行智能体（数据、分析、合规、主管和发布器）

   - 通过 CLI 和 FastAPI 服务构建和查询报告

   - 重新生成产物（PDF、Markdown、JSON 和图表）

   - 日志、监控和错误处理

2. **Fork 并克隆**
```bash
git clone https://github.com/ozlyyds04/Multi-Agent-Stock-Research.git
cd Multi-Agent-Stock-Research
```

3. **创建分支**
```bash
git checkout -b feature/my-feature
```
---
### 规范

- 遵循 PEP8 代码风格。提交前运行 black src tests。

- 保持提交信息描述清晰，例如：
     - feat: add sentiment-analysis agent

     - fix: handle missing AKShare API response

     - docs: update orchestration diagram

     - chore: refactor logging with RotatingFileHandler

     - 为任何新功能或 bug 修复添加/更新测试。

- 文档

  - 如果修改了编排逻辑，请更新 /docs 中的架构或流程图表。

  - 为每个类、函数和智能体添加 docstring。

  - 保持 README 和 CONTRIBUTING 文件与你的更改一致。
---
### 典型的贡献领域

- 智能体增强

  - 改进 LangGraph 编排逻辑。

  - 添加新智能体（例如"风险分析智能体"、"估值智能体"）。

- 性能优化

  - 降低数据获取（Alpha Vantage、AKShare）的延迟。

  - 增强多智能体运行中的缓存或内存管理。

- 报告改进

  - 扩展报告发布器以支持 HTML 或 DOCX 导出。

  - 增强图表或添加 AI 评论摘要。

- 合规与日志

  - 改进关键词过滤器、编辑逻辑或术语匹配。

  - 优化日志和错误告警机制。

- 文档与演示

  - 添加架构图或示例运行。

  - 创建展示工作流的短视频演示或 Jupyter 笔记本。
---
### 提交你的工作
1. 将你的分支推送到你的 fork：
```bash
git push origin feature/add-new-agent
```
2. 向 main 分支打开一个 Pull Request（PR）。

3. 在你的 PR 中包含：

   - 更改摘要

   - 截图或日志（如相关）

   - 所有测试本地通过的确认
---
### 行为准则
[行为准则](CODE_OF_CONDUCT.md)。
