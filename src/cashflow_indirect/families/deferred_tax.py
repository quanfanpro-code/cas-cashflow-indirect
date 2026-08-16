"""第11、12项递延所得税损益路径和余额桥接。"""

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
    "source",
    "source_fragment_id",
    "tax_entity_id",
    "tax_jurisdiction",
    "account_type",
    "balance_change_minor",
    "non_profit_change_minor",
    "profit_or_loss_effect_minor",
    "reason_code",
    "reason",
    "next_action",
]

_NON_PROFIT_ROUTES = {
    "other_comprehensive_income",
    "equity",
    "business_combination",
    "translation",
    "opening_adjustment",
    "prior_period_correction",
    "reclassification",
    "presentation_offset",
}


def _item_and_effect(account_type: str, balance_change_minor: int) -> tuple[int, int]:
    if account_type == "deferred_tax_asset":
        return 11, -balance_change_minor
    if account_type == "deferred_tax_liability":
        return 12, balance_change_minor
    raise ValueError(f"不支持的递延所得税科目类型：{account_type}")


def _claim(
    fragment_id: str,
    business_event_id: str,
    account_type: str,
    balance_change_minor: int,
    rule_id: str,
    reason: str,
) -> dict:
    item_no, effect = _item_and_effect(account_type, balance_change_minor)
    return {
        "fragment_id": fragment_id,
        "business_event_id": business_event_id,
        "item_no": item_no,
        "claim_amount_minor": effect,
        "decision": "included",
        "rule_id": rule_id,
        "reason": reason,
        "confidence": 1.0,
        "paired_offset_id": None,
    }


def _bridge(
    *,
    source: str,
    source_fragment_id: str,
    account_type: str,
    balance_change_minor: int,
    non_profit_change_minor: int,
    profit_or_loss_effect_minor: int,
    reason_code: str,
    reason: str,
    next_action: str = "",
    tax_entity_id: str = "",
    tax_jurisdiction: str = "",
) -> dict:
    return {
        "source": source,
        "source_fragment_id": source_fragment_id,
        "tax_entity_id": tax_entity_id,
        "tax_jurisdiction": tax_jurisdiction,
        "account_type": account_type,
        "balance_change_minor": balance_change_minor,
        "non_profit_change_minor": non_profit_change_minor,
        "profit_or_loss_effect_minor": profit_or_loss_effect_minor,
        "reason_code": reason_code,
        "reason": reason,
        "next_action": next_action,
    }


def _from_fragments(fragments: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    required = {
        "fragment_id",
        "account_type",
        "balance_change_minor",
        "route",
    }
    missing = sorted(required - set(fragments.columns))
    if missing:
        raise ValueError(f"递延所得税金额片段缺少字段：{missing}")

    claims: list[dict] = []
    bridge: list[dict] = []
    for _, row in fragments.iterrows():
        fragment_id = str(row["fragment_id"])
        event_id = str(row.get("business_event_id", ""))
        account_type = str(row["account_type"])
        balance_change = int(row["balance_change_minor"])
        item_no, profit_effect = _item_and_effect(account_type, balance_change)
        del item_no
        route = str(row["route"])

        if bool(row.get("technical_closing", False)):
            bridge.append(
                _bridge(
                    source="fragment",
                    source_fragment_id=fragment_id,
                    account_type=account_type,
                    balance_change_minor=balance_change,
                    non_profit_change_minor=balance_change,
                    profit_or_loss_effect_minor=0,
                    reason_code="TECHNICAL_CLOSING",
                    reason="技术性结转不影响当期递延所得税费用",
                )
            )
            continue
        if route == "profit_or_loss":
            if profit_effect:
                claims.append(
                    _claim(
                        fragment_id,
                        event_id,
                        account_type,
                        balance_change,
                        "TAX-DEFERRED-PROFIT-ROUTE",
                        "按业务事件最终进入所得税费用的递延所得税变动取数",
                    )
                )
            bridge.append(
                _bridge(
                    source="fragment",
                    source_fragment_id=fragment_id,
                    account_type=account_type,
                    balance_change_minor=balance_change,
                    non_profit_change_minor=0,
                    profit_or_loss_effect_minor=profit_effect,
                    reason_code="PROFIT_ROUTE",
                    reason="该变动最终进入当期所得税费用或收益",
                )
            )
        else:
            reason_code = (
                "NON_PROFIT_ROUTE"
                if route in _NON_PROFIT_ROUTES
                else "UNKNOWN_ROUTE"
            )
            bridge.append(
                _bridge(
                    source="fragment",
                    source_fragment_id=fragment_id,
                    account_type=account_type,
                    balance_change_minor=balance_change,
                    non_profit_change_minor=balance_change,
                    profit_or_loss_effect_minor=0,
                    reason_code=reason_code,
                    reason="该变动未进入当期净利润，不纳入第11、12项",
                    next_action=(
                        "ai_request" if reason_code == "UNKNOWN_ROUTE" else ""
                    ),
                )
            )
    return claims, bridge


def _from_trial_balance(trial_balance: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    required = {
        "tax_entity_id",
        "tax_jurisdiction",
        "account_type",
        "opening_gross_minor",
        "closing_gross_minor",
    }
    missing = sorted(required - set(trial_balance.columns))
    if missing:
        raise ValueError(f"递延所得税余额兜底缺少字段：{missing}")

    claims: list[dict] = []
    bridge: list[dict] = []
    for _, row in trial_balance.iterrows():
        entity = str(row["tax_entity_id"])
        jurisdiction = str(row["tax_jurisdiction"])
        account_type = str(row["account_type"])
        balance_change = int(row["closing_gross_minor"]) - int(
            row["opening_gross_minor"]
        )
        non_profit_change = int(row.get("non_profit_change_minor", 0))
        profit_balance_change = balance_change - non_profit_change
        item_no, profit_effect = _item_and_effect(
            account_type,
            profit_balance_change,
        )
        del item_no
        row_key = str(row.get("balance_row_id", "")) or (
            f"{entity}-{jurisdiction}-{account_type}"
        )
        fragment_id = f"tb-{row_key}"
        if profit_effect:
            claims.append(
                _claim(
                    fragment_id,
                    fragment_id,
                    account_type,
                    profit_balance_change,
                    "TAX-DEFERRED-BALANCE-FALLBACK",
                    "缺少精确损益片段时，按列报抵销前总额变动剔除非损益变化后兜底",
                )
            )
        bridge.append(
            _bridge(
                source="trial_balance_fallback",
                source_fragment_id=fragment_id,
                tax_entity_id=entity,
                tax_jurisdiction=jurisdiction,
                account_type=account_type,
                balance_change_minor=balance_change,
                non_profit_change_minor=non_profit_change,
                profit_or_loss_effect_minor=profit_effect,
                reason_code="GROSS_BALANCE_FALLBACK",
                reason="使用同一纳税主体和税收管辖区列报抵销前总额进行兜底",
            )
        )
    return claims, bridge


def calculate_deferred_tax(
    fragments: pd.DataFrame,
    trial_balance: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """优先使用损益路径，完全缺失时才使用余额表兜底。"""
    if fragments.empty:
        claims, bridge = _from_trial_balance(trial_balance)
    else:
        claims, bridge = _from_fragments(fragments)
    return (
        pd.DataFrame(claims, columns=_CLAIM_COLUMNS),
        pd.DataFrame(bridge, columns=_BRIDGE_COLUMNS),
    )
