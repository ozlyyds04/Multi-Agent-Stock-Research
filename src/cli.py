import argparse
import os
from src.graph.orchestrator import run_pipeline
from src.guardrails.inputs import DEFAULT_DAYS
from src.utils.logger import get_logger

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="自动股票市场研究（LangGraph 多智能体）")
    parser.add_argument("--symbol", required=True, help="要分析的股票代码（例如 AAPL、MSFT）")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="要分析的过去交易日数量")
    parser.add_argument("--outdir", default="artifacts", help="用于存储产物的输出目录")
    parser.add_argument("--human", default="false", help="启用人工在回路审核（true/false）")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    logger.info("CLI 调用：symbol=%s，days=%d，outdir=%s", symbol, args.days, args.outdir)
    os.makedirs(args.outdir, exist_ok=True)

    res = run_pipeline(args.symbol, args.days, args.outdir, human=(str(args.human).lower() == "true"))
    # 检查响应中是否包含错误
    if isinstance(res, dict) and res.get("status") == "error":
        print(f"\n[错误] {res['reason']}")
        if "suggested_action" in res:
            print(f"建议：{res['suggested_action']}")
        return

    logger.info("CLI 已成功完成 %s 的执行", symbol)

    print("\n== 输出 ==")
    for k, v in res.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
