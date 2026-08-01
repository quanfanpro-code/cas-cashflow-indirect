"""判断轨迹构建与完整性验证测试。"""

from importlib import import_module

import pandas as pd
import pytest

audit_trace = import_module("cashflow_indirect.audit_trace")


@pytest.fixture
def rule_claims_with_reason():
    return pd.DataFrame(
        [
            {
                "fragment_id": "f1",
                "business_event_id": "event-1",
                "item_no": 1,
                "claim_amount_minor": 20000,
                "decision": "included",
                "rule_id": "IMP-ASSET",
                "reason": "计入当期损益的减值",
                "confidence": 1.0,
                "paired_offset_id": None,
            }
        ]
    )


@pytest.fixture
def committed_claim():
    return pd.DataFrame(
        [
            {
                "fragment_id": "f1",
                "item_no": 1,
                "adjustment_amount_minor": 20000,
                "claimed_amount_minor": 20000,
                "physical_consumed_minor": 20000,
                "remaining_amount_minor": 0,
                "occupancy_type": "exclusive",
                "decision": "included",
                "rule_id": "IMP-ASSET",
                "paired_offset_id": None,
            }
        ]
    )


@pytest.fixture
def source_fragment():
    return pd.DataFrame(
        [
            {
                "fragment_id": "f1",
                "business_event_id": "event-1",
                "amount_minor": 20000,
                "direction_sign": 1,
                "processable": True,
                "source_row": 88,
            }
        ]
    )


@pytest.fixture
def empty_candidates():
    return pd.DataFrame(
        columns=[
            "fragment_id",
            "business_event_id",
            "item_no",
            "rule_id",
        ]
    )


@pytest.fixture
def empty_decisions():
    return pd.DataFrame(
        columns=[
            "request_id",
            "fragment_id",
            "business_event_id",
            "recommended_item",
        ]
    )


@pytest.fixture
def empty_evidence():
    return pd.DataFrame(
        columns=[
            "fragment_id",
            "evidence_id",
            "evidence_type",
            "content",
            "source_refs",
        ]
    )


@pytest.fixture
def empty_bridges():
    return pd.DataFrame(
        columns=[
            "fragment_id",
            "source_dimension",
            "opening_minor",
            "closing_minor",
            "adjustment_minor",
            "final_minor",
        ]
    )


def test_rule_trace_keeps_reason_confidence_and_occupancy(
    rule_claims_with_reason,
    empty_candidates,
    empty_decisions,
    committed_claim,
    source_fragment,
    empty_evidence,
    empty_bridges,
):
    trace = audit_trace.build_decision_trace(
        rule_claims=rule_claims_with_reason,
        ai_candidates=empty_candidates,
        ai_requests=[],
        ai_decisions=empty_decisions,
        committed=committed_claim,
        fragments=source_fragment,
        evidence=empty_evidence,
        family_bridges=empty_bridges,
    )
    row = trace.iloc[0]
    assert row["final_decision"] == "included"
    assert row["decision_reason"] == "计入当期损益的减值"
    assert row["confidence"] == 1.0
    assert row["physical_consumed_minor"] == 20000


@pytest.fixture
def ai_candidate_rows():
    return pd.DataFrame(
        [
            {
                "fragment_id": "f2",
                "business_event_id": "event-2",
                "item_no": 6,
                "rule_id": "NCA-DISPOSAL",
            },
            {
                "fragment_id": "f2",
                "business_event_id": "event-2",
                "item_no": 10,
                "rule_id": "INV-LOSS",
            },
        ]
    )


@pytest.fixture
def ai_request_rows():
    return [
        {
            "request_id": "ai-2",
            "fragment_id": "f2",
            "business_event_id": "event-2",
            "candidate_items": [6, 10],
            "candidate_rules": [
                {"item_no": 6, "rule_id": "NCA-DISPOSAL"},
                {"item_no": 10, "rule_id": "INV-LOSS"},
            ],
            "available_amount_minor": 250000,
            "evidence_ids": ["ev-1"],
            "performance_materiality_minor": 100000,
        }
    ]


