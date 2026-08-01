"""第3至7项折旧摊销及长期资产处置分类。"""

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

_ROUTING_COLUMNS = [
    "record_type",
    "target_item_no",
    "amount_minor",
    "reason_code",
    "source_fragment_id",
    "business_event_id",
    "reason",
    "next_action",
    "paired_offset_id",
]

_DEPRECIABLE_ASSETS = {
    "fixed_asset",
    "right_of_use_asset",
    "investment_property_cost",
    "investment_property_fair_value",
    "productive_biological_asset",
    "oil_gas_asset",
}

_AMORTIZABLE_ASSETS = {
    "intangible_asset",
    "investment_property_land_cost",
    "data_resource_intangible",
}

_OPERATING_DESTINATIONS = {
    "profit_or_loss",
    "inventory",
    "operating_contract_cost",
}

_NONOPERATING_DESTINATIONS = {
    "construction_in_progress": "进入在建工程",
    "capitalized_development": "进入资本化开发支出",
    "nonoperating_long_lived_asset": "进入其他非经营性长期资产",
}

_ABNORMAL_DEPRECIATION = {
    "fair_value_model": (
        "FAIR_VALUE_PROPERTY_DEPRECIATION",
        "公允价值模式投资性房地产不应计提折旧；实际已影响损益或经营性资产成本的金额仍予调整",
    ),
    "held_for_sale": (
        "HELD_FOR_SALE_DEPRECIATION",
        "持有待售期间不应计提折旧；实际已影响损益或经营性资产成本的金额仍予调整",
    ),
    "fully_depreciated": (
        "FULLY_DEPRECIATED_ASSET_DEPRECIATION",
        "已提足折旧仍继续使用的资产不应继续计提折旧；实际影响仍予调整",
    ),
    "land": (
        "LAND_DEPRECIATION",
        "单独计价入账的土地不应计提折旧；实际影响仍予调整",
    ),
}

_ITEM_6_EVENT_TYPES = {
    "long_asset_sale",
    "long_asset_transfer",
    "intangible_no_future_benefit",
    "construction_in_progress_disposal",
    "investment_property_disposal",
    "right_of_use_termination",
    "productive_biological_asset_disposal",
    "oil_gas_asset_disposal",
    "nonmonetary_long_asset_exchange",
    "debt_restructuring_long_asset_disposal",
}

_ITEM_6_ASSETS = {
    "fixed_asset",
    "intangible_asset",
    "construction_in_progress",
    "investment_property_cost",
    "investment_property_fair_value",
    "right_of_use_asset",
    "productive_biological_asset",
    "oil_gas_asset",
    "other_nonfinancial_long_lived_asset",
}

_ITEM_7_EVENT_TYPES = {
    "fixed_asset_damage",
    "fixed_asset_scrap",
    "fixed_asset_shortage",
}


def _claim(
    fragment_id: str,
    business_event_id: str,
    item_no: int,
    amount_minor: int,
    rule_id: str,
    reason: str,
    paired_offset_id: str | None = None,
) -> dict:
    return {
        "fragment_id": fragment_id,
        "business_event_id": business_event_id,
        "item_no": item_no,
        "claim_amount_minor": amount_minor,
        "decision": "included",
        "rule_id": rule_id,
        "reason": reason,
        "confidence": 1.0,
        "paired_offset_id": paired_offset_id,
    }


def _routing(
    record_type: str,
    source_fragment_id: str,
    business_event_id: str,
    reason_code: str,
    reason: str,
    *,
    target_item_no: int | None = None,
    amount_minor: int | None = None,
    next_action: str = "",
    paired_offset_id: str | None = None,
) -> dict:
    return {
        "record_type": record_type,
        "target_item_no": target_item_no,
        "amount_minor": amount_minor,
        "reason_code": reason_code,
        "source_fragment_id": source_fragment_id,
        "business_event_id": business_event_id,
        "reason": reason,
        "next_action": next_action,
        "paired_offset_id": paired_offset_id,
    }


