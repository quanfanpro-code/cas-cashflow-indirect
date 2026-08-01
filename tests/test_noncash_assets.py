import pandas as pd

from cashflow_indirect.families.noncash_assets import classify_noncash_assets


def fragment(
    fragment_id,
    signed_minor,
    asset_class,
    event_kind,
    *,
    cost_destination="profit_or_loss",
    accounting_status="normal",
    affects_profit_or_loss=True,
    business_event_id=None,
):
    return {
        "fragment_id": fragment_id,
        "business_event_id": business_event_id or f"event-{fragment_id}",
        "account_name": "折旧摊销或处置损益",
        "signed_minor": signed_minor,
        "asset_class": asset_class,
        "event_kind": event_kind,
        "cost_destination": cost_destination,
        "accounting_status": accounting_status,
        "affects_profit_or_loss": affects_profit_or_loss,
        "technical_closing": False,
    }


def test_depreciation_destination_controls_inclusion():
    fragments = pd.DataFrame(
        [
            fragment("f-expense", 100000, "fixed_asset", "depreciation"),
            fragment(
                "f-inventory",
                50000,
                "fixed_asset",
                "depreciation",
                cost_destination="inventory",
                affects_profit_or_loss=False,
            ),
            fragment(
                "f-cip",
                30000,
                "fixed_asset",
                "depreciation",
                cost_destination="construction_in_progress",
                affects_profit_or_loss=False,
            ),
        ]
    )

    claims, routing = classify_noncash_assets(fragments, pd.DataFrame())

    assert claims.query("item_no == 3")["claim_amount_minor"].sum() == 150000
    assert "进入在建工程" in routing.query("record_type == 'exclusion'")[
        "reason"
    ].tolist()
    assert routing.query("target_item_no == 13")["amount_minor"].sum() == 50000


def test_intangible_and_long_prepaid_boundaries():
    fragments = pd.DataFrame(
        [
            fragment("f-intangible", 20000, "intangible_asset", "amortization"),
            fragment(
                "f-indefinite",
                5000,
                "intangible_asset",
                "amortization",
                accounting_status="indefinite_life",
            ),
            fragment(
                "f-not-ready",
                3000,
                "intangible_asset",
                "amortization",
                accounting_status="not_ready_for_use",
            ),
            fragment(
                "f-held-for-sale-intangible",
                4000,
                "intangible_asset",
                "amortization",
                accounting_status="held_for_sale",
            ),
            fragment(
                "f-prepaid",
                10000,
                "long_prepaid_expense",
                "amortization",
            ),
            fragment(
                "f-prepaid-writeoff",
                8000,
                "long_prepaid_expense",
                "one_time_non_cash_writeoff",
            ),
            fragment(
                "f-prepaid-refund",
                -4000,
                "long_prepaid_expense",
                "cash_refund",
            ),
        ]
    )

    claims, routing = classify_noncash_assets(fragments, pd.DataFrame())

    assert claims.groupby("item_no")["claim_amount_minor"].sum().to_dict() == {
        4: 32000,
        5: 18000,
    }
    assert set(routing.query("record_type == 'exclusion'")["source_fragment_id"]) == {
        "f-prepaid-refund",
    }
    assert set(routing.query("record_type == 'issue'")["source_fragment_id"]) == {
        "f-indefinite",
        "f-not-ready",
        "f-held-for-sale-intangible",
    }
    assert set(routing.query("record_type == 'issue'")["next_action"]) == {
        "accounting_error_review"
    }


def test_abnormal_depreciation_is_adjusted_and_flagged():
    fragments = pd.DataFrame(
        [
            fragment(
                "f-fair-value",
                12000,
                "investment_property_fair_value",
                "depreciation",
                accounting_status="fair_value_model",
            ),
            fragment(
                "f-held-for-sale",
                9000,
                "fixed_asset",
                "depreciation",
                accounting_status="held_for_sale",
            ),
        ]
    )

    claims, routing = classify_noncash_assets(fragments, pd.DataFrame())

    assert claims["claim_amount_minor"].sum() == 21000
    assert set(routing.query("record_type == 'issue'")["reason_code"]) == {
        "FAIR_VALUE_PROPERTY_DEPRECIATION",
        "HELD_FOR_SALE_DEPRECIATION",
    }