@pytest.fixture
def ai_decision_rows():
    return pd.DataFrame(
        [
            {
                "request_id": "ai-2",
                "fragment_id": "f2",
                "business_event_id": "event-2",
                "candidate_items": [6, 10],
                "available_amount_minor": 250000,
                "performance_materiality_minor": 100000,
                "recommended_item": 6,
                "recommended_status": "included",
                "recommended_amount_minor": 250000,
                "recommendation": "作为长期资产处置损失计入第6项",
                "reason": "资产类别和损益科目均指向长期资产处置",
                "supporting_evidence_ids": ["ev-1"],
                "contrary_evidence": [],
                "rejected_alternatives": [
                    {"item_no": 10, "reason": "没有投资协议和投资收益凭证"}
                ],
                "applicable_rule_ids": ["NCA-DISPOSAL"],
                "cfo_impact_minor": 250000,
                "confidence": 0.91,
                "strong_conflict": False,
                "human_review_required": False,
                "model_id": "accounting-model",
                "prompt_template_version": "cashflow-ai-v1",
                "prompt_hash": "a" * 64,
                "response_hash": "b" * 64,
            }
        ]
    )


@pytest.fixture
def ai_committed_rows():
    return pd.DataFrame(
        [
            {
                "fragment_id": "f2",
                "item_no": 6,
                "adjustment_amount_minor": 250000,
                "claimed_amount_minor": 250000,
                "physical_consumed_minor": 250000,
                "remaining_amount_minor": 0,
                "occupancy_type": "exclusive",
                "decision": "ai_classified",
                "rule_id": "NCA-DISPOSAL",
                "paired_offset_id": None,
            }
        ]
    )


@pytest.fixture
def ai_source_fragment():
    return pd.DataFrame(
        [
            {
                "fragment_id": "f2",
                "business_event_id": "event-2",
                "amount_minor": 250000,
                "direction_sign": 1,
                "processable": True,
                "source_row": 77,
            }
        ]
    )


@pytest.fixture
def ai_evidence_rows():
    return pd.DataFrame(
        [
            {
                "fragment_id": "f2",
                "evidence_id": "ev-1",
                "evidence_type": "凭证",
                "content": "固定资产处置审批单显示处置损失250000元",
                "source_refs": "记-002",
            }
        ]
    )


def test_ai_trace_keeps_selected_and_reclassified_candidate_evidence(
    ai_candidate_rows,
    ai_request_rows,
    ai_decision_rows,
    ai_committed_rows,
    ai_source_fragment,
    ai_evidence_rows,
    empty_bridges,
):
    trace = audit_trace.build_decision_trace(
        rule_claims=pd.DataFrame(),
        ai_candidates=ai_candidate_rows,
        ai_requests=ai_request_rows,
        ai_decisions=ai_decision_rows,
        committed=ai_committed_rows,
        fragments=ai_source_fragment,
        evidence=ai_evidence_rows,
        family_bridges=empty_bridges,
    )
    row6 = trace.loc[trace["item_no"].eq(6)].iloc[0]
    row10 = trace.loc[trace["item_no"].eq(10)].iloc[0]
    assert row6["final_decision"] == "included"
    assert row6["recommendation_reason"] == "资产类别和损益科目均指向长期资产处置"
    assert "固定资产处置审批单" in row6["supporting_evidence"]
    assert row6["contrary_evidence"] == "未发现相反证据"
    assert "没有投资协议" in row6["rejected_alternatives"]
    assert row10["final_decision"] == "reclassified"
    assert "候选后改归第6项" in row10["decision_reason"]
    assert row10["recommendation_reason"] == "资产类别和损益科目均指向长期资产处置"
    assert "没有投资协议" in row10["rejected_alternatives"]
    assert row6["model_id"] == "accounting-model"
    assert row6["prompt_hash"] == "a" * 64
    assert row6["response_hash"] == "b" * 64
    assert row6["cfo_impact_minor"] == 250000


