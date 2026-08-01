from pathlib import Path


技能目录 = Path(__file__).resolve().parents[1]


def test_技能说明引导完整的输入和会计判断流程():
    内容 = (技能目录 / "SKILL.md").read_text(encoding="utf-8-sig")

    assert r"references\准则原文" in 内容
    assert r"references\知识库" in 内容
    assert "财政部当前有效原文" in 内容
    assert "REQUIRED SUB-SKILL" not in 内容
    assert "chen-yiwei-perspective" not in 内容
    assert "客户明确提供或确认" in 内容
    assert "不得根据序时账推算值代填" in 内容
    assert "达到实际执行重要性且存在强冲突证据" in 内容
    assert "唯一首选建议" in 内容
