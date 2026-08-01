import pandas as pd

import pytest

from cashflow_indirect.families.working_capital import calculate_working_capital


def balance_row(
    account_code,
    account_name,
    opening_minor,
    closing_minor,
    *,
    balance_side,
    business_nature="operating",
    asset_class="",
    period_debit_minor=0,
    period_credit_minor=0,
    nonoperating_net_increase_minor=0,
    translation_change_minor=0,
):
    debit_side = balance_side == "debit"
    return {
        "account_code": account_code,
        "account_name": account_name,
        "is_leaf": True,
        "business_nature": business_nature,
        "asset_class": asset_class,
        "opening_debit_minor": opening_minor if debit_side else 0,
        "opening_credit_minor": 0 if debit_side else opening_minor,
        "period_debit_minor": period_debit_minor,
        "period_credit_minor": period_credit_minor,
        "closing_debit_minor": closing_minor if debit_side else 0,
        "closing_credit_minor": 0 if debit_side else closing_minor,
        "nonoperating_net_increase_minor": nonoperating_net_increase_minor,
        "translation_change_minor": translation_change_minor,
    }


def empty_balance():
    return pd.DataFrame(
        columns=[
            "account_code",
            "account_name",
            "is_leaf",
            "business_nature",
            "asset_class",
            "opening_debit_minor",
            "opening_credit_minor",
            "period_debit_minor",
            "period_credit_minor",
            "closing_debit_minor",
            "closing_credit_minor",
            "nonoperating_net_increase_minor",
            "translation_change_minor",
        ]
    )


def test_inventory_writeoff_already_in_item_one_is_removed_from_inventory_change():
    inventory = pd.DataFrame(
        [
            balance_row(
                "1405",
                "库存商品原值",
                100000,
                0,
                balance_side="debit",
                asset_class="inventory",
            )
        ]
    )
    corrections = pd.DataFrame(
        [
            {
                "target_item_no": 13,
                "correction_minor": -40000,
                "reason_code": "ALLOWANCE_WRITEOFF",
            }
        ]
    )

    claims, bridge = calculate_working_capital(
        inventory,
        empty_balance(),
        empty_balance(),
        corrections=corrections,
    )

    assert claims.query("item_no == 13")["claim_amount_minor"].item() == 60000
    assert bridge.query("item_no == 13 and record_type == 'summary'")[
        "corrections_minor"
    ].item() == -40000


def test_direct_bad_debt_already_in_item_two_is_removed_from_receivable_change():
    receivable = pd.DataFrame(
        [
            balance_row(
                "1122",
                "应收账款原值",
                100000,
                0,
                balance_side="debit",
            )
        ]
    )
    corrections = pd.DataFrame(
        [
            {
                "target_item_no": 14,
                "correction_minor": -25000,
                "reason_code": "DIRECT_BAD_DEBT_ALREADY_ADJUSTED",
            }
        ]
    )

    claims, _ = calculate_working_capital(
        empty_balance(),
        receivable,
        empty_balance(),
        corrections=corrections,
    )

    assert claims.query("item_no == 14")["claim_amount_minor"].item() == 75000


def test_business_nature_and_non_cash_changes_control_items_14_and_15():
    receivable = pd.DataFrame(
        [
            balance_row(
                "122101",
                "经营押金",
                100000,
                50000,
                balance_side="debit",
                nonoperating_net_increase_minor=-20000,
            ),
            balance_row(
                "122102",
                "投资款",
                0,
                40000,
                balance_side="debit",
                business_nature="investment",
            ),
        ]
    )
    payable = pd.DataFrame(
        [
            balance_row(
                "220201",
                "货款",
                30000,
                70000,
                balance_side="credit",
            ),
            balance_row(
                "220202",
                "设备款",
                0,
                60000,
                balance_side="credit",
                business_nature="long_asset",
            ),
            balance_row(
                "220203",
                "供应链融资",
                10000,
                50000,
                balance_side="credit",
                business_nature="financing",
            ),
        ]
    )

    claims, bridge = calculate_working_capital(
        empty_balance(),
        receivable,
        payable,
        corrections=pd.DataFrame(),
    )

    assert claims.set_index("item_no")["claim_amount_minor"].to_dict() == {
        14: 30000,
        15: 40000,
    }
    assert set(bridge.query("record_type == 'excluded'")["account_code"]) == {
        "122102",
        "220202",
        "220203",
    }