def _classify_periodic_fragment(row: pd.Series) -> tuple[dict | None, list[dict]]:
    fragment_id = str(row["fragment_id"])
    event_id = str(row.get("business_event_id", ""))
    event_kind = str(row.get("event_kind", ""))
    asset_class = str(row.get("asset_class", ""))
    destination = str(row.get("cost_destination", ""))
    status = str(row.get("accounting_status", "normal"))
    amount = int(row["signed_minor"])
    records: list[dict] = []

    if bool(row.get("technical_closing", False)):
        return None, [
            _routing(
                "exclusion",
                fragment_id,
                event_id,
                "TECHNICAL_CLOSING",
                "技术性损益结转不属于本项目取数",
            )
        ]

    if event_kind in {"depreciation", "depletion"}:
        if asset_class not in _DEPRECIABLE_ASSETS:
            return None, [
                _routing(
                    "exclusion",
                    fragment_id,
                    event_id,
                    "NON_DEPRECIABLE_ASSET",
                    "该资产类别不属于第3项折旧、折耗范围",
                )
            ]
        item_no = 3
        rule_id = "NONCASH-DEPRECIATION"
        reason = "取实际进入当期损益或经营性资产成本的折旧、折耗"
        if status in _ABNORMAL_DEPRECIATION:
            code, issue_reason = _ABNORMAL_DEPRECIATION[status]
            records.append(
                _routing(
                    "issue",
                    fragment_id,
                    event_id,
                    code,
                    issue_reason,
                    next_action="accounting_error_review",
                )
            )
    elif event_kind == "amortization" and asset_class in _AMORTIZABLE_ASSETS:
        if status in {"indefinite_life", "not_ready_for_use", "held_for_sale"}:
            status_reason = {
                "indefinite_life": "使用寿命不确定的无形资产不应摊销",
                "not_ready_for_use": "尚未达到预定用途的无形资产不应开始摊销",
                "held_for_sale": "持有待售期间不应计提摊销",
            }[status]
            records.append(
                _routing(
                    "issue",
                    fragment_id,
                    event_id,
                    "INTANGIBLE_AMORTIZATION_NOT_ALLOWED",
                    f"{status_reason}；实际已影响损益或经营性资产成本的金额仍予调整",
                    next_action="accounting_error_review",
                )
            )
        item_no = 4
        rule_id = "NONCASH-INTANGIBLE-AMORTIZATION"
        reason = "取实际进入当期损益或经营性资产成本的无形资产摊销"
    elif asset_class == "long_prepaid_expense" and event_kind in {
        "amortization",
        "one_time_non_cash_writeoff",
    }:
        item_no = 5
        rule_id = "NONCASH-LONG-PREPAID"
        reason = "取长期待摊费用正常摊销或一次性非现金转销"
    elif asset_class == "long_prepaid_expense" and event_kind == "cash_refund":
        return None, [
            _routing(
                "exclusion",
                fragment_id,
                event_id,
                "LONG_PREPAID_CASH_SETTLEMENT",
                "现金退款或现金结算不属于长期待摊费用非现金摊销",
            )
        ]
    else:
        return None, records

    if destination in _NONOPERATING_DESTINATIONS:
        return None, records + [
            _routing(
                "exclusion",
                fragment_id,
                event_id,
                "NONOPERATING_CAPITALIZATION",
                _NONOPERATING_DESTINATIONS[destination],
            )
        ]
    if destination not in _OPERATING_DESTINATIONS:
        return None, records + [
            _routing(
                "issue",
                fragment_id,
                event_id,
                "UNKNOWN_COST_DESTINATION",
                "折旧或摊销的成本去向无法确定",
                next_action="ai_request",
            )
        ]
    if destination == "profit_or_loss" and not bool(
        row.get("affects_profit_or_loss", False)
    ):
        return None, records + [
            _routing(
                "exclusion",
                fragment_id,
                event_id,
                "NO_PROFIT_OR_LOSS_EFFECT",
                "标记为损益去向但未实际影响当期损益",
            )
        ]

    paired_offset_id = None
    if destination in {"inventory", "operating_contract_cost"}:
        paired_offset_id = f"PAIR-{fragment_id}-ITEM13"
        records.append(
            _routing(
                "pairing",
                fragment_id,
                event_id,
                "OPERATING_COST_DESTINATION",
                "折旧或摊销已进入经营性资产成本，与第13项建立配套抵销",
                target_item_no=13,
                amount_minor=amount,
                paired_offset_id=paired_offset_id,
            )
        )

    return (
        _claim(
            fragment_id,
            event_id,
            item_no,
            amount,
            rule_id,
            reason,
            paired_offset_id,
        ),
        records,
    )


