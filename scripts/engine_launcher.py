"""从当前 Skill 内部源码启动现金流量表间接法引擎。"""

import json
from pathlib import Path
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ["prepare", "finalize", "status"]


def _project_root() -> Path:
    return SKILL_ROOT


def _load_engine() -> Path:
    project_root = _project_root()
    source_root = project_root / "src"
    if not source_root.is_dir():
        raise FileNotFoundError(f"现金流量表引擎源码目录不存在：{source_root}")
    sys.path.insert(0, str(source_root))
    return project_root


REQUIRED_DEPENDENCIES = ["pandas", "pyarrow", "openpyxl", "xlsxwriter"]


def _check_dependencies() -> tuple[dict[str, str], list[str]]:
    import importlib.util

    installed: dict[str, str] = {}
    missing: list[str] = []
    for name in REQUIRED_DEPENDENCIES:
        if importlib.util.find_spec(name) is None:
            missing.append(name)
        else:
            installed[name] = "installed"
    return installed, missing


def _self_check() -> None:
    project_root = _load_engine()
    from cashflow_indirect.pipeline import (  # noqa: PLC0415
        finalize_run,
        get_run_status,
        prepare_run,
    )

    if not all(callable(value) for value in (
        prepare_run,
        finalize_run,
        get_run_status,
    )):
        raise RuntimeError("现金流量表引擎入口不可调用")
    installed, missing = _check_dependencies()
    if missing:
        print(
            json.dumps(
                {
                    "status": "missing_dependencies",
                    "commands": COMMANDS,
                    "skill_root": str(project_root),
                    "rule_file_exists": (
                        project_root / "rules" / "cas_general_v1.json"
                    ).is_file(),
                    "dependencies": installed,
                    "missing": missing,
                    "install_hint": "请让agent执行：pip install "
                    + " ".join(missing),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)
    print(
        json.dumps(
            {
                "status": "ok",
                "commands": COMMANDS,
                "skill_root": str(project_root),
                "rule_file_exists": (
                    project_root / "rules" / "cas_general_v1.json"
                ).is_file(),
                "dependencies": installed,
                "missing": [],
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if sys.argv[1:] == ["--self-check"]:
        _self_check()
        return
    _load_engine()
    from cashflow_indirect.__main__ import main as engine_main  # noqa: PLC0415

    engine_main()


if __name__ == "__main__":
    main()
