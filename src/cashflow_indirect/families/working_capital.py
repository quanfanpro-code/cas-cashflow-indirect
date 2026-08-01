"""第13至15项经营性营运资本余额桥接。"""

import pandas as pd


_CLAIM_COLUMNS = [
    "fragment_id",
    "business_event_id",
    "item_no",
    "claim_amount_minor",
    "decision",
    "rule_id",
    "reason",
    "confidence",
    "paired_offset_id",
]

_BRIDGE_COLUMNS = [
    "item_no",
    "record_type",
    "source_kind",
    "account_code",
    "owner_account_code",
    "opening_minor",
    "closing_minor",
    "base_change_minor",
    "nonoperating_correction_minor",
    "corrections_minor",
    "final_amount_minor",
    "target_item_no",
    "reason_code",
    "reason",
]

_BALANCE_COLUMNS = {
    "account_code",
    "opening_debit_minor",
    "opening_credit_minor",
    "period_debit_minor",
    "period_credit_minor",
    "closing_debit_minor",
    "closing_credit_minor",
}

_OPERATING_NATURES = {"", "operating"}

_INVENTORY_ASSETS = {
    "",
    "inventory",
    "consumable_biological_asset",
    "real_estate_development_inventory",
    "operating_contract_cost",
    "harvested_agricultural_produce",
}


def _require_balance(frame: pd.DataFrame, source_name: str) -> None:
    if frame.empty:
        return
    missing = sorted(_BALANCE_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"{source_name}缺少字段：{missing}")


def _balance(frame: pd.DataFrame, liability: bool) -> tuple[pd.Series, pd.Series]:
    if liability:
        opening = frame["opening_credit_minor"] - frame["opening_debit_minor"]
        closing = frame["closing_credit_minor"] - frame["closing_debit_minor"]
    else:
        opening = frame["opening_debit_minor"] - frame["opening_credit_minor"]
        closing = frame["closing_debit_minor"] - frame["closing_credit_minor"]
    return opening.astype("int64"), closing.astype("int64")


def _rows_with_auxiliary(
    balances: pd.DataFrame,
    auxiliary_dimensions: pd.DataFrame,
    source_name: str,
) -> pd.DataFrame:
    _require_balance(balances, source_name)
    if balances.empty:
        return balances.copy()

    rows = balances.copy()
    if "is_leaf" in rows:
        rows = rows.loc[rows["is_leaf"].fillna(False).astype(bool)].copy()
    rows["source_kind"] = "account"
    rows["owner_account_code"] = ""
    if auxiliary_dimensions.empty:
        return rows

    required_aux = (_BALANCE_COLUMNS - {"account_code"}) | {
        "owner_account_code"
    }
    missing = sorted(required_aux - set(auxiliary_dimensions.columns))
    if "account_code" not in auxiliary_dimensions and (
        "dimension_code" not in auxiliary_dimensions
    ):
        missing.append("account_code或dimension_code")
    if missing:
        raise ValueError(f"辅助维度缺少字段：{missing}")
    owners = set(rows["account_code"].astype(str))
    aux = auxiliary_dimensions.loc[
        auxiliary_dimensions["owner_account_code"].astype(str).isin(owners)
    ].copy()
    if aux.empty:
        return rows

    for owner, owner_aux in aux.groupby("owner_account_code", sort=False):
        parent = rows.loc[rows["account_code"].astype(str) == str(owner)].iloc[0]
        for column in (
            "opening_debit_minor",
            "opening_credit_minor",
            "period_debit_minor",
            "period_credit_minor",
            "closing_debit_minor",
            "closing_credit_minor",
        ):
            if int(owner_aux[column].sum()) != int(parent[column]):
                raise ValueError(f"辅助维度与所属科目{owner}的{column}合计不一致")

    rows = rows.loc[
        ~rows["account_code"].astype(str).isin(set(aux["owner_account_code"].astype(str)))
    ].copy()
    aux["source_kind"] = "auxiliary"
    if "dimension_code" in aux:
        aux["account_code"] = aux["dimension_code"].astype(str)
    else:
        aux["account_code"] = aux.index.map(lambda value: f"aux-{value}")
    if "account_name" not in aux:
        aux["account_name"] = aux.get("dimension_name", "")

    owner_defaults = balances.set_index(balances["account_code"].astype(str))
    for field, default in (
        ("business_nature", "operating"),
        ("asset_class", ""),
        ("nonoperating_net_increase_minor", 0),
        ("translation_change_minor", 0),
    ):
        if field not in aux:
            aux[field] = [
                owner_defaults.loc[str(owner)].get(field, default)
                for owner in aux["owner_account_code"]
            ]
        else:
            missing_value = aux[field].isna() | aux[field].astype(str).eq("")
            if missing_value.any():
                aux.loc[missing_value, field] = [
                    owner_defaults.loc[str(owner)].get(field, default)
                    for owner in aux.loc[missing_value, "owner_account_code"]
                ]
    return pd.concat([rows, aux], ignore_index=True, sort=False)


def _bridge_row(
    *,
    item_no: int,
    record_type: str,
    source_kind: str = "",
    account_code: str = "",
    owner_account_code: str = "",
    opening_minor: int = 0,
    closing_minor: int = 0,
    base_change_minor: int = 0,
    nonoperating_correction_minor: int = 0,
    corrections_minor: int = 0,
    final_amount_minor: int = 0,
    target_item_no: int | None = None,
    reason_code: str = "",
    reason: str = "",
) -> dict:
    return {
        "item_no": item_no,
        "record_type": record_type,
        "source_kind": source_kind,
        "account_code": account_code,
        "owner_account_code": owner_account_code,
        "opening_minor": opening_minor,
        "closing_minor": closing_minor,
        "base_change_minor": base_change_minor,
        "nonoperating_correction_minor": nonoperating_correction_minor,
        "corrections_minor": corrections_minor,
        "final_amount_minor": final_amount_minor,
        "target_item_no": target_item_no,
        "reason_code": reason_code,
        "reason": reason,
    }


