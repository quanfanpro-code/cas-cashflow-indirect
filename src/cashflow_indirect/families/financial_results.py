"""第8至10项公允价值、财务费用和投资损益分类。"""

import pandas as pd


_COLUMNS = [
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

_ITEM_8_EVENTS = {
    "fair_value_remeasurement",
    "cash_flow_hedge_ineffective",
    "contingent_consideration_remeasurement",
    "investment_property_fair_value_remeasurement",
    "derivative_fair_value_remeasurement",
}

_ITEM_9_EVENTS = {
    "financing_interest",
    "lease_liability_interest",
    "financing_fee",
    "effective_interest_amortization",
    "investment_interest",
    "investment_fx",
    "financing_fx",
    "cash_equivalent_fx",
    "nonoperating_discount_unwinding",
}

_ITEM_10_EVENTS = {
    "equity_method_result",
    "investment_dividend",
    "financial_asset_disposal",
    "long_term_equity_disposal",
    "subsidiary_disposal",
    "loss_of_control",
    "other_investment_result",
}


def _claim(
    row: pd.Series,
    item_no: int | None,
    rule_id: str,
    reason: str,
    *,
    decision: str = "included",
    confidence: float = 1.0,
) -> dict:
    return {
        "fragment_id": str(row["fragment_id"]),
        "business_event_id": str(row.get("business_event_id", "")),
        "item_no": item_no,
        "claim_amount_minor": int(row["signed_minor"]),
        "decision": decision,
        "rule_id": rule_id,
        "reason": reason,
        "confidence": confidence,
        "paired_offset_id": None,
    }


def classify_financial_results(
    fragments: pd.DataFrame,
    events: pd.DataFrame,
    financial_entity_flag: bool = False,
) -> pd.DataFrame:
    """按实际损益片段分类第8至10项。"""
    del events
    required = {
        "fragment_id",
        "signed_minor",
        "event_kind",
        "affects_profit_or_loss",
    }
    missing = sorted(required - set(fragments.columns))
    if missing:
        raise ValueError(f"金融损益分类缺少字段：{missing}")

    claims: list[dict] = []
    for _, row in fragments.iterrows():
        if bool(row.get("technical_closing", False)) or not bool(
            row["affects_profit_or_loss"]
        ):
            continue

        if financial_entity_flag and bool(row.get("core_financial_business", False)):
            claims.append(
                _claim(
                    row,
                    None,
                    "FINANCIAL_ENTITY_RULE_NOT_SUPPORTED",
                    "金融企业核心金融业务不能套用一般企业规则，当前版本阻断并保留金额片段",
                    decision="blocked",
                    confidence=0.0,
                )
            )
            continue

        event_kind = str(row["event_kind"])
        if event_kind in _ITEM_8_EVENTS:
            claims.append(
                _claim(
                    row,
                    8,
                    "FIN-FAIR-VALUE",
                    "取实际计入当期损益的纯公允价值重新计量或套期无效部分",
                )
            )
        elif event_kind in _ITEM_9_EVENTS:
            rule_id = (
                "FIN-CASH-FX"
                if event_kind == "cash_equivalent_fx"
                else "FIN-NONOPERATING-FINANCE-COST"
            )
            claims.append(
                _claim(
                    row,
                    9,
                    rule_id,
                    "取不属于经营活动且实际进入净利润的融资、投资相关财务损益",
                )
            )
        elif event_kind in _ITEM_10_EVENTS:
            claims.append(
                _claim(
                    row,
                    10,
                    "FIN-INVESTMENT-RESULT",
                    "取权益法、投资收益及投资处置事件实际进入净利润的净额",
                )
            )

    return pd.DataFrame(claims, columns=_COLUMNS)
