"""第16项其他明确调整的严格白名单和事件桥接。"""

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
    "business_event_id",
    "event_type",
    "source_fragment_id",
    "operating_cash_minor",
    "profit_effect_minor",
    "occupied_minor",
    "adjustment_minor",
    "target_item_no",
    "reason_code",
    "reason",
    "next_action",
]

_DEBT_RESTRUCTURING_RELATED_ITEMS = {6, 8, 10, 13, 14, 15}


def _claim(
    fragment_id: str,
    business_event_id: str,
    amount_minor: int,
    rule_id: str,
    reason: str,
) -> dict:
    return {
        "fragment_id": fragment_id,
        "business_event_id": business_event_id,
        "item_no": 16,
        "claim_amount_minor": amount_minor,
        "decision": "included",
        "rule_id": rule_id,
        "reason": reason,
        "confidence": 1.0,
        "paired_offset_id": None,
    }


def _bridge(
    business_event_id: str,
    event_type: str,
    source_fragment_id: str,
    *,
    operating_cash_minor: int = 0,
    profit_effect_minor: int = 0,
    occupied_minor: int = 0,
    adjustment_minor: int = 0,
    target_item_no: int | None = None,
    reason_code: str,
    reason: str,
    next_action: str = "",
) -> dict:
    return {
        "business_event_id": business_event_id,
        "event_type": event_type,
        "source_fragment_id": source_fragment_id,
        "operating_cash_minor": operating_cash_minor,
        "profit_effect_minor": profit_effect_minor,
        "occupied_minor": occupied_minor,
        "adjustment_minor": adjustment_minor,
        "target_item_no": target_item_no,
        "reason_code": reason_code,
        "reason": reason,
        "next_action": next_action,
    }


def _occupied_by_related_items(
    related_occupancy: pd.DataFrame,
    fragment_id: str,
) -> int:
    if related_occupancy.empty:
        return 0
    required = {"fragment_id", "item_no", "physical_consumed_minor"}
    missing = sorted(required - set(related_occupancy.columns))
    if missing:
        raise ValueError(f"相关项目占用记录缺少字段：{missing}")
    matched = related_occupancy.loc[
        related_occupancy["fragment_id"].astype(str).eq(fragment_id)
        & related_occupancy["item_no"].isin(_DEBT_RESTRUCTURING_RELATED_ITEMS)
    ]
    return int(matched["physical_consumed_minor"].sum())


def _event_adjustments(
    events: pd.DataFrame,
    related_occupancy: pd.DataFrame,
) -> tuple[list[dict], list[dict], set[str]]:
    if events.empty:
        return [], [], set()
    base_required = {
        "business_event_id",
        "event_type",
        "source_fragment_id",
        "source_evidence",
    }
    missing = sorted(base_required - set(events.columns))
    if missing:
        raise ValueError(f"第16项事件缺少字段：{missing}")

    claims: list[dict] = []
    bridge: list[dict] = []
    handled_fragments: set[str] = set()
    for _, row in events.iterrows():
        event_id = str(row["business_event_id"])
        event_type = str(row["event_type"])
        fragment_id = str(row["source_fragment_id"])
        handled_fragments.add(fragment_id)
        has_evidence = bool(row["source_evidence"])

        if event_type == "government_grant":
            required = {"operating_cash_minor", "profit_effect_minor"}
            missing = sorted(required - set(events.columns))
            if missing:
                raise ValueError(f"政府补助事件缺少字段：{missing}")
            cash = int(row["operating_cash_minor"])
            profit = int(row["profit_effect_minor"])
            adjustment = cash - profit
            if not has_evidence:
                bridge.append(
                    _bridge(
                        event_id,
                        event_type,
                        fragment_id,
                        operating_cash_minor=cash,
                        profit_effect_minor=profit,
                        reason_code="SOURCE_EVIDENCE_MISSING",
                        reason="政府补助现金和损益缺少来源证据，暂不落数",
                        next_action="ai_request",
                    )
                )
                continue
            bridge.append(
                _bridge(
                    event_id,
                    event_type,
                    fragment_id,
                    operating_cash_minor=cash,
                    profit_effect_minor=profit,
                    adjustment_minor=adjustment,
                    target_item_no=16,
                    reason_code="GOVERNMENT_GRANT_BRIDGE",
                    reason="经营活动政府补助现金减去政府补助对净利润的净影响",
                )
            )
            if adjustment:
                claims.append(
                    _claim(
                        fragment_id,
                        event_id,
                        adjustment,
                        "OTHER-GOVERNMENT-GRANT",
                        "按政府补助现金和损益事件桥接净额取数",
                    )
                )
        elif event_type == "debt_restructuring":
            if "profit_effect_minor" not in events:
                raise ValueError("债务重组事件缺少字段：['profit_effect_minor']")
            profit = int(row["profit_effect_minor"])
            if bool(row.get("shareholder_related", False)):
                bridge.append(
                    _bridge(
                        event_id,
                        event_type,
                        fragment_id,
                        profit_effect_minor=profit,
                        reason_code="SHAREHOLDER_EQUITY_TRANSACTION",
                        reason="基于股东身份的权益性投入或分配不按普通债务重组处理",
                    )
                )
                continue
            if not has_evidence:
                bridge.append(
                    _bridge(
                        event_id,
                        event_type,
                        fragment_id,
                        profit_effect_minor=profit,
                        reason_code="SOURCE_EVIDENCE_MISSING",
                        reason="债务重组损益缺少来源证据，暂不落数",
                        next_action="ai_request",
                    )
                )
                continue
            occupied = _occupied_by_related_items(
                related_occupancy,
                fragment_id,
            )
            if occupied > abs(profit):
                bridge.append(
                    _bridge(
                        event_id,
                        event_type,
                        fragment_id,
                        profit_effect_minor=profit,
                        occupied_minor=occupied,
                        reason_code="RELATED_OCCUPANCY_EXCEEDS_PROFIT",
                        reason="相关项目占用金额超过债务重组损益片段",
                        next_action="fix_occupancy",
                    )
                )
                continue
            remainder = (1 if profit >= 0 else -1) * (abs(profit) - occupied)
            bridge.append(
                _bridge(
                    event_id,
                    event_type,
                    fragment_id,
                    profit_effect_minor=profit,
                    occupied_minor=occupied,
                    adjustment_minor=remainder,
                    target_item_no=16 if remainder else None,
                    reason_code="DEBT_RESTRUCTURING_REMAINDER",
                    reason="仅取第6、8、10、13、14、15项尚未抵销的剩余损益",
                )
            )
            if remainder:
                claims.append(
                    _claim(
                        fragment_id,
                        event_id,
                        remainder,
                        "OTHER-DEBT-RESTRUCTURING-REMAINDER",
                        "取其他项目尚未抵销的债务重组剩余损益",
                    )
                )
    return claims, bridge, handled_fragments