def _classify_events(
    fragments: pd.DataFrame,
    events: pd.DataFrame,
) -> tuple[list[dict], list[dict]]:
    if events.empty:
        return [], []
    required = {
        "business_event_id",
        "event_type",
        "asset_class",
        "profit_or_loss_fragment_id",
        "profit_or_loss_minor",
        "affects_profit_or_loss",
    }
    missing = sorted(required - set(events.columns))
    if missing:
        raise ValueError(f"长期资产事件缺少字段：{missing}")
    if events["business_event_id"].astype(str).duplicated().any():
        raise ValueError("长期资产事件编号必须唯一")

    fragment_ids = set(fragments["fragment_id"].astype(str))
    claims: list[dict] = []
    records: list[dict] = []
    for _, row in events.iterrows():
        event_id = str(row["business_event_id"])
        event_type = str(row["event_type"])
        asset_class = str(row["asset_class"])
        fragment_id = str(row["profit_or_loss_fragment_id"])

        if event_type in _ITEM_7_EVENT_TYPES and asset_class == "fixed_asset":
            item_no = 7
            rule_id = "NONCASH-FIXED-ASSET-SCRAP"
            reason = "按固定资产毁损、报废或盘亏事件的最终净损益取数"
        elif event_type in _ITEM_6_EVENT_TYPES and asset_class in _ITEM_6_ASSETS:
            item_no = 6
            rule_id = "NONCASH-LONG-ASSET-DISPOSAL"
            reason = "按非金融长期资产处置或终止事件的最终净损益取数"
        else:
            records.append(
                _routing(
                    "exclusion",
                    fragment_id,
                    event_id,
                    "EVENT_OUTSIDE_ITEMS_6_7",
                    "该事件不属于第6项长期资产处置或第7项固定资产报废范围",
                )
            )
            continue

        if not bool(row["affects_profit_or_loss"]):
            records.append(
                _routing(
                    "exclusion",
                    fragment_id,
                    event_id,
                    "EVENT_NO_PROFIT_OR_LOSS_EFFECT",
                    "该长期资产事件未形成当期损益",
                )
            )
            continue
        if fragment_id not in fragment_ids:
            records.append(
                _routing(
                    "issue",
                    fragment_id,
                    event_id,
                    "EVENT_NET_RESULT_FRAGMENT_MISSING",
                    "事件最终净损益未关联到可占用的金额片段",
                    next_action="ai_request",
                )
            )
            continue

        claims.append(
            _claim(
                fragment_id,
                event_id,
                item_no,
                int(row["profit_or_loss_minor"]),
                rule_id,
                reason,
            )
        )
    return claims, records


def classify_noncash_assets(
    fragments: pd.DataFrame,
    events: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """分类第3至7项，并返回第13项配套抵销及异常记录。"""
    required = {
        "fragment_id",
        "signed_minor",
        "asset_class",
        "event_kind",
        "cost_destination",
    }
    missing = sorted(required - set(fragments.columns))
    if missing:
        raise ValueError(f"折旧摊销分类缺少字段：{missing}")

    claims: list[dict] = []
    routing: list[dict] = []
    for _, row in fragments.iterrows():
        claim, row_routing = _classify_periodic_fragment(row)
        if claim:
            claims.append(claim)
        routing.extend(row_routing)

    event_claims, event_routing = _classify_events(fragments, events)
    claims.extend(event_claims)
    routing.extend(event_routing)
    return (
        pd.DataFrame(claims, columns=_CLAIM_COLUMNS),
        pd.DataFrame(routing, columns=_ROUTING_COLUMNS),
    )
