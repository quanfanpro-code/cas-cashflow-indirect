"""使用Windows选择窗口取得现金流量表运行路径。"""

import argparse
import json
import os
from pathlib import Path
import sys


MODES = ["journal", "balance", "run-dir", "ai-decisions"]


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _cancelled(field: str) -> dict:
    return {"status": "cancelled", "field": field}


def _validate_run_dir(run_dir: str) -> dict:
    if not (Path(run_dir) / "run_state.json").is_file():
        return {
            "status": "invalid",
            "field": "run_dir",
            "reason": "所选目录不是可续跑目录，缺少run_state.json",
        }
    return {"status": "selected", "run_dir": run_dir}


def _dialog_root():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    return root


def _select_single_file(kind: str) -> dict:
    from tkinter import filedialog

    if kind == "journal":
        title = "选择经序时账分析器处理的标准序时账Excel"
        field = "journal_path"
    else:
        title = "选择科目余额表Excel"
        field = "trial_balance_path"

    root = _dialog_root()
    try:
        path = filedialog.askopenfilename(
            parent=root,
            title=title,
            filetypes=[("Excel文件", "*.xlsx *.xlsm")],
        )
        return (
            {"status": "selected", field: path}
            if path
            else _cancelled(field)
        )
    finally:
        root.destroy()


def _select_run_dir() -> dict:
    from tkinter import filedialog

    root = _dialog_root()
    try:
        run_dir = filedialog.askdirectory(
            parent=root,
            title="选择已有现金流量表运行目录",
        )
        return _validate_run_dir(run_dir) if run_dir else _cancelled("run_dir")
    finally:
        root.destroy()


def _select_ai_decisions() -> dict:
    from tkinter import filedialog

    root = _dialog_root()
    try:
        path = filedialog.askopenfilename(
            parent=root,
            title="选择AI结论JSONL",
            filetypes=[("JSONL文件", "*.jsonl"), ("所有文件", "*.*")],
        )
        return (
            {"status": "selected", "ai_decisions_path": path}
            if path
            else _cancelled("ai_decisions_path")
        )
    finally:
        root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="现金流量表Windows路径选择器")
    parser.add_argument("mode", choices=MODES, nargs="?")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        _emit({"status": "ok", "modes": MODES, "platform": "Windows"})
        return
    if os.name != "nt":
        raise RuntimeError("路径选择器仅支持Windows")
    if args.mode in ("journal", "balance"):
        result = _select_single_file(args.mode)
    elif args.mode == "run-dir":
        result = _select_run_dir()
    elif args.mode == "ai-decisions":
        result = _select_ai_decisions()
    else:
        parser.error("必须指定journal、balance、run-dir或ai-decisions")
        return
    _emit(result)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