def _calculate_pool(
    rows: pd.DataFrame,
    item_no: int,
    liability: bool,
) -> tuple[int, list[dict]]:
    if rows.empty:
        return 0, []
    opening, closing = _balance(rows, liability)
    records: list[dict] = []
    total = 0
    for position, (_, row) in enumerate(rows.iterrows()):
        account_code = str(row["account_code"])
        owner = str(row.get("owner_account_code", ""))
        source_kind = str(row.get("source_kind", "account"))
        business_nature = str(row.get("business_nature", "operating") or "operating")
        open_amount = int(opening.iloc[position])
        close_amount = int(closing.iloc[position])

        if item_no == 13 and str(row.get("asset_class", "")) not in _INVENTORY_ASSETS:
            business_nature = "non_inventory"
        if business_nature not in _OPERATING_NATURES:
            target = 16 if business_nature == "government_grant" else None
            records.append(
                _bridge_row(
                    item_no=item_no,
                    record_type="excluded",
                    source_kind=source_kind,
                    account_code=account_code,
                    owner_account_code=owner,
                    opening_minor=open_amount,
                    closing_minor=close_amount,
                    target_item_no=target,
                    reason_code="GOVERNMENT_GRANT_TO_ITEM16"
                    if target == 16
                    else "NONOPERATING_BALANCE",
                    reason="政府补助余额转第16项专门桥接"
                    if target == 16
                    else "按业务实质判定为非经营性余额，不进入营运资本池",
                )
            )
            continue

        base_change = (
            close_amount - open_amount if liability else open_amount - close_amount
        )
        nonoperating_increase = int(row.get("nonoperating_net_increase_minor", 0))
        translation_change = int(row.get("translation_change_minor", 0))
        correction = (
            -nonoperating_increase - translation_change
            if liability
            else nonoperating_increase + translation_change
        )
        amount = base_change + correction
        total += amount
        records.append(
            _bridge_row(
                item_no=item_no,
                record_type="included",
                source_kind=source_kind,
                account_code=account_code,
                owner_account_code=owner,
                opening_minor=open_amount,
                closing_minor=close_amount,
                base_change_minor=base_change,
                nonoperating_correction_minor=correction,
                final_amount_minor=amount,
                reason_code="OPERATING_BALANCE_INCLUDED",
                reason="本位币经营性余额变动保留，非现金或境外折算变化按明确金额剔除",
            )
        )
    return total, records


def _corrections(corrections: pd.DataFrame) -> dict[int, int]:
    if corrections.empty:
        return {13: 0, 14: 0, 15: 0}
    required = {"target_item_no", "correction_minor"}
    missing = sorted(required - set(corrections.columns))
    if missing:
        raise ValueError(f"营运资本修正缺少字段：{missing}")
    result = {13: 0, 14: 0, 15: 0}
    for item_no, amount in corrections.groupby("target_item_no")[
        "correction_minor"
    ].sum().items():
        numeric_item = int(item_no)
        if numeric_item in result:
            result[numeric_item] = int(amount)
    return result


def calculate_working_capital(
    inventory_balance: pd.DataFrame,
    receivable_balance: pd.DataFrame,
    payable_balance: pd.DataFrame,
    *,
    corrections: pd.DataFrame,
    auxiliary_dimensions: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """按末级科目或其辅助明细计算第13至15项。"""
    auxiliary = (
        auxiliary_dimensions
        if auxiliary_dimensions is not None
        else pd.DataFrame()
    )
    pools = {
        13: _rows_with_auxiliary(inventory_balance, auxiliary, "存货余额"),
        14: _rows_with_auxiliary(receivable_balance, auxiliary, "经营性应收余额"),
        15: _rows_with_auxiliary(payable_balance, auxiliary, "经营性应付余额"),
    }
    correction_totals = _corrections(corrections)
    claims: list[dict] = []
    bridge: list[dict] = []
    for item_no, rows in pools.items():
        base_total, records = _calculate_pool(
            rows,
            item_no,
            liability=item_no == 15,
        )
        bridge.extend(records)
        correction = correction_totals[item_no]
        final_amount = base_total + correction
        bridge.append(
            _bridge_row(
                item_no=item_no,
                record_type="summary",
                base_change_minor=base_total,
                corrections_minor=correction,
                final_amount_minor=final_amount,
                reason_code="WORKING_CAPITAL_SUMMARY",
                reason="经营性原值变动加明确的非现金防重复修正",
            )
        )
        if final_amount:
            claims.append(
                {
                    "fragment_id": f"working-capital-item-{item_no}",
                    "business_event_id": f"working-capital-item-{item_no}",
                    "item_no": item_no,
                    "claim_amount_minor": final_amount,
                    "decision": "included",
                    "rule_id": f"WC-ITEM-{item_no}",
                    "reason": "按经营性余额桥接和已确认修正汇总取数",
                    "confidence": 1.0,
                    "paired_offset_id": None,
                }
            )
    return (
        pd.DataFrame(claims, columns=_CLAIM_COLUMNS),
        pd.DataFrame(bridge, columns=_BRIDGE_COLUMNS),
    )
