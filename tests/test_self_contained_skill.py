from pathlib import Path


技能目录 = Path(__file__).resolve().parents[1]


def test_技能包含本地计算引擎和规则():
    assert (技能目录 / "src" / "cashflow_indirect").is_dir()
    assert (技能目录 / "rules" / "cas_general_v1.json").is_file()


def test_技能包含准则原文和知识库():
    assert (技能目录 / "references" / "准则原文").is_dir()
    assert (技能目录 / "references" / "知识库").is_dir()


def test_正式入口不依赖外部技能或本机固定路径():
    内容 = "\n".join(
        文件.read_text(encoding="utf-8-sig")
        for 文件 in (
            技能目录 / "SKILL.md",
            技能目录 / "scripts" / "engine_launcher.py",
        )
    )

    assert "REQUIRED SUB-SKILL" not in 内容
    assert "chen-yiwei-perspective" not in 内容
    assert "\\Users\\" not in 内容
    assert "/Users/" not in 内容


def test_正式代码不包含外部进程网络或固定用户目录():
    代码 = "\n".join(
        文件.read_text(encoding="utf-8-sig")
        for 目录 in (技能目录 / "src", 技能目录 / "scripts")
        for 文件 in 目录.rglob("*.py")
    )

    for 禁止内容 in (
        "CASHFLOW_NODE_PATH",
        "@oai/artifact-tool",
        "def _write_workbook(",
        "subprocess",
        "Path.home()",
        "requests.get(",
        "httpx.",
        "urlopen(",
    ):
        assert 禁止内容 not in 代码
    assert not (
        技能目录 / "src" / "cashflow_indirect" / "xlsx_builder.mjs"
    ).exists()
