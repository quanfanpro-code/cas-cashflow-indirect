from importlib import import_module

import pandas as pd


impairment = import_module("cashflow_indirect.families.impairment")


def loss_fragment(
    fragment_id,
    account_name,
    signed_minor,
    asset_class,
    event_kind,
    impairment_model=None,
    affects_profit_or_loss=True,
    technical_closing=False,
    counterparty_level1="减值准备",
):
    return {
        "fragment_id": fragment_id,
        "account_name": account_name,
        "level1_account_name": account_name,
        "counterparty_level1": counterparty_level1,
        "summary": event_kind,
        "signed_minor": signed_minor,
        "amount_minor": abs(signed_minor),
        "asset_class": asset_class,
        "event_kind": event_kind,
        "impairment_model": impairment_model,
        "affects_profit_or_loss": affects_profit_or_loss,
        "technical_closing": technical_closing,
        "business_event_id": f"event-{fragment_id}",
    }


def test_impairment_family_separates_asset_credit_and_contract_loss():
    fragments = pd.DataFrame(
        [
            loss_fragment(
                "f-asset",
                "资产减值损失",
                100000,
                "inventory",
                "provision",
                "asset",
            ),
            loss_fragment(
                "f-credit",
                "信用减值损失",
                60000,
                "receivable",
                "provision",
                "credit",
            ),
            loss_fragment(
                "f-contract",
                "资产减值损失",
                20000,
                "contract_asset",
                "provision",
                "credit",
            ),
        ]
    )

    claims, records = impairment.classify_impairment(
        fragments,
        pd.DataFrame(),
    )

    assert claims.groupby("item_no")["claim_amount_minor"].sum().to_dict() == {
        1: 120000,
        2: 60000,
    }
    assert records.empty


def test_allowance_writeoff_changes_receivable_not_credit_loss():
    fragments = pd.DataFrame(
        [
            loss_fragment(
                "f-writeoff",
                "坏账准备",
                40000,
                "receivable",
                "writeoff",
                "credit",
                affects_profit_or_loss=False,
                counterparty_level1="应收账款",
            )
        ]
    )

    claims, records = impairment.classify_impairment(
        fragments,
        pd.DataFrame(),
    )

    assert claims.empty
    assert records[
        [
            "record_type",
            "target_item_no",
            "correction_minor",
            "reason_code",
            "source_fragment_id",
        ]
    ].to_dict("records") == [
        {
            "record_type": "correction",
            "target_item_no": 14,
            "correction_minor": -40000,
            "reason_code": "ALLOWANCE_WRITEOFF",
            "source_fragment_id": "f-writeoff",
        }
    ]


def test_direct_bad_debt_is_adjusted_once_and_corrects_receivable():
    fragments = pd.DataFrame(
        [
            loss_fragment(
                "f-direct",
                "信用减值损失",
                25000,
                "receivable",
                "direct_loss",
                "credit",
                counterparty_level1="应收账款",
            )
        ]
    )

    claims, records = impairment.classify_impairment(
        fragments,
        pd.DataFrame(),
    )

    assert claims[["item_no", "claim_amount_minor"]].to_dict("records") == [
        {"item_no": 2, "claim_amount_minor": 25000}
    ]
    assert records.query("record_type == 'correction'")[
        ["target_item_no", "correction_minor", "reason_code"]
    ].to_dict("records") == [
        {
            "target_item_no": 14,
            "correction_minor": -25000,
            "reason_code": "DIRECT_BAD_DEBT_ALREADY_ADJUSTED",
        }
    ]


def test_long_lived_asset_reversal_is_adjusted_and_flagged():
    fragments = pd.DataFrame(
        [
            loss_fragment(
                "f-inventory-reversal",
                "资产减值损失",
                -30000,
                "inventory",
                "reversal",
                "asset",
            ),
            loss_fragment(
                "f-fixed-reversal",
                "资产减值损失",
                -10000,
                "fixed_asset",
                "reversal",
                "asset",
            ),
        ]
    )

    claims, records = impairment.classify_impairment(
        fragments,
        pd.DataFrame(),
    )

    assert claims["claim_amount_minor"].sum() == -40000
    assert records.query("record_type == 'issue'")["reason_code"].tolist() == [
        "PROHIBITED_LONG_LIVED_REVERSAL"
    ]


def test_non_profit_and_technical_closing_changes_are_excluded():
    fragments = pd.DataFrame(
        [
            loss_fragment(
                "f-oci",
                "信用减值损失",
                10000,
                "other_debt_investment",
                "oci_change",
                "credit",
                affects_profit_or_loss=False,
            ),
            loss_fragment(
                "f-closing",
                "资产减值损失",
                -5000,
                "inventory",
                "closing",
                "asset",
                technical_closing=True,
            ),
        ]
    )

    claims, records = impairment.classify_impairment(
        fragments,
        pd.DataFrame(),
    )

    assert claims.empty
    assert records.query("record_type == 'exclusion'")["source_fragment_id"].tolist() == [
        "f-oci",
        "f-closing",
    ]
