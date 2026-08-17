"""18张表审计底稿文档一致性检查。"""

from pathlib import Path

技能目录 = Path(__file__).resolve().parents[1]


def test_正式输出协议描述19张表和全量序时账():
    协议 = (
        技能目录
        / "references"
        / "协议"
        / "现金流量表附表间接法_正式输出协议.md"
    ).read_text(encoding="utf-8-sig")

    assert "19" in 协议 or "十九" in 协议
    assert "附表结果" in 协议
    assert "全量序时账" in 协议
    assert "机器用复核数据" in 协议
    assert "链接" in 协议
    assert "判断轨迹" in 协议


def test_留痕协议包含判断轨迹():
    协议 = (
        技能目录
        / "references"
        / "协议"
        / "现金流量表附表间接法_JSONL计算留痕协议.md"
    ).read_text(encoding="utf-8-sig")

    assert "判断轨迹" in 协议
    assert "decision_trace" in 协议


def test_README用大白话说明19张表查看方法():
    内容 = (技能目录 / "README.md").read_text(encoding="utf-8-sig")

    assert "19" in 内容
    assert "全量序时账" in 内容
    assert "返回" in 内容
    assert "底稿" in 内容
    assert "```" not in 内容
    assert any(
        表情 in 内容
        for 表情 in ("📘", "📂", "🚀", "🔍", "✅", "⚠️")
    )


def test_SKILL要求净利润由用户提供并指向参考资料():
    内容 = (技能目录 / "SKILL.md").read_text(encoding="utf-8")

    assert "净利润" in 内容
    assert "客户" in 内容 or "用户" in 内容
    assert "references" in 内容
    assert "正式输出协议" in 内容
