"""确定性规则编号三方一致性：代码产出的规则编号必须注册在规则清单中。"""

import pandas as pd

from cashflow_indirect.contracts import RunSpec
from cashflow_indirect.families.deferred_tax import calculate_deferred_tax
from cashflow_indirect.families.financial_results import (
    classify_financial_results,
)
from cashflow_indirect.families.impairment import classify_impairment
from cashflow_indirect.families.noncash_assets import classify_noncash_assets
from cashflow_indirect.families.other_adjustments import (
    calculate_other_adjustments,
)
from cashflow_indirect.families.working_capital import calculate_working_capital
from cashflow_indirect.pipeline import _family_results


def _registered(rule_book, item_no, rule_id):
    item = next(item for item in rule_book.items if item.item_no == item_no)
    assert rule_id in item.rule_ids, (
        f"第{item_no}项产出了未注册的规则编号：{rule_id}"
    )


def test_impairment_rule_ids_registered(rule_book):
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f-asset",
                "account_name": "资产减值损失",
                "signed_minor": 100,
                "asset_class": "fixed_asset",
                "event_kind": "provision",
                "impairment_model": "asset",
                "affects_profit_or_loss": True,
                "technical_closing": False,
                "profit_or_loss_effect_minor": 100,
                "business_event_id": "e1",
            },
            {
                "fragment_id": "f-credit",
                "account_name": "信用减值损失",
                "signed_minor": 200,
                "asset_class": "receivable",
                "event_kind": "provision",
                "impairment_model": "credit",
                "affects_profit_or_loss": True,
                "technical_closing": False,
                "profit_or_loss_effect_minor": 200,
                "business_event_id": "e2",
            },
        ]
    )
    claims, _ = classify_impairment(fragments, pd.DataFrame())
    for row in claims.to_dict("records"):
        _registered(rule_book, int(row["item_no"]), row["rule_id"])


def test_noncash_rule_ids_registered(rule_book):
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f-dep",
                "signed_minor": 100,
                "asset_class": "fixed_asset",
                "event_kind": "depreciation",
                "cost_destination": "profit_or_loss",
                "accounting_status": "normal",
                "affects_profit_or_loss": True,
                "technical_closing": False,
                "business_event_id": "e1",
            },
            {
                "fragment_id": "f-amort",
                "signed_minor": 50,
                "asset_class": "intangible_asset",
                "event_kind": "amortization",
                "cost_destination": "profit_or_loss",
                "accounting_status": "normal",
                "affects_profit_or_loss": True,
                "technical_closing": False,
                "business_event_id": "e2",
            },
            {
                "fragment_id": "f-prepaid",
                "signed_minor": 30,
                "asset_class": "long_prepaid_expense",
                "event_kind": "amortization",
                "cost_destination": "profit_or_loss",
                "accounting_status": "normal",
                "affects_profit_or_loss": True,
                "technical_closing": False,
                "business_event_id": "e3",
            },
        ]
    )
    events = pd.DataFrame(
        [
            {
                "business_event_id": "e-sale",
                "event_type": "long_asset_sale",
                "asset_class": "fixed_asset",
                "profit_or_loss_fragment_id": "f-dep",
                "profit_or_loss_minor": -20,
                "affects_profit_or_loss": True,
            },
            {
                "business_event_id": "e-scrap",
                "event_type": "fixed_asset_scrap",
                "asset_class": "fixed_asset",
                "profit_or_loss_fragment_id": "f-amort",
                "profit_or_loss_minor": 10,
                "affects_profit_or_loss": True,
            },
        ]
    )
    claims, _ = classify_noncash_assets(fragments, events)
    assert len(claims) == 5
    for row in claims.to_dict("records"):
        _registered(rule_book, int(row["item_no"]), row["rule_id"])


def test_financial_rule_ids_registered(rule_book):
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": f"f-{kind}",
                "signed_minor": 100,
                "event_kind": kind,
                "affects_profit_or_loss": True,
                "technical_closing": False,
                "business_event_id": "e",
            }
            for kind in (
                "fair_value_remeasurement",
                "financing_interest",
                "cash_equivalent_fx",
                "other_investment_result",
            )
        ]
    )
    claims = classify_financial_results(fragments, pd.DataFrame())
    assert len(claims) == 4
    for row in claims.to_dict("records"):
        _registered(rule_book, int(row["item_no"]), row["rule_id"])


