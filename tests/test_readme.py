from pathlib import Path


技能目录 = Path(__file__).resolve().parents[1]


def test_说明书覆盖普通用户需要知道的主要内容():
    内容 = (技能目录 / "README.md").read_text(encoding="utf-8-sig")

    for 主题 in (
        "使用前准备",
        "标准序时账",
        "净利润",
        "重要性水平",
        "开始使用",
        "继续上次任务",
        "运行逻辑",
        "AI建议",
        "人工复核",
        "查看结果",
        "核对失败",
        "常见问题",
    ):
        assert 主题 in 内容

    assert "```" not in 内容
    assert any(
        表情 in 内容
        for 表情 in ("📘", "📂", "🚀", "🔍", "✅", "⚠️")
    )
    assert "文件成功生成不等于正式核对完成" in 内容
    assert "仅诊断、待总对账" in 内容
    assert "补充主表直接法经营活动现金流量净额" in 内容


def test_说明书不向普通用户展示生僻技术缩写():
    内容 = (技能目录 / "README.md").read_text(encoding="utf-8-sig")

    for 禁止词 in ("JSONL", "CLI", "API", "TDD", "pytest", "Parquet"):
        assert 禁止词 not in 内容
