import json
import importlib.util
from pathlib import Path
import subprocess
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]


def _run(script_name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / script_name),
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_skill_has_only_required_structure():
    assert (SKILL_ROOT / "SKILL.md").is_file()
    assert (SKILL_ROOT / "README.md").is_file()
    assert (SKILL_ROOT / "agents" / "openai.yaml").is_file()
    assert (SKILL_ROOT / "scripts" / "engine_launcher.py").is_file()
    assert (SKILL_ROOT / "scripts" / "select_paths.py").is_file()
    assert (SKILL_ROOT / "src" / "cashflow_indirect").is_dir()
    assert (SKILL_ROOT / "rules" / "cas_general_v1.json").is_file()


def test_engine_launcher_loads_existing_engine_without_installing_package():
    result = _run("engine_launcher.py", "--self-check")

    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["status"] == "ok"
    assert status["commands"] == ["prepare", "finalize", "status"]
    assert status["skill_root"] == str(SKILL_ROOT)
    assert status["rule_file_exists"] is True
    assert status["missing"] == []
    for 依赖 in ("pandas", "pyarrow", "openpyxl", "xlsxwriter"):
        assert status["dependencies"][依赖] == "installed"


def test_engine_launcher_exposes_three_commands():
    result = _run("engine_launcher.py", "--help")

    assert result.returncode == 0, result.stderr
    assert all(
        command in result.stdout
        for command in ("prepare", "finalize", "status")
    )


def test_windows_path_selector_has_noninteractive_self_check():
    result = _run("select_paths.py", "--self-check")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "ok",
        "modes": ["prepare", "run-dir", "ai-decisions"],
        "platform": "Windows",
    }


def test_prepare_selector_explicitly_requests_analyzed_journal(monkeypatch):
    script_path = SKILL_ROOT / "scripts" / "select_paths.py"
    spec = importlib.util.spec_from_file_location("select_paths", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    titles = []

    class Root:
        def destroy(self):
            return None

    def askopenfilename(**kwargs):
        titles.append(kwargs["title"])
        return (
            r"C:\测试\标准序时账.xlsx"
            if len(titles) == 1
            else r"C:\测试\科目余额表.xlsx"
        )

    monkeypatch.setattr(module, "_dialog_root", Root)
    monkeypatch.setattr("tkinter.filedialog.askopenfilename", askopenfilename)
    monkeypatch.setattr(
        "tkinter.filedialog.askdirectory",
        lambda **kwargs: r"C:\测试\输出",
    )

    result = module._select_prepare()

    assert result["status"] == "selected"
    assert titles[0] == "选择经序时账分析器处理的标准序时账Excel"


def test_run_directory_rejects_export_folder_without_run_state(tmp_path):
    script_path = SKILL_ROOT / "scripts" / "select_paths.py"
    spec = importlib.util.spec_from_file_location("select_paths", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    invalid = module._validate_run_dir(str(tmp_path))
    assert invalid == {
        "status": "invalid",
        "field": "run_dir",
        "reason": "所选目录不是可续跑目录，缺少run_state.json",
    }

    (tmp_path / "run_state.json").write_text("{}", encoding="utf-8")
    assert module._validate_run_dir(str(tmp_path)) == {
        "status": "selected",
        "run_dir": str(tmp_path),
    }


def test_skill_instructions_are_runnable_from_any_working_directory():
    content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert r"<技能根目录>\scripts\engine_launcher.py" in content
    assert r"<技能根目录>\scripts\select_paths.py" in content
    assert "chen-yiwei-perspective" in content
    assert "财政部" in content
    assert "达到实际执行重要性且存在强冲突" in content


def test_skill_requires_customer_confirmed_net_profit_before_prepare():
    content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "打开选择窗口前" in content
    assert "客户明确提供或确认" in content
    assert "不得根据序时账推算值代填" in content
    assert "缺少时必须主动询问客户" in content
    assert "input_source_confirmation" in content