def test_fixed_asset_sale_and_scrap_are_mutually_exclusive():
    fragments = pd.DataFrame(
        [
            fragment(
                "f-sale-net",
                25000,
                "fixed_asset",
                "disposal_profit_or_loss",
                business_event_id="sale",
            ),
            fragment(
                "f-scrap-net",
                15000,
                "fixed_asset",
                "scrap_profit_or_loss",
                business_event_id="scrap",
            ),
        ]
    )
    events = pd.DataFrame(
        [
            {
                "business_event_id": "sale",
                "event_type": "long_asset_sale",
                "asset_class": "fixed_asset",
                "profit_or_loss_fragment_id": "f-sale-net",
                "profit_or_loss_minor": 25000,
                "affects_profit_or_loss": True,
            },
            {
                "business_event_id": "scrap",
                "event_type": "fixed_asset_scrap",
                "asset_class": "fixed_asset",
                "profit_or_loss_fragment_id": "f-scrap-net",
                "profit_or_loss_minor": 15000,
                "affects_profit_or_loss": True,
            },
        ]
    )

    claims, _ = classify_noncash_assets(fragments, events)

    assert claims.query("business_event_id == 'sale'")["item_no"].unique().tolist() == [
        6
    ]
    assert claims.query("business_event_id == 'scrap'")["item_no"].unique().tolist() == [
        7
    ]


def test_disposal_uses_one_event_net_result_across_vouchers():
    fragments = pd.DataFrame(
        [
            fragment(
                "f-original",
                200000,
                "fixed_asset",
                "asset_cost_transfer",
                business_event_id="sale",
            ),
            fragment(
                "f-accumulated",
                -120000,
                "fixed_asset",
                "accumulated_depreciation_transfer",
                business_event_id="sale",
            ),
            fragment(
                "f-proceeds",
                -55000,
                "fixed_asset",
                "cash_proceeds",
                business_event_id="sale",
            ),
            fragment(
                "f-net",
                25000,
                "fixed_asset",
                "disposal_profit_or_loss",
                business_event_id="sale",
            ),
        ]
    )
    events = pd.DataFrame(
        [
            {
                "business_event_id": "sale",
                "event_type": "long_asset_sale",
                "asset_class": "fixed_asset",
                "profit_or_loss_fragment_id": "f-net",
                "profit_or_loss_minor": 25000,
                "affects_profit_or_loss": True,
            }
        ]
    )

    claims, _ = classify_noncash_assets(fragments, events)

    event_claims = claims.query("business_event_id == 'sale'")
    assert event_claims[
        ["fragment_id", "item_no", "claim_amount_minor"]
    ].to_dict("records") == [
        {
            "fragment_id": "f-net",
            "item_no": 6,
            "claim_amount_minor": 25000,
        }
    ]


def test_oil_gas_and_productive_biology_are_depreciation_not_inventory():
    fragments = pd.DataFrame(
        [
            fragment("f-oil", 30000, "oil_gas_asset", "depletion"),
            fragment(
                "f-productive",
                11000,
                "productive_biological_asset",
                "depreciation",
            ),
            fragment(
                "f-consumable",
                7000,
                "consumable_biological_asset",
                "depreciation",
            ),
        ]
    )

    claims, routing = classify_noncash_assets(fragments, pd.DataFrame())

    assert claims["claim_amount_minor"].sum() == 41000
    assert routing.query("source_fragment_id == 'f-consumable'")[
        "record_type"
    ].tolist() == ["exclusion"]


def test_intangible_writeoff_and_rou_termination_go_to_item_6():
    fragments = pd.DataFrame(
        [
            fragment(
                "f-intangible-net",
                18000,
                "intangible_asset",
                "disposal_profit_or_loss",
                business_event_id="intangible-end",
            ),
            fragment(
                "f-rou-net",
                -6000,
                "right_of_use_asset",
                "disposal_profit_or_loss",
                business_event_id="rou-end",
            ),
        ]
    )
    events = pd.DataFrame(
        [
            {
                "business_event_id": "intangible-end",
                "event_type": "intangible_no_future_benefit",
                "asset_class": "intangible_asset",
                "profit_or_loss_fragment_id": "f-intangible-net",
                "profit_or_loss_minor": 18000,
                "affects_profit_or_loss": True,
            },
            {
                "business_event_id": "rou-end",
                "event_type": "right_of_use_termination",
                "asset_class": "right_of_use_asset",
                "profit_or_loss_fragment_id": "f-rou-net",
                "profit_or_loss_minor": -6000,
                "affects_profit_or_loss": True,
            },
        ]
    )

    claims, _ = classify_noncash_assets(fragments, events)

    assert claims.groupby("item_no")["claim_amount_minor"].sum().to_dict() == {
        6: 12000
    }
