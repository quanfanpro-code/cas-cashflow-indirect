"""现金流量表间接法命令入口。"""

import argparse
import json
from pathlib import Path

from .pipeline import finalize_run, get_run_status, prepare_run


def main() -> None:
    parser = argparse.ArgumentParser(description="现金流量表间接法验证引擎")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="读取输入并生成AI请求")
    prepare.add_argument("--config", type=Path, required=True)

    finalize = commands.add_parser("finalize", help="接收AI结论并正式输出")
    finalize.add_argument("--run-dir", type=Path, required=True)
    finalize.add_argument("--ai-decisions", type=Path)

    status = commands.add_parser("status", help="只读取运行状态")
    status.add_argument("--run-dir", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_run(args.config)
    elif args.command == "finalize":
        result = finalize_run(args.run_dir, args.ai_decisions)
    else:
        result = get_run_status(args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
