import pandas as pd
import pytest

from cashflow_indirect.reconciliation import (
    calculate_reconciliation_inputs,
    calculate_summary,
    reconcile_all,
)


def sample_occupancy():
    return pd.DataFrame(
        [
            {
                "fragment_id": "f1",
                "item_no": 1,
                "adjustment_amount_minor": 100000,
                "decision": "included",
            },
            {
                "fragment_id": "f2",
                "item_no": 3,
                "adjustment_amount_minor": 80000,
                "decision": "included",
            },
            {
                "fragment_id": "f3",
                "item_no": 9,
                "adjustment_amount_minor": 50000,
                "decision": "ai_classified",
            },
            {
                "fragment_id": "f4",
                "item_no": 13,
                "adjustment_amount_minor": -20000,
                "decision": "paired_offset",
            },
            {
                "fragment_id": "f5",
                "item_no": 16,
                "adjustment_amount_minor": 40000,
                "decision": "review_required",
            },
        ]
    )


def test_indirect_cash_flow_equals_profit_plus_sixteen_items(rule_book):
    summary = calculate_summary(1_000_000, sample_occupancy(), rule_book)

    assert summary.query("item_no == 0")["amount_minor"].item() == 1_000_000
    assert summary.query("item_no > 0")["amount_minor"].sum() == 250_000
    result = reconcile_all(summary, direct_cfo_minor=1_250_000)
    assert result["indirect_cfo_minor"] == 1_250_000
    assert result["difference_minor"] == 0
    assert result["status"] == "pass"


def test_summary_always_contains_all_sixteen_items(rule_book):
    summary = calculate_summary(500000, pd.DataFrame(), rule_book)

    assert summary["item_no"].tolist() == list(range(17))
    assert summary.query("item_no > 0")["amount_minor"].eq(0).all()
    assert summary.query("item_no > 0")["detail_count"].eq(0).all()


def test_summary_uses_only_committed_occupancy_metadata(rule_book):
    summary = calculate_summary(1_000_000, sample_occupancy(), rule_book)

    item9 = summary.query("item_no == 9").iloc[0]
    item16 = summary.query("item_no == 16").iloc[0]
    assert item9["ai_amount_minor"] == 50000
    assert item16["review_amount_minor"] == 40000
    assert item9["detail_count"] == 1


def test_reconciliation_reports_each_failed_check_without_plugging_difference(
    rule_book,
):
    summary = calculate_summary(1_000_000, sample_occupancy(), rule_book)

    result = reconcile_all(
        summary,
        direct_cfo_minor=1_200_000,
        voucher_balance_difference_minor=100,
        journal_trial_balance_difference_minor=-200,
        occupancy_issue_count=1,
        exclusive_conflict_count=0,
        balance_bridge_difference_minor=300,
        deferred_tax_difference_minor=400,
    )

    assert result["difference_minor"] == 50000
    assert result["status"] == "fail"
    assert {
        check["check_id"]
        for check in result["checks"]
        if check["status"] == "fail"
    } == {
        "voucher_balance",
        "journal_trial_balance",
        "amount_occupancy",
        "balance_bridge",
        "deferred_tax",
        "direct_indirect_cfo",
    }
    assert "plug" not in str(result).lower()


def test_summary_rejects_unknown_item_instead_of_silently_dropping(rule_book):
    occupancy = pd.DataFrame(
        [
            {
                "fragment_id": "bad",
                "item_no": 17,
                "adjustment_amount_minor": 1,
                "decision": "included",
            }
        ]
    )

    with pytest.raises(ValueError, match="项目编号"):
        calculate_summary(0, occupancy, rule_book)


def test_reconciliation_inputs_are_calculated_from_frozen_evidence():
    journal = pd.DataFrame(
        [
            {
                "voucher_id": "v1",
                "account_code": "1001",
                "debit_minor": 100,
                "credit_minor": 100,
            }
        ]
    )
    trial_balance = pd.DataFrame(
        [
            {
                "account_code": "1001",
                "period_debit_minor": 90,
                "period_credit_minor": 100,
            }
        ]
    )
    committed = pd.DataFrame(
        [
            {"item_no": 11, "adjustment_amount_minor": 100},
            {"item_no": 12, "adjustment_amount_minor": 100},
            {"item_no": 13, "adjustment_amount_minor": 1100},
            {"item_no": 14, "adjustment_amount_minor": 2200},
            {"item_no": 15, "adjustment_amount_minor": 1500},
        ]
    )
    occupancy_issues = pd.DataFrame(
        [
            {"issue_code": "EXCLUSIVE_CLAIM_CONFLICT"},
            {"issue_code": "REFERENCE_ERROR"},
        ]
    )
    bridges = pd.DataFrame(
        [
            {
                "family": "working_capital",
                "record_type": "summary",
                "item_no": 13,
                "final_amount_minor": 1800,
            },
            {
                "family": "working_capital",
                "record_type": "summary",
                "item_no": 14,
                "final_amount_minor": 2200,
            },
            {
                "family": "working_capital",
                "record_type": "summary",
                "item_no": 15,
                "final_amount_minor": 1500,
            },
            {
                "family": "noncash_assets",
                "record_type": "pairing",
                "target_item_no": 13,
                "amount_minor": 700,
            },
            {
                "family": "deferred_tax",
                "account_type": "deferred_tax_asset",
                "profit_or_loss_effect_minor": 100,
            },
            {
                "family": "deferred_tax",
                "account_type": "deferred_tax_liability",
                "profit_or_loss_effect_minor": 100,
            },
        ]
    )

    assert calculate_reconciliation_inputs(
        journal,
        trial_balance,
        committed,
        occupancy_issues,
        bridges,
    ) == {
        "voucher_balance_difference_minor": 0,
        "journal_trial_balance_difference_minor": 10,
        "occupancy_issue_count": 2,
        "exclusive_conflict_count": 1,
        "balance_bridge_difference_minor": 0,
        "deferred_tax_difference_minor": 0,
    }


