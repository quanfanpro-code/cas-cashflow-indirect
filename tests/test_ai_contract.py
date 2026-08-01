from copy import deepcopy
from importlib import import_module

import pandas as pd
import pytest


ai_contract = import_module("cashflow_indirect.ai_contract")


@pytest.fixture
def ai_request():
    return {
        "request_id": "ai-1",
        "fragment_id": "f1",
        "business_event_id": "event-1",
        "candidate_items": [6, 7],
        "candidate_rules": [
            {"item_no": 6, "rule_id": "NCA-DISPOSAL"},
            {"item_no": 7, "rule_id": "NCA-SCRAP"},
        ],
        "available_amount_minor": 250000,
        "evidence_ids": ["ev-1", "ev-2"],
        "performance_materiality_minor": 100000,
    }


def valid_decision(ai_request):
    return {
        "request_id": ai_request["request_id"],
        "recommended_item": 6,
        "recommended_status": "included",
        "recommended_amount_minor": 250000,
        "recommendation": "作为非金融长期资产处置损失计入第6项",
        "reason": "资产类别和损益科目均指向长期资产处置",
        "supporting_evidence_ids": ai_request["evidence_ids"],
        "contrary_evidence": [],
        "rejected_alternatives": [
            {"item_no": 7, "reason": "没有毁损、报废或盘亏证据"}
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


def test_ai_decision_requires_one_final_item_and_reason(ai_request):
    result = ai_contract.validate_ai_decisions(
        [ai_request],
        [valid_decision(ai_request)],
    )

    assert result.loc[0, "recommended_item"] == 6
    assert not result.loc[0, "human_review_required"]
    assert result.loc[0, "reason"]


def test_material_strong_conflict_keeps_ai_advice_and_escalates(ai_request):
    decision = valid_decision(ai_request)
    decision["strong_conflict"] = True
    decision["human_review_required"] = True
    decision["contrary_evidence"] = ["存在毁损审批单，但资产处置损益摘要不一致"]

    result = ai_contract.validate_ai_decisions([ai_request], [decision])

    assert result.loc[0, "human_review_required"]
    assert result.loc[0, "recommendation"]
    assert result.loc[0, "reason"]


@pytest.mark.parametrize("amount", [250001, -250001])
def test_ai_decision_rejects_amount_above_fragment(ai_request, amount):
    bad = valid_decision(ai_request)
    bad["recommended_amount_minor"] = amount

    with pytest.raises(ai_contract.AiDecisionError, match="超过可用金额"):
        ai_contract.validate_ai_decisions([ai_request], [bad])


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"reason": ""}, "理由"),
        ({"recommended_item": 10}, "候选项目"),
        ({"supporting_evidence_ids": ["unknown"]}, "未知证据"),
        ({"confidence": 1.1}, "置信度"),
        ({"human_review_required": True}, "人工升级"),
    ],
)
def test_ai_decision_rejects_invalid_content(ai_request, change, message):
    bad = valid_decision(ai_request)
    bad.update(change)

    with pytest.raises(ai_contract.AiDecisionError, match=message):
        ai_contract.validate_ai_decisions([ai_request], [bad])


def test_ai_decision_requires_reasons_for_rejected_alternatives(ai_request):
    bad = valid_decision(ai_request)
    bad["rejected_alternatives"] = []

    with pytest.raises(ai_contract.AiDecisionError, match="备选分类"):
        ai_contract.validate_ai_decisions([ai_request], [bad])


def test_ai_decision_rejects_boolean_cfo_impact(ai_request):
    bad = valid_decision(ai_request)
    bad["cfo_impact_minor"] = True

    with pytest.raises(ai_contract.AiDecisionError, match="经营活动现金流量影响"):
        ai_contract.validate_ai_decisions([ai_request], [bad])


def test_ai_contract_rejects_duplicate_request_and_decision(ai_request):
    with pytest.raises(ai_contract.AiDecisionError, match="重复请求"):
        ai_contract.validate_ai_decisions(
            [ai_request, deepcopy(ai_request)],
            [valid_decision(ai_request)],
        )

    with pytest.raises(ai_contract.AiDecisionError, match="重复AI结论"):
        ai_contract.validate_ai_decisions(
            [ai_request],
            [valid_decision(ai_request), valid_decision(ai_request)],
        )


def test_build_ai_request_contains_only_relevant_fragment_and_evidence():
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f1",
                "business_event_id": "event-1",
                "amount_minor": 250000,
                "direction_sign": -1,
                "voucher_id": "v1",
                "voucher_date": "2026-03-31",
                "summary": "处置固定资产",
                "account_path": "资产处置损益",
                "counterparty_level1": "固定资产清理",
                "whole_ledger_secret": "不得进入AI请求",
            }
        ]
    )
    candidates = pd.DataFrame(
        [
            {"fragment_id": "f1", "item_no": 6, "rule_id": "NCA-DISPOSAL"},
            {"fragment_id": "f1", "item_no": 7, "rule_id": "NCA-SCRAP"},
        ]
    )
    evidence = pd.DataFrame(
        [
            {
                "fragment_id": "f1",
                "evidence_id": "ev-1",
                "evidence_type": "account",
                "content": "固定资产转入清理",
                "source_refs": [{"source_row": 10}],
            },
            {
                "fragment_id": "other",
                "evidence_id": "ev-other",
                "evidence_type": "account",
                "content": "无关凭证",
                "source_refs": [{"source_row": 999}],
            },
        ]
    )

    requests = ai_contract.build_ai_requests(
        fragments,
        candidates,
        evidence,
        performance_materiality_minor=100000,
    )

    assert len(requests) == 1
    assert requests[0]["candidate_items"] == [6, 7]
    assert requests[0]["evidence_ids"] == ["ev-1"]
    assert requests[0]["available_amount_minor"] == 250000
    assert "whole_ledger_secret" not in str(requests[0])