def test_auxiliary_dimensions_replace_owner_total_instead_of_adding_again():
    receivable = pd.DataFrame(
        [
            balance_row(
                "1221",
                "其他应收款",
                100000,
                80000,
                balance_side="debit",
            )
        ]
    )
    auxiliary = pd.DataFrame(
        [
            {
                **balance_row(
                    "P01",
                    "员工备用金",
                    60000,
                    50000,
                    balance_side="debit",
                ),
                "owner_account_code": "1221",
                "dimension_type": "项目",
                "dimension_code": "P01",
            },
            {
                **balance_row(
                    "P02",
                    "投资款",
                    40000,
                    30000,
                    balance_side="debit",
                    business_nature="investment",
                ),
                "owner_account_code": "1221",
                "dimension_type": "项目",
                "dimension_code": "P02",
            },
        ]
    )

    claims, bridge = calculate_working_capital(
        empty_balance(),
        receivable,
        empty_balance(),
        corrections=pd.DataFrame(),
        auxiliary_dimensions=auxiliary,
    )

    assert claims.query("item_no == 14")["claim_amount_minor"].item() == 10000
    assert bridge.query("record_type == 'included'")["source_kind"].tolist() == [
        "auxiliary"
    ]


def test_auxiliary_dimensions_reject_period_amount_mismatch():
    receivable = pd.DataFrame(
        [
            balance_row(
                "1221",
                "其他应收款",
                100000,
                80000,
                balance_side="debit",
                period_debit_minor=50000,
            )
        ]
    )
    auxiliary = pd.DataFrame(
        [
            {
                **balance_row(
                    "P01",
                    "员工备用金",
                    100000,
                    80000,
                    balance_side="debit",
                    period_debit_minor=40000,
                ),
                "owner_account_code": "1221",
                "dimension_type": "项目",
                "dimension_code": "P01",
            }
        ]
    )

    with pytest.raises(ValueError, match="period_debit_minor"):
        calculate_working_capital(
            empty_balance(),
            receivable,
            empty_balance(),
            corrections=pd.DataFrame(),
            auxiliary_dimensions=auxiliary,
        )


def test_local_currency_fx_is_retained_translation_is_removed_and_grant_is_routed():
    receivable = pd.DataFrame(
        [
            balance_row(
                "112201",
                "经营性外币应收",
                100000,
                120000,
                balance_side="debit",
            ),
            balance_row(
                "112202",
                "境外经营折算应收",
                50000,
                80000,
                balance_side="debit",
                translation_change_minor=30000,
            ),
            balance_row(
                "112203",
                "应收政府补助",
                0,
                70000,
                balance_side="debit",
                business_nature="government_grant",
            ),
        ]
    )
    payable = pd.DataFrame(
        [
            balance_row(
                "2401",
                "递延收益",
                0,
                90000,
                balance_side="credit",
                business_nature="government_grant",
            )
        ]
    )

    claims, bridge = calculate_working_capital(
        empty_balance(),
        receivable,
        payable,
        corrections=pd.DataFrame(),
    )

    assert claims.query("item_no == 14")["claim_amount_minor"].item() == -20000
    assert 15 not in set(claims["item_no"])
    assert set(bridge.query("target_item_no == 16")["account_code"]) == {
        "112203",
        "2401",
    }


def test_inventory_scope_includes_special_operating_assets_and_keeps_ordinary_loss():
    inventory = pd.DataFrame(
        [
            balance_row(
                "1401",
                "原材料",
                100000,
                80000,
                balance_side="debit",
                asset_class="inventory",
            ),
            balance_row(
                "1421",
                "消耗性生物资产",
                50000,
                40000,
                balance_side="debit",
                asset_class="consumable_biological_asset",
            ),
            balance_row(
                "1409",
                "开发产品",
                200000,
                170000,
                balance_side="debit",
                asset_class="real_estate_development_inventory",
            ),
            balance_row(
                "1451",
                "经营性合同成本",
                30000,
                25000,
                balance_side="debit",
                asset_class="operating_contract_cost",
            ),
        ]
    )

    claims, _ = calculate_working_capital(
        inventory,
        empty_balance(),
        empty_balance(),
        corrections=pd.DataFrame(),
    )

    assert claims.query("item_no == 13")["claim_amount_minor"].item() == 65000
