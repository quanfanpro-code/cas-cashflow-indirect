"""第1、2项减值分类及第13、14项核销修正。"""

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

_RECORD_COLUMNS = [
    "record_type",
    "target_item_no",
    "correction_minor",
    "reason_code",
    "source_fragment_id",
    "reason",
    "next_action",
]

_CREDIT_ASSETS = {
    "receivable",
    "note_receivable",
    "other_receivable",
    "loan",
    "debt_investment",
    "other_debt_investment",
    "lease_receivable",
    "financial_guarantee",
    "loan_commitment",
}

_LONG_LIVED_ASSETS = {
    "fixed_asset",
    "intangible_asset",
    "goodwill",
    "long_term_equity_investment",
    "investment_property_cost",
    "construction_in_progress",
    "construction_materials",
    "right_of_use_asset",
    "oil_gas_asset",
    "productive_biological_asset",
}

_EXCLUDED_EVENTS = {
    "business_combination",
    "subsidiary_disposal",
    "translation",
    "reclassification",
    "opening_adjustment",
    "prior_period_correction",
    "disposal",
    "oci_change",
    "recovery_of_writeoff",
    "closing",
}


def _record(
    record_type: str,
    source_fragment_id: str,
    reason_code: str,
    reason: str,
    target_item_no: int | None = None,
    correction_minor: int | None = None,
    next_action: str = "",
) -> dict:
    return {
        "record_type": record_type,
        "target_item_no": target_item_no,
        "correction_minor": correction_minor,
        "reason_code": reason_code,
        "source_fragment_id": source_fragment_id,
        "reason": reason,
        "next_action": next_action,
    }


def _working_capital_target(asset_class: str) -> int | None:
    if asset_class in {"inventory", "contract_cost", "consumable_biological_asset"}:
        return 13
    if asset_class in _CREDIT_ASSETS:
        return 14
    return None


def classify_impairment(
    fragments: pd.DataFrame,
    events: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    del events
    required = {
        "fragment_id",
        "account_name",
        "signed_minor",
        "asset_class",
        "event_kind",
    }
    missing = sorted(required - set(fragments.columns))
    if missing:
        raise ValueError(f"减值分类缺少字段：{missing}")

    claims: list[dict] = []
    records: list[dict] = []
    for _, row in fragments.iterrows():
        fragment_id = str(row["fragment_id"])
        event_kind = str(row.get("event_kind", ""))
        asset_class = str(row.get("asset_class", ""))
        technical_closing = bool(row.get("technical_closing", False))
        affects_profit = bool(row.get("affects_profit_or_loss", False))
        amount = int(
            row.get("profit_or_loss_effect_minor", row["signed_minor"])
        )

        if technical_closing or event_kind in _EXCLUDED_EVENTS:
            records.append(
                _record(
                    "exclusion",
                    fragment_id,
                    "NON_PROFIT_OR_TECHNICAL_CHANGE",
                    "该变动未进入当期净利润或属于技术性结转",
                )
            )
            continue

        if event_kind == "writeoff":
            target = _working_capital_target(asset_class)
            if target:
                records.append(
                    _record(
                        "correction",
                        fragment_id,
                        "ALLOWANCE_WRITEOFF",
                        "准备核销原值不进入减值计提额，并修正营运资本原值变动",
                        target_item_no=target,
                        correction_minor=-abs(int(row["signed_minor"])),
                    )
                )
            else:
                records.append(
                    _record(
                        "issue",
                        fragment_id,
                        "WRITEOFF_TARGET_UNKNOWN",
                        "核销对应的资产原值项目不明确",
                        next_action="ai_request",
                    )
                )
            continue

        account_name = str(row.get("account_name", ""))
        model = str(row.get("impairment_model", "") or "")
        if asset_class == "contract_asset":
            model = "asset"
        elif not model:
            if "信用减值损失" in account_name or asset_class in _CREDIT_ASSETS:
                model = "credit"
            elif "资产减值损失" in account_name:
                model = "asset"

        loss_line = (
            "资产减值损失" in account_name
            or "信用减值损失" in account_name
            or event_kind == "direct_loss"
        )
        if not affects_profit or not loss_line:
            records.append(
                _record(
                    "exclusion",
                    fragment_id,
                    "NO_PROFIT_OR_LOSS_EFFECT",
                    "本金额片段未形成当期损益中的减值损失或利得",
                )
            )
            continue
        if model not in {"asset", "credit"}:
            records.append(
                _record(
                    "issue",
                    fragment_id,
                    "AMBIGUOUS_IMPAIRMENT_MODEL",
                    "无法唯一确定适用资产减值模型还是信用减值模型",
                    next_action="ai_request",
                )
            )
            continue

        item_no = 1 if model == "asset" else 2
        rule_id = "IMP-ASSET" if item_no == 1 else "IMP-CREDIT"
        claims.append(
            {
                "fragment_id": fragment_id,
                "business_event_id": str(row.get("business_event_id", "")),
                "item_no": item_no,
                "claim_amount_minor": amount,
                "decision": "included",
                "rule_id": rule_id,
                "reason": "取当期损益中的减值计提、转回或直接减值净额",
                "confidence": 1.0,
                "paired_offset_id": None,
            }
        )

        if event_kind == "direct_loss":
            target = _working_capital_target(asset_class)
            if target:
                reason_code = (
                    "DIRECT_BAD_DEBT_ALREADY_ADJUSTED"
                    if target == 14
                    else "DIRECT_ASSET_LOSS_ALREADY_ADJUSTED"
                )
                records.append(
                    _record(
                        "correction",
                        fragment_id,
                        reason_code,
                        "直接减值已经在减值项目调整，原值减少需从营运资本变动剔除",
                        target_item_no=target,
                        correction_minor=-abs(amount),
                    )
                )

        if (
            item_no == 1
            and event_kind == "reversal"
            and asset_class in _LONG_LIVED_ASSETS
        ):
            records.append(
                _record(
                    "issue",
                    fragment_id,
                    "PROHIBITED_LONG_LIVED_REVERSAL",
                    "适用企业会计准则第8号的长期资产减值通常不得转回；本期实际损益仍按负数调整",
                    next_action="accounting_error_review",
                )
            )

    return (
        pd.DataFrame(claims, columns=_CLAIM_COLUMNS),
        pd.DataFrame(records, columns=_RECORD_COLUMNS),
    )
