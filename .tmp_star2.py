import re
from xml.sax.saxutils import escape

XML_PATH = r"D:\AI\multiagent-stock-research\.tmp_star2_unpacked\word\document.xml"


def run(text: str, bold: bool = False) -> str:
    rpr = (
        '<w:rPr><w:rFonts w:hint="eastAsia" w:ascii="微软雅黑" w:hAnsi="微软雅黑" '
        'w:eastAsia="微软雅黑" w:cs="微软雅黑"/>'
        + ("<w:b/><w:bCs/>" if bold else "")
        + '<w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr>'
    )
    return f"<w:r>{rpr}<w:t>{escape(text)}</w:t></w:r>"


def para(children: list, before: bool = False) -> str:
    spacing = '<w:spacing w:after="6" w:line="254" w:lineRule="exact"/>'
    if before:
        spacing = '<w:spacing w:before="30" w:after="6" w:line="254" w:lineRule="exact"/>'
    return f"<w:p><w:pPr>{spacing}</w:pPr>{''.join(children)}</w:p>"


def item(lead: str, body: str) -> str:
    return para([run(lead, True), run(body)])


block1 = (
    para([run("技术栈：", True), run("Python / LangGraph / LangChain / FastAPI / Streamlit / PostgreSQL / Alpha Vantage / AKShare / DeepSeek-v4-flash / fpdf2 / matplotlib")])
    + para([run("项目背景：", True), run("面向金融投研场景，针对研报生成依赖人工采集撰写、耗时且难批量的问题，独立设计并落地一套多智能体研究系统，输入代码即可自动完成行情 / 基本面 / 新闻采集、分析、合规与报告输出，覆盖美股 / 港股 / A 股。")])
    + para([run("主要职责：", True)], before=True)
    + item("1、多智能体编排：", "设计 Data / Analyst / Compliance / Supervisor 四智能体职责分离架构，基于 LangGraph 打通数据采集、修复、校验、分析、合规、发布全流程，并以 Checkpointer 断点恢复（实测跨进程成功）。")
    + item("2、工具调用与人工审批：", "绑定 Function Calling 实现数据缺口自主补数；实现 HITL interrupt 审批（批准 / 驳回 / 修改 ≤3 轮），状态经 PostgreSQL 持久化；设计 strict_mode 中止 / 降级路由。")
    + item("3、工程化与交付：", "基于 FastAPI + Streamlit 搭建可运行前后端，实现指数退避重试、超时熔断、降级 fallback 与运行级日志，自动导出 Markdown / PDF / JSON 研报与价格图表。")
    + para([run("项目成果：", True)], before=True)
    + para([run("在覆盖美股 / 港股 / A 股的 18 只标的端到端跑通，单份研报平均约 40 秒；累计 140 个单元 / 集成测试全绿，核心覆盖率约 70%。实现了“输入代码 → 自动产出完整研报”的功能闭环，并把多智能体协作、Function Calling、断点恢复、人工审批沉淀为可复用的 Agent 工程能力；个人也从“能调 API”成长为“能独立设计并交付一套多智能体系统”。")])
)

block2 = (
    para([run("技术栈：", True), run("Python / LangGraph / LangChain / Qdrant / Streamlit / Deepseek-v4-flash / Qwen3.7-text-embedding / jieba / Tavily")])
    + para([run("项目背景：", True), run("面向知识库问答场景，针对传统 RAG 检索到无关内容仍直接生成、幻觉率高、答案不可信的问题，基于 LangGraph 构建纠正式问答系统，设计“多路召回 → LLM 重排 → 相关性过滤 → 联网兜底 → 严格生成”的可控链路。")])
    + para([run("主要职责：", True)], before=True)
    + item("1、多路召回与重排：", "实现稠密向量 + jieba 关键词稀疏向量经 RRF 融合取 12 条候选，再由 LLM 打分重排取 Top-5，兼顾语义与字面 / 术语匹配，降低无效上下文污染。")
    + item("2、纠错与生成：", "基于 LangGraph 编排相关性评分、查询改写、Tavily 联网兜底与严格生成；支持多知识库隔离、批量入库 + 进度条、指数退避重试与编码回退。")
    + item("3、评测体系：", "自建 47 条问答评测集，用 LLM-as-judge 对基线与纠错 RAG 做检索精度、命中率、忠实度、相关性四维对比与失败分析。")
    + para([run("项目成果：", True)], before=True)
    + para([run("在 20 题 V3 评测集上，相比“纯向量检索 + 直接生成”基线：幻觉率从 10% 降至 0%、事实正确率从 90% 提升到 95%、检索精度从 0.73 提升到 0.98（命中率 1.0）。沉淀了“评测 → 失败分析 → 迭代”闭环，能通过日志 / Badcase 定位并解决拒答、API 失败等真实问题；系统掌握了混合检索、重排、相关性过滤与 LLM-as-judge 评测方法，建立“先评测再优化”的工程直觉。")])
)

with open(XML_PATH, encoding="utf-8") as f:
    s = f.read()

paras = re.findall(r"<w:p\b.*?</w:p>", s, re.S)
assert len(paras) == 28, f"unexpected {len(paras)}"

new_paras = paras[0:10] + [block1] + paras[18:20] + [block2]

first_p = s.find("<w:p", s.find("<w:body>"))
sect = s.find("<w:sectPr", first_p)
last_p_end = s.rfind("</w:p>", first_p, sect) + len("</w:p>")

with open(XML_PATH, "w", encoding="utf-8") as f:
    f.write(s[:first_p] + "".join(new_paras) + s[last_p_end:])

print("rebuilt STAR v2")
