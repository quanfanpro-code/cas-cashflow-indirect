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


def test_技能说明引导金融企业标志():
    内容 = (技能目录 / "SKILL.md").read_text(encoding="utf-8-sig")

    assert "financial_entity_flag" in 内容
    assert "金融企业" in 内容


def test_正式输入协议与现行交互口径一致():
    协议 = (
        技能目录
        / "references"
        / "协议"
        / "现金流量表附表间接法_正式输入协议.md"
    ).read_text(encoding="utf-8-sig")

    assert "financial_entity_flag" in 协议
    assert "输出目录不通过窗口选择" in 协议
    assert "直接输入绝对路径" in 协议
    assert "3. 输出目录；" not in 协议