def test_ai_exclusion_is_recorded_as_excluded_without_occupancy(
    ai_candidate_rows,
    ai_request_rows,
    ai_decision_rows,
    ai_source_fragment,
    ai_evidence_rows,
    empty_bridges,
):
    decisions = ai_decision_rows.copy()
    decisions.loc[0, "recommended_status"] = "excluded"
    decisions.loc[0, "recommended_amount_minor"] = 0
    decisions.loc[0, "cfo_impact_minor"] = 0

    trace = audit_trace.build_decision_trace(
        rule_claims=pd.DataFrame(),
        ai_candidates=ai_candidate_rows,
        ai_requests=ai_request_rows,
        ai_decisions=decisions,
        committed=pd.DataFrame(),
        fragments=ai_source_fragment,
        evidence=ai_evidence_rows,
        family_bridges=empty_bridges,
    )

    assert trace.loc[trace["item_no"].eq(6), "final_decision"].item() == "excluded"
    audit_trace.validate_decision_trace(trace)



def _trace_row(**overrides):
    row = {
        "trace_id": "trace-1",
        "fragment_id": "f1",
        "business_event_id": "event-1",
        "source_row": 88,
        "item_no": 6,
        "candidate_status": "rule_classified",
        "final_decision": "included",
        "rule_id": "NCA-DISPOSAL",
        "decision_reason": "资产处置损失",
        "confidence": 1.0,
        "recommendation": "",
        "recommendation_reason": "",
        "supporting_evidence": "",
        "contrary_evidence": "未发现相反证据",
        "rejected_alternatives": "",
        "human_review_required": False,
        "claim_amount_minor": 20000,
        "physical_consumed_minor": 20000,
        "remaining_amount_minor": 0,
        "occupancy_type": "exclusive",
        "paired_offset_id": None,
        "recommended_status": "",
        "recommended_amount_minor": 0,
        "cfo_impact_minor": 0,
        "model_id": "",
        "prompt_template_version": "",
        "prompt_hash": "",
        "response_hash": "",
    }
    row.update(overrides)
    return row


def _trace_frame(*rows):
    return pd.DataFrame(list(rows))


def test_validate_rejects_unknown_fragment():
    trace = _trace_frame(_trace_row(fragment_id=""))
    with pytest.raises(ValueError, match="未知金额片段"):
        audit_trace.validate_decision_trace(trace)


def test_validate_rejects_duplicate_trace_id():
    trace = _trace_frame(
        _trace_row(),
        _trace_row(),
    )
    with pytest.raises(ValueError, match="判断轨迹主键重复"):
        audit_trace.validate_decision_trace(trace)


def test_validate_rejects_included_without_final_occupancy():
    trace = _trace_frame(
        _trace_row(occupancy_type="", physical_consumed_minor=0)
    )
    with pytest.raises(ValueError, match="已纳入金额无法关联最终占用"):
        audit_trace.validate_decision_trace(trace)


def test_validate_rejects_candidate_without_final_explanation():
    trace = _trace_frame(
        _trace_row(
            candidate_status="ai_candidate",
            final_decision="reclassified",
            decision_reason="",
        )
    )
    with pytest.raises(ValueError, match="候选项目缺少最终纳入或改归说明"):
        audit_trace.validate_decision_trace(trace)


def test_validate_rejects_unresolved_ai_evidence():
    trace = _trace_frame(
        _trace_row(
            candidate_status="ai_candidate",
            recommendation_reason="资产类别指向长期资产处置",
            supporting_evidence="",
        )
    )
    with pytest.raises(ValueError, match="人工智能证据无法解析"):
        audit_trace.validate_decision_trace(trace)
