from importlib import import_module

import pandas as pd


occupancy_module = import_module("cashflow_indirect.occupancy")


def make_fragment(fragment_id="f1", amount_minor=10000, processable=True):
    return pd.DataFrame(
        [
            {
                "fragment_id": fragment_id,
                "amount_minor": amount_minor,
                "processable": processable,
            }
        ]
    )


def make_claim(
    item_no,
    amount_minor=10000,
    fragment_id="f1",
    rule_id="RULE-1",
    paired_offset_id=None,
):
    return {
        "fragment_id": fragment_id,
        "item_no": item_no,
        "claim_amount_minor": amount_minor,
        "decision": "included",
        "rule_id": rule_id,
        "paired_offset_id": paired_offset_id,
    }


def test_exclusive_claim_is_committed_once(rule_book):
    claims = pd.DataFrame([make_claim(1)])

    committed, issues = occupancy_module.resolve_claims(
        make_fragment(),
        claims,
        rule_book,
    )

    assert committed["claimed_amount_minor"].sum() == 10000
    assert committed["physical_consumed_minor"].sum() == 10000
    assert committed["remaining_amount_minor"].iloc[-1] == 0
    assert issues.empty


def test_mutually_exclusive_full_claims_are_rejected(rule_book):
    claims = pd.DataFrame(
        [
            make_claim(1, rule_id="IMP-ASSET"),
            make_claim(2, rule_id="IMP-CREDIT"),
        ]
    )

    committed, issues = occupancy_module.resolve_claims(
        make_fragment(),
        claims,
        rule_book,
    )

    assert committed.empty
    assert issues["issue_code"].tolist() == ["EXCLUSIVE_CLAIM_CONFLICT"]


def test_configured_pair_shares_one_physical_consumption(rule_book):
    claims = pd.DataFrame(
        [
            make_claim(3, 10000, rule_id="NCA-DEPRECIATION", paired_offset_id="p1"),
            make_claim(13, -10000, rule_id="WC-INVENTORY", paired_offset_id="p1"),
        ]
    )

    committed, issues = occupancy_module.resolve_claims(
        make_fragment(),
        claims,
        rule_book,
    )

    assert committed["item_no"].tolist() == [3, 13]
    assert committed["adjustment_amount_minor"].tolist() == [10000, -10000]
    assert committed["physical_consumed_minor"].sum() == 10000
    assert committed["remaining_amount_minor"].iloc[-1] == 0
    assert issues.empty


def test_invalid_pair_is_rejected(rule_book):
    claims = pd.DataFrame(
        [
            make_claim(3, 10000, paired_offset_id="p1"),
            make_claim(13, -9000, paired_offset_id="p1"),
        ]
    )

    committed, issues = occupancy_module.resolve_claims(
        make_fragment(),
        claims,
        rule_book,
    )

    assert committed.empty
    assert issues["issue_code"].tolist() == ["INVALID_PAIRED_OFFSET"]


def test_two_normal_items_cannot_share_one_unsplit_fragment(rule_book):
    claims = pd.DataFrame(
        [
            make_claim(9, 5000, rule_id="FIN-NONOPERATING"),
            make_claim(10, 5000, rule_id="FIN-INVESTMENT"),
        ]
    )

    committed, issues = occupancy_module.resolve_claims(
        make_fragment(),
        claims,
        rule_book,
    )

    assert committed.empty
    assert issues["issue_code"].tolist() == ["MULTIPLE_ITEM_CLAIM"]


def test_claim_above_available_amount_is_rejected(rule_book):
    committed, issues = occupancy_module.resolve_claims(
        make_fragment(),
        pd.DataFrame([make_claim(1, 10001)]),
        rule_book,
    )

    assert committed.empty
    assert issues["issue_code"].tolist() == ["CLAIM_EXCEEDS_AVAILABLE"]


def test_unclassified_remainder_is_sent_to_ai_not_other_item(rule_book):
    committed, issues = occupancy_module.resolve_claims(
        make_fragment(),
        pd.DataFrame([make_claim(1, 6000)]),
        rule_book,
    )

    assert committed["remaining_amount_minor"].tolist() == [4000]
    assert committed["item_no"].tolist() == [1]
    assert issues[["issue_code", "remaining_amount_minor", "next_action"]].to_dict(
        "records"
    ) == [
        {
            "issue_code": "UNCLASSIFIED_REMAINDER",
            "remaining_amount_minor": 4000,
            "next_action": "ai_request",
        }
    ]


def test_zero_nonprocessable_fragment_needs_no_claim(rule_book):
    committed, issues = occupancy_module.resolve_claims(
        make_fragment(amount_minor=0, processable=False),
        pd.DataFrame(
            columns=[
                "fragment_id",
                "item_no",
                "claim_amount_minor",
                "decision",
                "rule_id",
                "paired_offset_id",
            ]
        ),
        rule_book,
    )

    assert committed.empty
    assert issues.empty


def test_review_required_claim_still_occupies_and_keeps_escalation(rule_book):
    claims = pd.DataFrame(
        [
            {
                "fragment_id": "f1",
                "item_no": 6,
                "claim_amount_minor": 20000,
                "decision": "review_required",
                "rule_id": "NCA-DISPOSAL",
                "paired_offset_id": None,
            }
        ]
    )

    committed, issues = occupancy_module.resolve_claims(
        make_fragment(amount_minor=20000),
        claims,
        rule_book,
    )

    assert committed["claimed_amount_minor"].sum() == 20000
    assert committed["physical_consumed_minor"].sum() == 20000
    assert committed["decision"].tolist() == ["review_required"]
    assert issues.empty