def test_deferred_tax_rule_ids_registered(rule_book):
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f-tax",
                "account_type": "deferred_tax_asset",
                "balance_change_minor": 100,
                "route": "profit_or_loss",
                "technical_closing": False,
                "business_event_id": "e",
            }
        ]
    )
    claims, _ = calculate_deferred_tax(fragments, pd.DataFrame())
    for row in claims.to_dict("records"):
        _registered(rule_book, int(row["item_no"]), row["rule_id"])

    trial_balance = pd.DataFrame(
        [
            {
                "tax_entity_id": "甲公司",
                "tax_jurisdiction": "境内",
                "account_type": "deferred_tax_liability",
                "opening_gross_minor": 100,
                "closing_gross_minor": 300,
            }
        ]
    )
    claims, _ = calculate_deferred_tax(pd.DataFrame(), trial_balance)
    for row in claims.to_dict("records"):
        _registered(rule_book, int(row["item_no"]), row["rule_id"])


def test_working_capital_rule_ids_registered(rule_book):
    balance = pd.DataFrame(
        [
            {
                "account_code": "112201",
                "opening_debit_minor": 500,
                "opening_credit_minor": 0,
                "period_debit_minor": 0,
                "period_credit_minor": 0,
                "closing_debit_minor": 300,
                "closing_credit_minor": 0,
                "is_leaf": True,
            }
        ]
    )
    claims, _ = calculate_working_capital(
        balance,
        balance,
        balance,
        corrections=pd.DataFrame(),
    )
    assert len(claims) == 3
    for row in claims.to_dict("records"):
        _registered(rule_book, int(row["item_no"]), row["rule_id"])


def test_other_adjustments_rule_ids_registered(rule_book):
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f-share",
                "event_kind": "equity_settled_share_payment",
                "signed_minor": 100,
                "cost_destination": "profit_or_loss",
                "source_evidence": True,
                "business_event_id": "e-share",
            }
        ]
    )
    events = pd.DataFrame(
        [
            {
                "business_event_id": "e-grant",
                "event_type": "government_grant",
                "source_fragment_id": "f-grant",
                "operating_cash_minor": 100,
                "profit_effect_minor": 20,
                "source_evidence": True,
            },
            {
                "business_event_id": "e-debt",
                "event_type": "debt_restructuring",
                "source_fragment_id": "f-debt",
                "profit_effect_minor": 50,
                "source_evidence": True,
            },
        ]
    )
    claims, _ = calculate_other_adjustments(
        fragments,
        events,
        pd.DataFrame(),
    )
    assert len(claims) == 3
    for row in claims.to_dict("records"):
        _registered(rule_book, int(row["item_no"]), row["rule_id"])


def test_paired_offset_rule_id_registered(rule_book, tmp_path):
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f-pair",
                "line_id": "l-1",
                "voucher_id": "v-1",
                "source_file": "序时账.xlsx",
                "source_sheet": "序时账",
                "source_row": 2,
                "split_index": 0,
                "line_no": 1,
                "voucher_date": "2026-12-31",
                "summary": "计提折旧",
                "account_code": "510101",
                "account_name": "制造费用_折旧费",
                "account_path": "制造费用/制造费用_折旧费",
                "account_nature": "成本",
                "counterparty_level1": "累计折旧",
                "match_type": "标准模板",
                "signed_minor": 100,
                "amount_minor": 100,
                "source_amount_minor": 100,
                "debit_minor": 100,
                "credit_minor": 0,
                "direction_sign": 1,
                "source_side": "debit",
                "effective_side": "debit",
                "fragment_index": 0,
                "processable": True,
                "technical_closing": False,
                "requires_ai": False,
                "business_event_id": "e-pair",
            }
        ]
    )
    spec = RunSpec(
        entity_name="甲公司",
        period_start="2026-01-01",
        period_end="2026-12-31",
        functional_currency="CNY",
        currency_scale=2,
        display_unit="元",
        net_profit_minor=0,
        performance_materiality_minor=50000,
        journal_preprocessing_confirmed=True,
        net_profit_user_confirmed=True,
        net_profit_source="user_provided",
        net_profit_source_note="测试客户直接提供净利润",
    )
    _, _, claims, _, _, _, _ = _family_results(
        fragments,
        pd.DataFrame(
            columns=[
                "account_code",
                "account_name",
                "account_path",
                "account_level",
                "parent_account_code",
                "is_leaf",
                "account_nature",
                "opening_debit_minor",
                "opening_credit_minor",
                "period_debit_minor",
                "period_credit_minor",
                "closing_debit_minor",
                "closing_credit_minor",
            ]
        ),
        pd.DataFrame(),
        spec,
        rule_book,
    )
    rule_ids = {
        (int(row["item_no"]), row["rule_id"])
        for row in claims.to_dict("records")
    }
    assert (3, "NONCASH-DEPRECIATION") in rule_ids
    assert (13, "WC-NONCASH-COST-OFFSET") in rule_ids
    for item_no, rule_id in rule_ids:
        _registered(rule_book, item_no, rule_id)
