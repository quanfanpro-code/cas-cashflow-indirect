import pandas as pd

from cashflow_indirect.families.deferred_tax import calculate_deferred_tax


def deferred_tax_fragment(
    fragment_id,
    account_type,
    balance_change_minor,
    route,
    *,
    event_kind="temporary_difference_change",
    direct_counterparty="其他科目",
):
    return {
        "fragment_id": fragment_id,
        "business_event_id": f"event-{fragment_id}",
        "account_type": account_type,
        "balance_change_minor": balance_change_minor,
        "route": route,
        "event_kind": event_kind,
        "direct_counterparty": direct_counterparty,
        "technical_closing": False,
    }


def deferred_tax_balance():
    return pd.DataFrame(
        [
            {
                "tax_entity_id": "entity-a",
                "tax_jurisdiction": "CN",
                "account_type": "deferred_tax_asset",
                "opening_gross_minor": 100000,
                "closing_gross_minor": 80000,
                "non_profit_change_minor": 0,
            },
            {
                "tax_entity_id": "entity-a",
                "tax_jurisdiction": "CN",
                "account_type": "deferred_tax_liability",
                "opening_gross_minor": 40000,
                "closing_gross_minor": 70000,
                "non_profit_change_minor": 0,
            },
        ]
    )


def test_deferred_tax_uses_profit_or_loss_route_not_only_direct_counterparty():
    fragments = pd.DataFrame(
        [
            deferred_tax_fragment(
                "f-dta",
                "deferred_tax_asset",
                -20000,
                "profit_or_loss",
                direct_counterparty="可抵扣暂时性差异",
            ),
            deferred_tax_fragment(
                "f-dtl",
                "deferred_tax_liability",
                30000,
                "profit_or_loss",
                direct_counterparty="应纳税暂时性差异",
            ),
        ]
    )

    claims, bridge = calculate_deferred_tax(fragments, deferred_tax_balance())

    assert claims.groupby("item_no")["claim_amount_minor"].sum().to_dict() == {
        11: 20000,
        12: 30000,
    }
    assert bridge["profit_or_loss_effect_minor"].sum() == 50000
    assert set(bridge["source"]) == {"fragment"}


def test_oci_and_business_combination_changes_are_excluded():
    fragments = pd.DataFrame(
        [
            deferred_tax_fragment(
                "f-oci",
                "deferred_tax_asset",
                10000,
                "other_comprehensive_income",
            ),
            deferred_tax_fragment(
                "f-combination",
                "deferred_tax_liability",
                25000,
                "business_combination",
            ),
            deferred_tax_fragment(
                "f-equity",
                "deferred_tax_liability",
                5000,
                "equity",
            ),
        ]
    )

    claims, bridge = calculate_deferred_tax(fragments, deferred_tax_balance())

    assert claims.empty
    assert set(bridge["reason_code"]) == {"NON_PROFIT_ROUTE"}


def test_balance_fallback_uses_gross_balances_and_removes_non_profit_changes():
    trial_balance = pd.DataFrame(
        [
            {
                "tax_entity_id": "entity-a",
                "tax_jurisdiction": "CN",
                "account_type": "deferred_tax_asset",
                "opening_gross_minor": 100000,
                "closing_gross_minor": 80000,
                "non_profit_change_minor": 5000,
            },
            {
                "tax_entity_id": "entity-a",
                "tax_jurisdiction": "CN",
                "account_type": "deferred_tax_liability",
                "opening_gross_minor": 40000,
                "closing_gross_minor": 70000,
                "non_profit_change_minor": 10000,
            },
        ]
    )

    claims, bridge = calculate_deferred_tax(pd.DataFrame(), trial_balance)

    assert claims.groupby("item_no")["claim_amount_minor"].sum().to_dict() == {
        11: 25000,
        12: 20000,
    }
    assert set(bridge["source"]) == {"trial_balance_fallback"}


def test_tax_rate_and_recoverability_changes_follow_actual_profit_effect():
    fragments = pd.DataFrame(
        [
            deferred_tax_fragment(
                "f-rate",
                "deferred_tax_asset",
                10000,
                "profit_or_loss",
                event_kind="tax_rate_change",
            ),
            deferred_tax_fragment(
                "f-new",
                "deferred_tax_asset",
                12000,
                "profit_or_loss",
                event_kind="new_asset_recognition",
            ),
            deferred_tax_fragment(
                "f-write-down",
                "deferred_tax_asset",
                -8000,
                "profit_or_loss",
                event_kind="recoverability_write_down",
            ),
            deferred_tax_fragment(
                "f-reversal",
                "deferred_tax_asset",
                3000,
                "profit_or_loss",
                event_kind="recoverability_reversal",
            ),
        ]
    )

    claims, _ = calculate_deferred_tax(fragments, deferred_tax_balance())

    assert claims.query("item_no == 11")["claim_amount_minor"].sum() == -17000
    assert set(claims["rule_id"]) == {"TAX-DEFERRED-PROFIT-ROUTE"}


def test_precise_fragments_take_priority_over_balance_fallback():
    fragments = pd.DataFrame(
        [
            deferred_tax_fragment(
                "f-dta",
                "deferred_tax_asset",
                -20000,
                "profit_or_loss",
            ),
            deferred_tax_fragment(
                "f-dtl",
                "deferred_tax_liability",
                30000,
                "profit_or_loss",
            ),
        ]
    )

    claims, bridge = calculate_deferred_tax(fragments, deferred_tax_balance())

    assert claims["claim_amount_minor"].sum() == 50000
    assert "trial_balance_fallback" not in set(bridge["source"])


def test_unknown_deferred_tax_route_is_sent_to_ai_instead_of_assumed_profit():
    fragments = pd.DataFrame(
        [
            deferred_tax_fragment(
                "f-unknown",
                "deferred_tax_asset",
                10000,
                "unknown",
            )
        ]
    )

    claims, bridge = calculate_deferred_tax(fragments, deferred_tax_balance())

    assert claims.empty
    assert bridge["reason_code"].tolist() == ["UNKNOWN_ROUTE"]
    assert bridge["next_action"].tolist() == ["ai_request"]


def test_balance_fallback_fragment_ids_are_unique_per_balance_row():
    trial_balance = pd.DataFrame(
        [
            {
                "balance_row_id": "181101",
                "tax_entity_id": "entity-a",
                "tax_jurisdiction": "CN",
                "account_type": "deferred_tax_asset",
                "opening_gross_minor": 100000,
                "closing_gross_minor": 80000,
                "non_profit_change_minor": 0,
            },
            {
                "balance_row_id": "181102",
                "tax_entity_id": "entity-a",
                "tax_jurisdiction": "CN",
                "account_type": "deferred_tax_asset",
                "opening_gross_minor": 50000,
                "closing_gross_minor": 30000,
                "non_profit_change_minor": 0,
            },
        ]
    )

    claims, _ = calculate_deferred_tax(pd.DataFrame(), trial_balance)

    assert claims["fragment_id"].is_unique
    assert set(claims["fragment_id"]) == {"tb-181101", "tb-181102"}
