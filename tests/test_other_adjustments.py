import pandas as pd
import pytest

from cashflow_indirect.families.other_adjustments import (
    calculate_other_adjustments,
)


def grant_inputs(cash_minor, profit_minor):
    expected = cash_minor - profit_minor
    fragment_id = "grant-bridge"
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": fragment_id,
                "event_kind": "government_grant",
                "signed_minor": expected,
                "cost_destination": "profit_or_loss",
                "source_evidence": True,
                "business_event_id": "grant-event",
            }
        ]
    )
    events = pd.DataFrame(
        [
            {
                "business_event_id": "grant-event",
                "event_type": "government_grant",
                "source_fragment_id": fragment_id,
                "operating_cash_minor": cash_minor,
                "profit_effect_minor": profit_minor,
                "source_evidence": True,
            }
        ]
    )
    return fragments, events


@pytest.mark.parametrize(
    ("cash_minor", "profit_minor", "expected"),
    [
        (10000, 10000, 0),
        (10000, 2000, 8000),
        (0, 2000, -2000),
        (0, 10000, -10000),
        (10000, 0, 10000),
        (-10000, -3000, -7000),
    ],
)
def test_government_grant_event_bridge(cash_minor, profit_minor, expected):
    fragments, events = grant_inputs(cash_minor, profit_minor)

    claims, bridge = calculate_other_adjustments(
        fragments,
        events,
        pd.DataFrame(),
    )

    assert bridge.query("event_type == 'government_grant'")[
        "adjustment_minor"
    ].sum() == expected
    assert claims["claim_amount_minor"].sum() == expected


def test_share_payment_routes_by_cost_destination():
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f-expense",
                "business_event_id": "share-expense",
                "event_kind": "equity_settled_share_payment",
                "signed_minor": 10000,
                "cost_destination": "profit_or_loss",
                "source_evidence": True,
            },
            {
                "fragment_id": "f-inventory",
                "business_event_id": "share-inventory",
                "event_kind": "equity_settled_share_payment",
                "signed_minor": 6000,
                "cost_destination": "inventory",
                "source_evidence": True,
            },
            {
                "fragment_id": "f-long-asset",
                "business_event_id": "share-long-asset",
                "event_kind": "equity_settled_share_payment",
                "signed_minor": 4000,
                "cost_destination": "nonoperating_long_lived_asset",
                "source_evidence": True,
            },
            {
                "fragment_id": "f-cash",
                "business_event_id": "share-cash",
                "event_kind": "cash_settled_share_payment",
                "signed_minor": 3000,
                "cost_destination": "profit_or_loss",
                "source_evidence": True,
            },
        ]
    )

    claims, bridge = calculate_other_adjustments(
        fragments,
        pd.DataFrame(),
        pd.DataFrame(),
    )

    assert claims[["fragment_id", "item_no", "claim_amount_minor"]].to_dict(
        "records"
    ) == [
        {
            "fragment_id": "f-expense",
            "item_no": 16,
            "claim_amount_minor": 10000,
        }
    ]
    inventory_route = bridge.query("source_fragment_id == 'f-inventory'").iloc[0]
    assert inventory_route["target_item_no"] == 13
    assert inventory_route["adjustment_minor"] == 6000


def test_debt_restructuring_only_takes_unoccupied_supported_remainder():
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f-debt",
                "business_event_id": "debt-event",
                "event_kind": "debt_restructuring",
                "signed_minor": 10000,
                "cost_destination": "profit_or_loss",
                "source_evidence": True,
            }
        ]
    )
    events = pd.DataFrame(
        [
            {
                "business_event_id": "debt-event",
                "event_type": "debt_restructuring",
                "source_fragment_id": "f-debt",
                "profit_effect_minor": 10000,
                "source_evidence": True,
                "shareholder_related": False,
            }
        ]
    )
    occupancy = pd.DataFrame(
        [
            {
                "fragment_id": "f-debt",
                "item_no": 10,
                "physical_consumed_minor": 6000,
            }
        ]
    )

    claims, bridge = calculate_other_adjustments(fragments, events, occupancy)

    assert claims[["item_no", "claim_amount_minor"]].to_dict("records") == [
        {"item_no": 16, "claim_amount_minor": 4000}
    ]
    assert bridge.query("event_type == 'debt_restructuring'")[
        ["occupied_minor", "adjustment_minor"]
    ].to_dict("records") == [
        {"occupied_minor": 6000, "adjustment_minor": 4000}
    ]


def test_shareholder_transaction_is_not_normal_debt_restructuring():
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f-shareholder",
                "business_event_id": "shareholder-event",
                "event_kind": "debt_restructuring",
                "signed_minor": 12000,
                "cost_destination": "profit_or_loss",
                "source_evidence": True,
            }
        ]
    )
    events = pd.DataFrame(
        [
            {
                "business_event_id": "shareholder-event",
                "event_type": "debt_restructuring",
                "source_fragment_id": "f-shareholder",
                "profit_effect_minor": 12000,
                "source_evidence": True,
                "shareholder_related": True,
            }
        ]
    )

    claims, bridge = calculate_other_adjustments(
        fragments,
        events,
        pd.DataFrame(),
    )

    assert claims.empty
    assert bridge["reason_code"].tolist() == ["SHAREHOLDER_EQUITY_TRANSACTION"]


def test_unlisted_adjustment_is_rejected_instead_of_becoming_a_plug():
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f-plug",
                "business_event_id": "plug-event",
                "event_kind": "unexplained_difference",
                "signed_minor": 99999,
                "cost_destination": "profit_or_loss",
                "source_evidence": False,
            }
        ]
    )

    claims, bridge = calculate_other_adjustments(
        fragments,
        pd.DataFrame(),
        pd.DataFrame(),
    )

    assert claims.empty
    assert bridge["reason_code"].tolist() == ["NOT_WHITELISTED"]
    assert bridge["next_action"].tolist() == ["ai_request"]
