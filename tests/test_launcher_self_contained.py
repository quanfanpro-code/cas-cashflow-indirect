import json
from pathlib import Path
import subprocess
import sys


技能目录 = Path(__file__).resolve().parents[1]


def test_启动器自检报告技能内资源():
    结果 = subprocess.run(
        [
            sys.executable,
            str(技能目录 / "scripts" / "engine_launcher.py"),
            "--self-check",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert 结果.returncode == 0, 结果.stderr
    状态 = json.loads(结果.stdout)
    assert 状态["status"] == "ok"
    assert 状态["rule_file_exists"] is True
    assert Path(状态["skill_root"]) == 技能目录
    assert 状态["missing"] == []
    for 依赖 in ("pandas", "pyarrow", "openpyxl", "xlsxwriter"):
        assert 状态["dependencies"][依赖] == "installed"
