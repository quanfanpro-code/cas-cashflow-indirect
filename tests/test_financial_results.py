import pandas as pd

from cashflow_indirect.families.financial_results import classify_financial_results


def financial_fragment(
    fragment_id,
    signed_minor,
    event_kind,
    *,
    business_event_id=None,
    affects_profit_or_loss=True,
    core_financial_business=False,
    cash_settled=False,
):
    return {
        "fragment_id": fragment_id,
        "business_event_id": business_event_id or f"event-{fragment_id}",
        "account_name": "公允价值变动损益或财务费用或投资收益",
        "signed_minor": signed_minor,
        "event_kind": event_kind,
        "affects_profit_or_loss": affects_profit_or_loss,
        "technical_closing": False,
        "core_financial_business": core_financial_business,
        "cash_settled": cash_settled,
    }


def test_cash_equivalent_fx_is_removed_from_operating_cash_flow():
    fragments = pd.DataFrame(
        [
            financial_fragment(
                "f-cash-fx",
                -30000,
                "cash_equivalent_fx",
                business_event_id="cash-fx",
            )
        ]
    )

    claims = classify_financial_results(fragments, pd.DataFrame())

    cash_fx = claims.query("rule_id == 'FIN-CASH-FX'")
    assert cash_fx["item_no"].tolist() == [9]
    assert cash_fx["claim_amount_minor"].tolist() == [-30000]


def test_fair_value_and_disposal_use_different_items():
    fragments = pd.DataFrame(
        [
            financial_fragment(
                "f-remeasure",
                50000,
                "fair_value_remeasurement",
                business_event_id="remeasure",
                cash_settled=True,
            ),
            financial_fragment(
                "f-dispose",
                -40000,
                "financial_asset_disposal",
                business_event_id="dispose",
                cash_settled=True,
            ),
        ]
    )

    claims = classify_financial_results(fragments, pd.DataFrame())

    assert claims.query("business_event_id == 'remeasure'")["item_no"].tolist() == [
        8
    ]
    assert claims.query("business_event_id == 'dispose'")["item_no"].tolist() == [
        10
    ]


def test_oci_and_effective_cash_flow_hedge_are_not_claimed():
    fragments = pd.DataFrame(
        [
            financial_fragment(
                "f-oci",
                10000,
                "fair_value_oci",
                affects_profit_or_loss=False,
            ),
            financial_fragment(
                "f-own-credit",
                8000,
                "own_credit_oci",
                affects_profit_or_loss=False,
            ),
            financial_fragment(
                "f-hedge",
                6000,
                "cash_flow_hedge_effective",
                affects_profit_or_loss=False,
            ),
            financial_fragment(
                "f-ineffective",
                3000,
                "cash_flow_hedge_ineffective",
            ),
        ]
    )

    claims = classify_financial_results(fragments, pd.DataFrame())

    assert claims[["fragment_id", "item_no", "claim_amount_minor"]].to_dict(
        "records"
    ) == [
        {
            "fragment_id": "f-ineffective",
            "item_no": 8,
            "claim_amount_minor": 3000,
        }
    ]


def test_finance_cost_inclusions_and_operating_exclusions():
    fragments = pd.DataFrame(
        [
            financial_fragment("f-borrowing", 20000, "financing_interest"),
            financial_fragment("f-lease", 9000, "lease_liability_interest"),
            financial_fragment("f-invest-income", -7000, "investment_interest"),
            financial_fragment("f-invest-fx", 4000, "investment_fx"),
            financial_fragment("f-operating-fx", 3000, "operating_receivable_fx"),
            financial_fragment("f-bank-fee", 2000, "bank_settlement_fee"),
            financial_fragment(
                "f-capitalized",
                6000,
                "capitalized_borrowing_cost",
                affects_profit_or_loss=False,
            ),
        ]
    )

    claims = classify_financial_results(fragments, pd.DataFrame())

    assert claims.query("item_no == 9")["claim_amount_minor"].sum() == 26000
    assert set(claims["fragment_id"]) == {
        "f-borrowing",
        "f-lease",
        "f-invest-income",
        "f-invest-fx",
    }


def test_investment_results_are_complete_without_stealing_other_items():
    fragments = pd.DataFrame(
        [
            financial_fragment("f-equity", -12000, "equity_method_result"),
            financial_fragment("f-dividend", -5000, "investment_dividend"),
            financial_fragment("f-subsidiary", 17000, "subsidiary_disposal"),
            financial_fragment("f-control", -9000, "loss_of_control"),
            financial_fragment("f-credit", 7000, "credit_impairment"),
            financial_fragment("f-long-asset", 6000, "long_asset_disposal"),
            financial_fragment("f-factoring", 4000, "operating_factoring"),
        ]
    )

    claims = classify_financial_results(fragments, pd.DataFrame())

    assert claims.query("item_no == 10")["claim_amount_minor"].sum() == -9000
    assert set(claims["fragment_id"]) == {
        "f-equity",
        "f-dividend",
        "f-subsidiary",
        "f-control",
    }


def test_financial_entity_core_business_is_blocked_not_forced():
    fragments = pd.DataFrame(
        [
            financial_fragment(
                "f-core",
                -30000,
                "investment_interest",
                core_financial_business=True,
            ),
            financial_fragment(
                "f-noncore",
                10000,
                "lease_liability_interest",
                core_financial_business=False,
            ),
        ]
    )

    claims = classify_financial_results(
        fragments,
        pd.DataFrame(),
        financial_entity_flag=True,
    )

    blocked = claims.query("fragment_id == 'f-core'").iloc[0]
    assert blocked["decision"] == "blocked"
    assert blocked["rule_id"] == "FINANCIAL_ENTITY_RULE_NOT_SUPPORTED"
    assert pd.isna(blocked["item_no"])
    assert claims.query("fragment_id == 'f-noncore'")["item_no"].tolist() == [9]