def _share_payment_and_rejections(
    fragments: pd.DataFrame,
    handled_fragments: set[str],
) -> tuple[list[dict], list[dict]]:
    if fragments.empty:
        return [], []
    required = {
        "fragment_id",
        "event_kind",
        "signed_minor",
        "cost_destination",
        "source_evidence",
    }
    missing = sorted(required - set(fragments.columns))
    if missing:
        raise ValueError(f"第16项金额片段缺少字段：{missing}")

    claims: list[dict] = []
    bridge: list[dict] = []
    for _, row in fragments.iterrows():
        fragment_id = str(row["fragment_id"])
        if fragment_id in handled_fragments:
            continue
        event_id = str(row.get("business_event_id", ""))
        event_kind = str(row["event_kind"])
        amount = int(row["signed_minor"])
        destination = str(row["cost_destination"])

        if not bool(row["source_evidence"]):
            bridge.append(
                _bridge(
                    event_id,
                    event_kind,
                    fragment_id,
                    reason_code="NOT_WHITELISTED",
                    reason="没有白名单事件及来源证据，不允许作为第16项倒轧数",
                    next_action="ai_request",
                )
            )
            continue
        if event_kind == "equity_settled_share_payment":
            if destination == "profit_or_loss":
                claims.append(
                    _claim(
                        fragment_id,
                        event_id,
                        amount,
                        "OTHER-EQUITY-SETTLED-SHARE-PAYMENT",
                        "权益结算股份支付实际进入当期净利润的费用予以加回",
                    )
                )
                bridge.append(
                    _bridge(
                        event_id,
                        event_kind,
                        fragment_id,
                        profit_effect_minor=amount,
                        adjustment_minor=amount,
                        target_item_no=16,
                        reason_code="EQUITY_SHARE_PAYMENT_PROFIT",
                        reason="权益结算股份支付费用进入当期损益",
                    )
                )
            elif destination == "inventory":
                bridge.append(
                    _bridge(
                        event_id,
                        event_kind,
                        fragment_id,
                        adjustment_minor=amount,
                        target_item_no=13,
                        reason_code="EQUITY_SHARE_PAYMENT_INVENTORY",
                        reason="进入尚未出售存货的非现金股份支付成本转第13项修正",
                    )
                )
            else:
                bridge.append(
                    _bridge(
                        event_id,
                        event_kind,
                        fragment_id,
                        reason_code="EQUITY_SHARE_PAYMENT_CAPITALIZED",
                        reason="资本化进入非经营性长期资产的股份支付当期不调整",
                    )
                )
        elif event_kind == "cash_settled_share_payment":
            bridge.append(
                _bridge(
                    event_id,
                    event_kind,
                    fragment_id,
                    reason_code="CASH_SETTLED_SHARE_PAYMENT_EXCLUDED",
                    reason="现金结算股份支付通过经营性负债和实际现金支付处理",
                )
            )
        else:
            bridge.append(
                _bridge(
                    event_id,
                    event_kind,
                    fragment_id,
                    reason_code="NOT_WHITELISTED",
                    reason="事项不在第16项严格白名单内，不允许接收倒轧差额",
                    next_action="ai_request",
                )
            )
    return claims, bridge


def calculate_other_adjustments(
    fragments: pd.DataFrame,
    events: pd.DataFrame,
    related_occupancy: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """计算政府补助、权益结算股份支付和债务重组剩余调整。"""
    event_claims, event_bridge, handled = _event_adjustments(
        events,
        related_occupancy,
    )
    fragment_claims, fragment_bridge = _share_payment_and_rejections(
        fragments,
        handled,
    )
    return (
        pd.DataFrame(
            event_claims + fragment_claims,
            columns=_CLAIM_COLUMNS,
        ),
        pd.DataFrame(
            event_bridge + fragment_bridge,
            columns=_BRIDGE_COLUMNS,
        ),
    )