def test_missing_direct_cfo_is_not_formal_completion(rule_book):
    summary = calculate_summary(1_000_000, sample_occupancy(), rule_book)

    result = reconcile_all(summary, direct_cfo_minor=None)

    assert result["status"] == "not_run"
    direct_check = next(
        check
        for check in result["checks"]
        if check["check_id"] == "direct_indirect_cfo"
    )
    assert direct_check["status"] == "not_run"
    assert result["difference_minor"] is None


def test_voucher_balance_checks_each_voucher_without_offsetting():
    journal = pd.DataFrame(
        [
            {
                "voucher_id": "v1",
                "account_code": "1001",
                "debit_minor": 100,
                "credit_minor": 0,
            },
            {
                "voucher_id": "v2",
                "account_code": "1001",
                "debit_minor": 0,
                "credit_minor": 100,
            },
        ]
    )
    trial_balance = pd.DataFrame(
        [
            {
                "account_code": "1001",
                "period_debit_minor": 100,
                "period_credit_minor": 100,
            }
        ]
    )

    result = calculate_reconciliation_inputs(
        journal,
        trial_balance,
        pd.DataFrame(columns=["item_no", "adjustment_amount_minor"]),
        pd.DataFrame(),
        pd.DataFrame(),
    )

    assert result["voucher_balance_difference_minor"] == 200


def test_journal_trial_balance_checks_each_account_without_offsetting():
    journal = pd.DataFrame(
        [
            {
                "voucher_id": "v1",
                "account_code": "1001",
                "debit_minor": 100,
                "credit_minor": 100,
            },
            {
                "voucher_id": "v1",
                "account_code": "1002",
                "debit_minor": 100,
                "credit_minor": 100,
            },
        ]
    )
    trial_balance = pd.DataFrame(
        [
            {
                "account_code": "1001",
                "period_debit_minor": 200,
                "period_credit_minor": 0,
            },
            {
                "account_code": "1002",
                "period_debit_minor": 0,
                "period_credit_minor": 200,
            },
        ]
    )

    result = calculate_reconciliation_inputs(
        journal,
        trial_balance,
        pd.DataFrame(columns=["item_no", "adjustment_amount_minor"]),
        pd.DataFrame(),
        pd.DataFrame(),
    )

    assert result["journal_trial_balance_difference_minor"] == 400


def test_deferred_tax_items_cannot_hide_each_others_difference():
    journal = pd.DataFrame(
        [
            {
                "voucher_id": "v1",
                "account_code": "1001",
                "debit_minor": 100,
                "credit_minor": 100,
            }
        ]
    )
    trial_balance = pd.DataFrame(
        [
            {
                "account_code": "1001",
                "period_debit_minor": 100,
                "period_credit_minor": 100,
            }
        ]
    )
    committed = pd.DataFrame(
        [
            {"item_no": 11, "adjustment_amount_minor": 200},
            {"item_no": 12, "adjustment_amount_minor": 100},
        ]
    )
    bridges = pd.DataFrame(
        [
            {
                "family": "deferred_tax",
                "account_type": "deferred_tax_asset",
                "profit_or_loss_effect_minor": 100,
            },
            {
                "family": "deferred_tax",
                "account_type": "deferred_tax_liability",
                "profit_or_loss_effect_minor": 200,
            },
        ]
    )

    result = calculate_reconciliation_inputs(
        journal,
        trial_balance,
        committed,
        pd.DataFrame(),
        bridges,
    )

    assert result["deferred_tax_difference_minor"] == 200


def test_balance_bridge_does_not_add_other_adjustments_after_late_corrections():
    journal = pd.DataFrame(
        [
            {
                "voucher_id": "v1",
                "account_code": "140501",
                "debit_minor": 60,
                "credit_minor": 0,
            },
            {
                "voucher_id": "v1",
                "account_code": "400201",
                "debit_minor": 0,
                "credit_minor": 60,
            },
        ]
    )
    trial_balance = pd.DataFrame(
        [
            {
                "account_code": "140501",
                "period_debit_minor": 60,
                "period_credit_minor": 0,
            },
            {
                "account_code": "400201",
                "period_debit_minor": 0,
                "period_credit_minor": 60,
            },
        ]
    )
    committed = pd.DataFrame(columns=["item_no", "adjustment_amount_minor"])
    bridges = pd.DataFrame(
        [
            {
                "family": "working_capital",
                "record_type": "summary",
                "item_no": 13,
                "final_amount_minor": 0,
            },
            {
                "family": "other_adjustments",
                "target_item_no": 13,
                "adjustment_minor": 60,
            },
        ]
    )

    result = calculate_reconciliation_inputs(
        journal,
        trial_balance,
        committed,
        pd.DataFrame(),
        bridges,
    )

    assert result["balance_bridge_difference_minor"] == 0
