"""准备、AI停点、定稿、核对和输出的流水线。"""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import time
import tracemalloc
from typing import Any

import pandas as pd

from .ai_contract import build_ai_requests, validate_ai_decisions
from .audit_trace import build_decision_trace, validate_decision_trace
from .contracts import RunSpec, RunStatus, load_rule_book
from .core import (
    link_business_events,
    mark_technical_closing_lines,
    register_initial_fragments,
)
from .families.deferred_tax import calculate_deferred_tax
from .families.financial_results import classify_financial_results
from .families.impairment import classify_impairment
from .families.noncash_assets import classify_noncash_assets
from .families.other_adjustments import calculate_other_adjustments
from .families.working_capital import calculate_working_capital
from .input_adapter import (
    InputProtocolError,
    align_journal_account_codes,
    normalize_journal_frame,
    normalize_trial_balance_frame,
    read_excel_sheet,
)
from .occupancy import resolve_claims
from .output import export_results, publish_audit_evidence
from .reconciliation import (
    calculate_reconciliation_inputs,
    calculate_summary,
    reconcile_all,
)
from .storage import RunStore, event_hash, sha256_file, validate_event_chain


_STATE_FILE = "run_state.json"


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8-sig",
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def _append_audit_events(run_store: RunStore, drafts: list[dict]) -> None:
    path = run_store.root / "07_audit" / "events.jsonl"
    existing = _read_jsonl(path)
    previous_hash = existing[-1]["event_hash"] if existing else "0" * 64
    events = []
    for offset, draft in enumerate(drafts, start=1):
        event = {
            "sequence_no": len(existing) + offset,
            "previous_event_hash": previous_hash,
            "run_id": run_store.run_id,
            "stage": draft["stage"],
            "event_type": draft["event_type"],
            "payload": draft.get("payload", {}),
        }
        event["event_hash"] = event_hash(event)
        previous_hash = event["event_hash"]
        events.append(event)
    run_store.append_events(events)


def _validated_source_confirmation(config: dict) -> dict:
    if config.get("journal_preprocessing_confirmed") is not True:
        raise InputProtocolError(
            "请选择经过序时账分析器处理的标准序时账，并确认后再运行"
        )
    if config.get("net_profit_user_confirmed") is not True:
        raise InputProtocolError(
            "必须先向客户取得或确认本期净利润，序时账推算值只能用于核对"
        )
    net_profit_source = str(config.get("net_profit_source", "")).strip()
    if net_profit_source not in {
        "user_provided",
        "user_confirmed_statement",
    }:
        if net_profit_source == "journal_calculated":
            raise InputProtocolError(
                "序时账推算净利润只能用于核对，不能作为正式净利润来源"
            )
        raise InputProtocolError(
            "净利润来源必须是客户直接提供或客户确认的报表金额"
        )
    net_profit_source_note = str(
        config.get("net_profit_source_note", "")
    ).strip()
    if not net_profit_source_note:
        raise InputProtocolError("净利润来源说明不能为空")
    return {
        "journal_preprocessing_confirmed": True,
        "net_profit_user_confirmed": True,
        "net_profit_source": net_profit_source,
        "net_profit_source_note": net_profit_source_note,
    }


def _spec(config: dict) -> RunSpec:
    source_confirmation = _validated_source_confirmation(config)
    entity_name = str(config.get("entity_name", "")).strip()
    if not entity_name:
        raise InputProtocolError("主体名称不能为空")
    functional_currency = str(config.get("functional_currency", "")).strip()
    if not functional_currency:
        raise InputProtocolError("本位币不能为空")
    currency_scale = config.get("currency_scale")
    if (
        isinstance(currency_scale, bool)
        or not isinstance(currency_scale, int)
        or not 0 <= currency_scale <= 6
    ):
        raise InputProtocolError("本位币小数位数必须是0至6的整数")
    display_unit = str(config.get("display_unit", "")).strip()
    if display_unit not in {"元", "千元", "万元"}:
        raise InputProtocolError("展示单位必须是元、千元或万元")
    net_profit_minor = config.get("net_profit_minor")
    if isinstance(net_profit_minor, bool) or not isinstance(net_profit_minor, int):
        raise InputProtocolError("净利润必须是最小货币单位整数")
    materiality = config.get("performance_materiality_minor")
    if (
        isinstance(materiality, bool)
        or not isinstance(materiality, int)
        or materiality <= 0
    ):
        raise InputProtocolError("实际执行重要性水平必须是正整数")
    financial_entity_flag = config.get("financial_entity_flag", False)
    if not isinstance(financial_entity_flag, bool):
        raise InputProtocolError("金融企业标志必须是JSON布尔值")
    direct_cfo_minor = config.get("direct_cfo_minor")
    if direct_cfo_minor is not None and (
        isinstance(direct_cfo_minor, bool)
        or not isinstance(direct_cfo_minor, int)
    ):
        raise InputProtocolError("直接法经营活动现金流量净额必须是整数")
    return RunSpec(
        entity_name=entity_name,
        period_start=str(config["period_start"]),
        period_end=str(config["period_end"]),
        functional_currency=functional_currency,
        currency_scale=currency_scale,
        display_unit=display_unit,
        net_profit_minor=net_profit_minor,
        performance_materiality_minor=materiality,
        financial_entity_flag=financial_entity_flag,
        direct_cfo_minor=direct_cfo_minor,
        **source_confirmation,
    )


def _display_divisor_minor(spec: RunSpec) -> int:
    unit_factor = {"元": 1, "千元": 1000, "万元": 10000}
    if spec.display_unit not in unit_factor:
        raise ValueError(f"不支持的展示单位：{spec.display_unit}")
    return (10**spec.currency_scale) * unit_factor[spec.display_unit]


def _snapshot_inputs(
    store: RunStore,
    paths: dict[str, Path],
) -> dict[str, str]:
    snapshot_dir = store.root / "01_input_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[Path, Path] = {}
    result: dict[str, str] = {}
    for name, source in paths.items():
        resolved = source.resolve()
        if resolved not in copied:
            target = snapshot_dir / f"{name}__{source.name}"
            shutil.copy2(resolved, target)
            copied[resolved] = target
        result[name] = str(copied[resolved])
    return result


def _journal_with_nature(
    journal: pd.DataFrame,
    trial_balance: pd.DataFrame,
) -> pd.DataFrame:
    nature_by_code = trial_balance.set_index("account_code")[
        "account_nature"
    ].to_dict()
    result = journal.copy()
    result["account_nature"] = result["account_code"].map(
        nature_by_code
    ).fillna("")
    return result


def _joined_text(row: pd.Series) -> str:
    return " ".join(
        str(row.get(field, "") or "")
        for field in (
            "summary",
            "account_name",
            "account_path",
            "counterparty_level1",
        )
    )


def _asset_class(text: str) -> str:
    rules = (
        ("contract_asset", ("合同资产",)),
        ("held_for_sale_asset", ("持有待售资产", "持有待售非流动资产")),
        ("long_term_equity_investment", ("长期股权投资",)),
        ("investment_property_cost", ("投资性房地产",)),
        ("construction_in_progress", ("在建工程",)),
        ("construction_materials", ("工程物资",)),
        ("goodwill", ("商誉",)),
        ("receivable", ("应收", "坏账", "贷款", "债权")),
        ("right_of_use_asset", ("使用权资产",)),
        ("intangible_asset", ("无形资产", "累计摊销")),
        ("long_prepaid_expense", ("长期待摊",)),
        ("productive_biological_asset", ("生产性生物资产",)),
        ("oil_gas_asset", ("油气资产", "油气")),
        ("fixed_asset", ("固定资产", "累计折旧", "折旧费")),
        ("inventory", ("存货", "库存", "原材料", "跌价准备")),
    )
    for asset_class, terms in rules:
        if any(term in text for term in terms):
            return asset_class
    return ""


def _compile_issues(
    decisions: pd.DataFrame,
    family_issues: pd.DataFrame,
    occupancy_issues: pd.DataFrame,
    committed: pd.DataFrame,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if decisions is not None and not decisions.empty:
        human = decisions.loc[
            decisions.get(
                "human_review_required",
                pd.Series(False, index=decisions.index),
            ).fillna(False).astype(bool)
        ].copy()
        if not human.empty:
            human["item_no"] = human["recommended_item"].astype(int)
            human["issue_code"] = "HUMAN_REVIEW_REQUIRED"
            human["amount_minor"] = human["recommended_amount_minor"].astype(int)
            human["final_decision"] = "review_required"
            frames.append(human)
    for frame in (family_issues, occupancy_issues):
        if frame is not None and not frame.empty:
            frames.append(frame.copy())
    if not frames:
        return pd.DataFrame()
    issues = pd.concat(frames, ignore_index=True, sort=False)
    if "item_no" not in issues:
        issues["item_no"] = pd.NA
    if "target_item_no" in issues:
        missing_item = pd.to_numeric(issues["item_no"], errors="coerce").isna()
        issues.loc[missing_item, "item_no"] = issues.loc[
            missing_item, "target_item_no"
        ]
    if not committed.empty:
        primary = committed.loc[
            committed["physical_consumed_minor"].astype(int).gt(0)
        ].drop_duplicates("fragment_id")
        item_by_fragment = primary.set_index(
            primary["fragment_id"].astype(str)
        )["item_no"].to_dict()
        source_ids = issues.get(
            "source_fragment_id",
            issues.get("fragment_id", pd.Series("", index=issues.index)),
        ).fillna("").astype(str)
        missing_item = pd.to_numeric(issues["item_no"], errors="coerce").isna()
        issues.loc[missing_item, "item_no"] = source_ids.loc[missing_item].map(
            item_by_fragment
        )
    return issues


def _synthetic_fragment(
    columns: list[str],
    fragment_id: str,
    signed_minor: int,
    summary: str,
    *,
    business_event_id: str | None = None,
) -> dict:
    row = {column: "" for column in columns}
    positive = signed_minor >= 0
    row.update(
        {
            "source_file": "规则计算",
            "source_sheet": "中间数据",
            "source_row": 0,
            "split_index": 0,
            "line_no": 0,
            "voucher_id": business_event_id or fragment_id,
            "line_id": fragment_id,
            "fragment_id": fragment_id,
            "business_event_id": business_event_id or fragment_id,
            "summary": summary,
            "account_code": "SYNTHETIC",
            "account_name": summary,
            "account_path": summary,
            "account_nature": "计算",
            "signed_minor": int(signed_minor),
            "amount_minor": abs(int(signed_minor)),
            "debit_minor": max(int(signed_minor), 0),
            "credit_minor": max(-int(signed_minor), 0),
            "source_amount_minor": abs(int(signed_minor)),
            "direction_sign": 1 if positive else -1,
            "source_side": "debit" if positive else "credit",
            "effective_side": "debit" if positive else "credit",
            "fragment_index": 0,
            "processable": signed_minor != 0,
            "technical_closing": False,
            "requires_ai": False,
        }
    )
    return row


def _enrich_working_capital(
    frame: pd.DataFrame,
    *,
    asset_class: str = "",
) -> pd.DataFrame:
    result = frame.copy()
    names = result["account_name"].fillna("").astype(str)
    result["business_nature"] = "operating"
    result.loc[
        names.str.contains("政府补助|递延收益", regex=True),
        "business_nature",
    ] = "government_grant"
    result.loc[
        names.str.contains("投资款|股权款", regex=True),
        "business_nature",
    ] = "investment"
    result.loc[
        names.str.contains("设备款|工程款", regex=True),
        "business_nature",
    ] = "long_asset"
    result.loc[
        names.str.contains("融资", regex=True),
        "business_nature",
    ] = "financing"
    result["asset_class"] = asset_class
    result["nonoperating_net_increase_minor"] = 0
    result["translation_change_minor"] = 0
    return result


def _account_group_mask(
    frame: pd.DataFrame,
    account_names: tuple[str, ...],
) -> pd.Series:
    names = set(account_names)
    paths = frame["account_path"].fillna("").astype(str)
    return paths.map(
        lambda path: any(
            segment.split("_", 1)[0] in names
            for segment in path.split("/")
        )
    )


def _family_results(
    fragments: pd.DataFrame,
    trial_balance: pd.DataFrame,
    auxiliary: pd.DataFrame,
    spec: RunSpec,
    rules: object,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    text = fragments.apply(_joined_text, axis=1)
    account = fragments["account_name"].fillna("").astype(str)
    nature = fragments["account_nature"].fillna("").astype(str)
    affects_profit = nature.eq("损益") | fragments[
        "account_code"
    ].astype(str).str.startswith("6")
    handled_ids: set[str] = set()
    forced_ai_ids: set[str] = set()
    synthetic_rows: list[dict] = []
    claim_frames: list[pd.DataFrame] = []
    bridge_frames: list[pd.DataFrame] = []

    direct_impairment_mask = affects_profit & text.str.contains(
        "坏账损失|直接(?:核销|减记|确认).*(?:应收|坏账|减值)",
        regex=True,
    )
    impairment_mask = account.str.contains(
        "资产减值损失|信用减值损失", regex=True
    ) | (
        account.str.contains("准备")
        & fragments["summary"].fillna("").astype(str).str.contains("核销")
    ) | direct_impairment_mask
    impairment_input = fragments.loc[impairment_mask].copy()
    if not impairment_input.empty:
        impairment_text = impairment_input.apply(_joined_text, axis=1)
        impairment_input["asset_class"] = impairment_text.map(_asset_class)
        impairment_input["event_kind"] = "provision"
        impairment_input.loc[
            direct_impairment_mask.loc[impairment_input.index],
            "event_kind",
        ] = "direct_loss"
        impairment_input.loc[
            impairment_input["account_name"].str.contains("准备")
            & impairment_input["summary"].str.contains("核销"),
            "event_kind",
        ] = "writeoff"
        impairment_input.loc[
            impairment_input["summary"].str.contains(
                "收回已核销|已核销款项收回|收回原已核销", regex=True
            ),
            "event_kind",
        ] = "recovery_of_writeoff"
        impairment_input.loc[
            impairment_input["summary"].str.contains("转回|冲回|红字", regex=True),
            "event_kind",
        ] = "reversal"
        impairment_input["impairment_model"] = "asset"
        impairment_input.loc[
            impairment_text.str.contains("信用减值|坏账|应收|贷款|债权", regex=True),
            "impairment_model",
        ] = "credit"
        impairment_input["affects_profit_or_loss"] = affects_profit.loc[
            impairment_input.index
        ]
        impairment_input["profit_or_loss_effect_minor"] = impairment_input[
            "signed_minor"
        ]
        impairment_claims, impairment_records = classify_impairment(
            impairment_input,
            pd.DataFrame(),
        )
        claim_frames.append(impairment_claims)
        bridge_frames.append(
            impairment_records.assign(family="impairment")
        )
        handled_ids.update(impairment_input["fragment_id"].astype(str))
    else:
        impairment_records = pd.DataFrame()

    periodic_mask = account.str.contains(
        "折旧费|折耗|无形资产摊销|长期待摊费用摊销",
        regex=True,
    )
    long_prepaid_writeoff_mask = (
        affects_profit
        & text.str.contains("长期待摊", regex=False)
        & text.str.contains("一次性|非现金|转销|核销", regex=True)
        & ~text.str.contains("现金退款|现金收回", regex=True)
    )
    disposal_mask = affects_profit & text.str.contains(
        "出售固定资产|处置固定资产|资产处置损益|毁损报废|报废净损失|"
        "固定资产盘亏|处置无形资产|出售无形资产|处置投资性房地产|"
        "出售投资性房地产|投资性房地产处置|使用权资产终止|在建工程处置",
        regex=True,
    )
    investment_disposal_mask = affects_profit & text.str.contains(
        "长期股权投资|金融资产|子公司",
        regex=True,
    ) & text.str.contains("处置|出售|转让|丧失控制", regex=True)
    disposal_mask &= ~investment_disposal_mask
    noncash_input = fragments.loc[
        periodic_mask | long_prepaid_writeoff_mask | disposal_mask
    ].copy()
    noncash_events: list[dict] = []
    if not noncash_input.empty:
        noncash_text = noncash_input.apply(_joined_text, axis=1)
        noncash_input["asset_class"] = noncash_text.map(_asset_class)
        noncash_input["event_kind"] = ""
        noncash_input.loc[
            noncash_input["account_name"].str.contains("折旧费"),
            "event_kind",
        ] = "depreciation"
        noncash_input.loc[
            noncash_input["account_name"].str.contains("折耗"),
            "event_kind",
        ] = "depletion"
        noncash_input.loc[
            noncash_input["account_name"].str.contains("摊销"),
            "event_kind",
        ] = "amortization"
        noncash_input.loc[
            noncash_input.index.isin(
                long_prepaid_writeoff_mask.index[long_prepaid_writeoff_mask]
            ),
            "event_kind",
        ] = "one_time_non_cash_writeoff"
        noncash_input.loc[
            noncash_input["account_name"].str.contains("长期待摊"),
            "asset_class",
        ] = "long_prepaid_expense"
        noncash_input["cost_destination"] = "profit_or_loss"
        noncash_input.loc[
            noncash_input["account_name"].str.contains("制造费用|生产成本", regex=True),
            "cost_destination",
        ] = "inventory"
        noncash_input["affects_profit_or_loss"] = affects_profit.loc[
            noncash_input.index
        ]
        status_series = pd.Series("normal", index=noncash_input.index)
        status_series[
            noncash_text.str.contains("公允价值模式", regex=False)
        ] = "fair_value_model"
        status_series[
            noncash_text.str.contains("已提足折旧|提足折旧", regex=True)
        ] = "fully_depreciated"
        status_series[
            noncash_text.str.contains("单独计价.*土地", regex=True)
        ] = "land"
        status_series[
            noncash_text.str.contains("使用寿命不确定|使用年限不确定", regex=True)
        ] = "indefinite_life"
        status_series[
            noncash_text.str.contains(
                "尚未达到预定用途|尚未可供使用|未达到可使用状态",
                regex=True,
            )
        ] = "not_ready_for_use"
        status_series[
            noncash_text.str.contains("持有待售", regex=False)
        ] = "held_for_sale"
        noncash_input["accounting_status"] = status_series
        for _, row in noncash_input.loc[disposal_mask].iterrows():
            row_text = _joined_text(row)
            asset_class = str(row.get("asset_class", ""))
            if not asset_class:
                forced_ai_ids.add(str(row["fragment_id"]))
                continue
            is_fixed_scrap = asset_class == "fixed_asset" and any(
                term in row_text for term in ("报废", "毁损", "盘亏")
            )
            event_type = (
                "fixed_asset_scrap" if is_fixed_scrap else "long_asset_sale"
            )
            row_index = noncash_input["fragment_id"].eq(row["fragment_id"])
            noncash_input.loc[
                row_index,
                "event_kind",
            ] = (
                "scrap_profit_or_loss"
                if is_fixed_scrap
                else "disposal_profit_or_loss"
            )
            event_id = f"{row['business_event_id']}-{event_type}"
            noncash_input.loc[row_index, "business_event_id"] = event_id
            noncash_events.append(
                {
                    "business_event_id": event_id,
                    "event_type": event_type,
                    "asset_class": asset_class,
                    "profit_or_loss_fragment_id": row["fragment_id"],
                    "profit_or_loss_minor": int(row["signed_minor"]),
                    "affects_profit_or_loss": True,
                }
            )
        noncash_claims, noncash_routing = classify_noncash_assets(
            noncash_input,
            pd.DataFrame(noncash_events),
        )
        paired = []
        for _, row in noncash_routing.loc[
            noncash_routing["record_type"].eq("pairing")
        ].iterrows():
            paired.append(
                {
                    "fragment_id": row["source_fragment_id"],
                    "business_event_id": row["business_event_id"],
                    "item_no": int(row["target_item_no"]),
                    "claim_amount_minor": -int(row["amount_minor"]),
                    "decision": "paired_offset",
                    "rule_id": "WC-NONCASH-COST-OFFSET",
                    "reason": "与进入存货成本的折旧摊销建立等额反向抵销",
                    "confidence": 1.0,
                    "paired_offset_id": row["paired_offset_id"],
                }
            )
        claim_frames.extend([noncash_claims, pd.DataFrame(paired)])
        bridge_frames.append(
            noncash_routing.assign(family="noncash_assets")
        )
        handled_ids.update(
            set(noncash_input["fragment_id"].astype(str)) - forced_ai_ids
        )

    financial_mask = affects_profit & (
        account.str.contains("公允价值变动损益|财务费用|投资收益", regex=True)
        | text.str.contains("现金及现金等价物汇兑", regex=True)
        | investment_disposal_mask
    )
    financial_input = fragments.loc[financial_mask].copy()
    if not financial_input.empty:
        financial_text = financial_input.apply(_joined_text, axis=1)

        def financial_kind(value: str) -> str:
            if "公允价值" in value:
                return "fair_value_remeasurement"
            if "现金及现金等价物" in value and "汇兑" in value:
                return "cash_equivalent_fx"
            if "投资收益" in value:
                return "other_investment_result"
            if "长期股权投资" in value and any(
                term in value for term in ("处置", "出售", "转让")
            ):
                return "long_term_equity_disposal"
            if "子公司" in value and any(
                term in value for term in ("处置", "出售", "丧失控制")
            ):
                return "subsidiary_disposal"
            if any(
                term in value
                for term in ("理财", "债券", "债权投资", "贷款")
            ):
                return "investment_interest"
            if any(
                term in value
                for term in ("存款利息", "活期", "结算账户", "协定存款")
            ):
                return "operating_deposit_interest"
            if "手续费" in value or "结算费" in value:
                return "bank_settlement_fee"
            if "经营性" in value and "汇兑" in value:
                return "operating_receivable_fx"
            if "汇兑" in value:
                return "financing_fx"
            return "financing_interest"

        financial_input["event_kind"] = financial_text.map(financial_kind)
        financial_input["affects_profit_or_loss"] = True
        financial_input["core_financial_business"] = (
            spec.financial_entity_flag
            & financial_text.str.contains(
                "核心|主营|利息收入|利息支出|手续费及佣金",
                regex=True,
            )
        )
        financial_claims = classify_financial_results(
            financial_input,
            pd.DataFrame(),
            financial_entity_flag=spec.financial_entity_flag,
        )
        claim_frames.append(
            financial_claims.loc[financial_claims["item_no"].notna()]
        )
        blocked_financial_ids = set(
            financial_claims.loc[
                financial_claims["decision"].eq("blocked"),
                "fragment_id",
            ].astype(str)
        )
        handled_ids.update(
            set(financial_input["fragment_id"].astype(str))
            - blocked_financial_ids
        )
        forced_ai_ids.update(blocked_financial_ids)

    deferred_mask = account.str.contains("递延所得税资产|递延所得税负债", regex=True)
    deferred_input = fragments.loc[deferred_mask].copy()
    if not deferred_input.empty:
        deferred_input["account_type"] = "deferred_tax_asset"
        deferred_input.loc[
            deferred_input["account_name"].str.contains("负债"),
            "account_type",
        ] = "deferred_tax_liability"
        deferred_input["balance_change_minor"] = deferred_input["signed_minor"]
        liability = deferred_input["account_type"].eq("deferred_tax_liability")
        deferred_input.loc[liability, "balance_change_minor"] *= -1
        deferred_text = deferred_input.apply(_joined_text, axis=1)
        deferred_input["route"] = "unknown"
        deferred_input.loc[
            deferred_text.str.contains("所得税费用"),
            "route",
        ] = "profit_or_loss"
        deferred_input.loc[
            deferred_text.str.contains("其他综合收益"),
            "route",
        ] = "other_comprehensive_income"
        deferred_input.loc[
            deferred_text.str.contains("企业合并"),
            "route",
        ] = "business_combination"
        deferred_input.loc[
            deferred_text.str.contains("直接计入权益"),
            "route",
        ] = "equity"
        deferred_claims, deferred_bridge = calculate_deferred_tax(
            deferred_input,
            pd.DataFrame(),
        )
        claim_frames.append(deferred_claims)
        bridge_frames.append(
            deferred_bridge.assign(family="deferred_tax")
        )
        handled_ids.update(deferred_input["fragment_id"].astype(str))
        deferred_voucher_ids = set(deferred_input["voucher_id"].astype(str))
        deferred_profit_counterpart = (
            fragments["voucher_id"].astype(str).isin(deferred_voucher_ids)
            & affects_profit
            & account.str.contains("所得税费用")
            & fragments["counterparty_level1"]
            .fillna("")
            .astype(str)
            .str.contains("递延所得税资产|递延所得税负债", regex=True)
        )
        handled_ids.update(
            fragments.loc[
                deferred_profit_counterpart,
                "fragment_id",
            ].astype(str)
        )
        handled_ids.update(
            fragments.loc[
                fragments["summary"].str.contains("递延所得税"),
                "fragment_id",
            ].astype(str)
        )

    covered_deferred_codes = (
        set(deferred_input["account_code"].astype(str))
        if not deferred_input.empty
        else set()
    )
    fallback_rows: list[dict] = []
    for _, balance_row in trial_balance.iterrows():
        balance_name = str(balance_row["account_name"])
        if (
            "递延所得税资产" not in balance_name
            and "递延所得税负债" not in balance_name
        ):
            continue
        balance_code = str(balance_row["account_code"])
        if balance_code in covered_deferred_codes:
            continue
        account_type = (
            "deferred_tax_asset"
            if "递延所得税资产" in balance_name
            else "deferred_tax_liability"
        )
        if account_type == "deferred_tax_asset":
            opening_gross = int(balance_row["opening_debit_minor"]) - int(
                balance_row["opening_credit_minor"]
            )
            closing_gross = int(balance_row["closing_debit_minor"]) - int(
                balance_row["closing_credit_minor"]
            )
        else:
            opening_gross = int(balance_row["opening_credit_minor"]) - int(
                balance_row["opening_debit_minor"]
            )
            closing_gross = int(balance_row["closing_credit_minor"]) - int(
                balance_row["closing_debit_minor"]
            )
        fallback_rows.append(
            {
                "balance_row_id": balance_code,
                "tax_entity_id": spec.entity_name,
                "tax_jurisdiction": "默认税收管辖区",
                "account_type": account_type,
                "opening_gross_minor": opening_gross,
                "closing_gross_minor": closing_gross,
                "non_profit_change_minor": 0,
            }
        )
    if fallback_rows:
        fallback_claims, fallback_bridge = calculate_deferred_tax(
            pd.DataFrame(), pd.DataFrame(fallback_rows)
        )
        claim_frames.append(fallback_claims)
        bridge_frames.append(
            fallback_bridge.assign(family="deferred_tax")
        )
        for claim in fallback_claims.to_dict("records"):
            synthetic_rows.append(
                _synthetic_fragment(
                    fragments.columns.tolist(),
                    str(claim["fragment_id"]),
                    int(claim["claim_amount_minor"]),
                    "递延所得税余额表兜底",
                    business_event_id=str(claim["business_event_id"]),
                )
            )

    inventory_terms = tuple(rules.account_groups["存货"])
    receivable_terms = (
        *rules.account_groups["经营性应收"],
        "其他应收款",
    )
    payable_terms = (
        *rules.account_groups["经营性应付"],
        "其他应付款",
    )

    debt_text_mask = text.str.contains(
        "债务重组|债转股|以资抵债|非货币性资产交换|豁免债务|债务豁免|债务减免|减免债务",
        regex=True,
    )
    debt_voucher_ids = set(
        fragments.loc[debt_text_mask, "voucher_id"].astype(str)
    )
    debt_events: list[dict] = []
    debt_correction_rows: list[dict] = []
    claimed_by_other_families: set[str] = set(forced_ai_ids)
    for frame in claim_frames:
        if frame.empty or "fragment_id" not in frame:
            continue
        claimed_by_other_families.update(
            frame.loc[
                frame["decision"].isin({"included", "paired_offset"}),
                "fragment_id",
            ]
            .astype(str)
        )
    if debt_voucher_ids:
        debt_rows = fragments.loc[
            fragments["voucher_id"].astype(str).isin(debt_voucher_ids)
        ]
        handled_ids.update(debt_rows["fragment_id"].astype(str))
        inventory_mask = _account_group_mask(debt_rows, inventory_terms)
        receivable_mask = _account_group_mask(debt_rows, receivable_terms)
        payable_mask = _account_group_mask(debt_rows, payable_terms)
        for voucher_id, rows in debt_rows.groupby("voucher_id", sort=False):
            cash_signed = int(
                rows.loc[
                    rows["account_name"].astype(str).str.contains(
                        "库存现金|银行存款|其他货币资金", regex=True
                    ),
                    "signed_minor",
                ].sum()
            )
            profit_mask = rows["account_nature"].eq("损益") | rows[
                "account_code"
            ].astype(str).str.startswith("6")
            profit_rows = rows.loc[profit_mask]
            unclaimed_profit_rows = profit_rows.loc[
                ~profit_rows["fragment_id"]
                .astype(str)
                .isin(claimed_by_other_families)
            ]
            profit = int(unclaimed_profit_rows["signed_minor"].sum())
            voucher_text = rows.apply(_joined_text, axis=1).str.cat(sep=" ")
            shareholder_related = (
                "股东" in voucher_text or "权益性交易" in voucher_text
            )
            fragment_id = hashlib.sha256(
                f"debt-restructuring\x1f{voucher_id}".encode("utf-8")
            ).hexdigest()
            event_id = f"debt-{voucher_id}"
            synthetic = _synthetic_fragment(
                fragments.columns.tolist(),
                fragment_id,
                profit,
                str(rows.iloc[0]["summary"]),
                business_event_id=event_id,
            )
            synthetic_rows.append(synthetic)
            debt_events.append(
                {
                    "business_event_id": event_id,
                    "event_type": "debt_restructuring",
                    "source_fragment_id": fragment_id,
                    "profit_effect_minor": profit,
                    "shareholder_related": shareholder_related,
                    "source_evidence": True,
                }
            )
            group_masks = (
                (inventory_mask, 13),
                (receivable_mask, 14),
                (payable_mask, 15),
            )
            for pool_mask, item_no in group_masks:
                for row_index in rows.index[pool_mask.loc[rows.index]]:
                    row = rows.loc[row_index]
                    correction = int(row["signed_minor"]) + cash_signed
                    if correction:
                        debt_correction_rows.append(
                            {
                                "target_item_no": item_no,
                                "correction_minor": correction,
                                "reason_code": "DEBT_RESTRUCTURING_BALANCE_CORRECTION",
                            }
                        )

    inventory = _enrich_working_capital(
        trial_balance.loc[
            _account_group_mask(trial_balance, inventory_terms)
        ],
        asset_class="inventory",
    )
    receivable = _enrich_working_capital(
        trial_balance.loc[
            _account_group_mask(trial_balance, receivable_terms)
        ]
    )
    payable = _enrich_working_capital(
        trial_balance.loc[
            _account_group_mask(trial_balance, payable_terms)
        ]
    )
    corrections = (
        impairment_records.loc[
            impairment_records.get(
                "record_type",
                pd.Series(dtype=str),
            ).eq("correction"),
            ["target_item_no", "correction_minor", "reason_code"],
        ]
        if not impairment_records.empty
        else pd.DataFrame()
    )
    if debt_correction_rows:
        corrections = pd.concat(
            [
                corrections,
                pd.DataFrame(
                    debt_correction_rows,
                    columns=["target_item_no", "correction_minor", "reason_code"],
                ),
            ],
            ignore_index=True,
            sort=False,
        )
    auxiliary_enriched = auxiliary.copy()
    if not auxiliary_enriched.empty:
        dimension_names = auxiliary_enriched["dimension_name"].fillna("").astype(str)
        auxiliary_enriched["business_nature"] = "operating"
        auxiliary_enriched.loc[
            dimension_names.str.contains("投资款|股权款", regex=True),
            "business_nature",
        ] = "investment"
        auxiliary_enriched["asset_class"] = ""
        auxiliary_enriched["nonoperating_net_increase_minor"] = 0
        auxiliary_enriched["translation_change_minor"] = 0
    working_claims, working_bridge = calculate_working_capital(
        inventory,
        receivable,
        payable,
        corrections=corrections,
        auxiliary_dimensions=auxiliary_enriched,
    )

    share_mask = (
        text.str.contains("权益结算股份支付")
        & fragments["signed_minor"].gt(0)
    )
    share_fragments = fragments.loc[share_mask].copy()
    if not share_fragments.empty:
        share_text = share_fragments.apply(_joined_text, axis=1)
        share_fragments["event_kind"] = "equity_settled_share_payment"
        share_fragments["cost_destination"] = "profit_or_loss"
        share_fragments.loc[
            share_text.str.contains(
                "存货|库存商品|原材料|在产品|合同履约成本",
                regex=True,
            ),
            "cost_destination",
        ] = "inventory"
        share_fragments.loc[
            share_text.str.contains("在建工程|开发支出|长期资产", regex=True),
            "cost_destination",
        ] = "nonoperating_long_lived_asset"
        share_fragments["source_evidence"] = True
        handled_ids.update(share_fragments["fragment_id"].astype(str))

    grant_mask = text.str.contains("政府补助")
    handled_ids.update(fragments.loc[grant_mask, "fragment_id"].astype(str))
    grant_fragments: list[dict] = []
    grant_events: list[dict] = []
    for voucher_id, rows in fragments.loc[grant_mask].groupby(
        "voucher_id",
        sort=False,
    ):
        cash_mask = rows["account_name"].str.contains(
            "库存现金|银行存款|其他货币资金",
            regex=True,
        )
        profit_mask = rows["account_nature"].eq("损益") | rows[
            "account_code"
        ].astype(str).str.startswith("6")
        cash = int(rows.loc[cash_mask, "signed_minor"].sum())
        profit = -int(rows.loc[profit_mask, "signed_minor"].sum())
        adjustment = cash - profit
        fragment_id = hashlib.sha256(
            f"government-grant\x1f{voucher_id}".encode("utf-8")
        ).hexdigest()
        event_id = f"grant-{voucher_id}"
        synthetic = _synthetic_fragment(
            fragments.columns.tolist(),
            fragment_id,
            adjustment,
            str(rows.iloc[0]["summary"]),
            business_event_id=event_id,
        )
        synthetic.update(
            {
                "event_kind": "government_grant",
                "cost_destination": "profit_or_loss",
                "source_evidence": True,
            }
        )
        grant_fragments.append(synthetic)
        synthetic_rows.append(synthetic)
        grant_events.append(
            {
                "business_event_id": event_id,
                "event_type": "government_grant",
                "source_fragment_id": fragment_id,
                "operating_cash_minor": cash,
                "profit_effect_minor": profit,
                "source_evidence": True,
            }
        )
    other_input = pd.concat(
        [share_fragments, pd.DataFrame(grant_fragments)],
        ignore_index=True,
        sort=False,
    )
    other_bridge = pd.DataFrame()
    if not other_input.empty or grant_events or debt_events:
        other_claims, other_bridge = calculate_other_adjustments(
            other_input,
            pd.DataFrame(grant_events + debt_events),
            pd.DataFrame(),
        )
        claim_frames.append(other_claims)
        bridge_frames.append(
            other_bridge.assign(family="other_adjustments")
        )

    if not other_bridge.empty:
        late_corrections = other_bridge.loc[
            other_bridge["target_item_no"].isin([13, 14, 15])
            & other_bridge["adjustment_minor"].fillna(0).ne(0),
            ["target_item_no", "adjustment_minor", "reason_code"],
        ].rename(columns={"adjustment_minor": "correction_minor"})
        if not late_corrections.empty:
            working_claims, working_bridge = calculate_working_capital(
                inventory,
                receivable,
                payable,
                corrections=pd.concat(
                    [corrections, late_corrections],
                    ignore_index=True,
                    sort=False,
                ),
                auxiliary_dimensions=auxiliary_enriched,
            )

    claim_frames.append(working_claims)
    bridge_frames.append(working_bridge.assign(family="working_capital"))
    for _, row in working_claims.iterrows():
        synthetic_rows.append(
            _synthetic_fragment(
                fragments.columns.tolist(),
                str(row["fragment_id"]),
                int(row["claim_amount_minor"]),
                f"第{int(row['item_no'])}项余额桥接",
                business_event_id=str(row["business_event_id"]),
            )
        )

    populated_claims = [frame for frame in claim_frames if not frame.empty]
    claims = (
        pd.concat(populated_claims, ignore_index=True, sort=False)
        if populated_claims
        else pd.DataFrame(
            columns=[
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
        )
    )
    claims = claims.loc[
        claims["decision"].isin({"included", "paired_offset"})
        & claims["item_no"].notna()
    ].copy()
    synthetic = pd.DataFrame(synthetic_rows)
    all_fragments = pd.concat(
        [fragments, synthetic],
        ignore_index=True,
        sort=False,
    ).drop_duplicates("fragment_id", keep="first")

    bridge_frames = [frame for frame in bridge_frames if not frame.empty]
    bridges = (
        pd.concat(bridge_frames, ignore_index=True, sort=False)
        if bridge_frames
        else pd.DataFrame()
    )
    ai_issue_ids: set[str] = set()
    if not bridges.empty and "next_action" in bridges:
        ai_issue_ids.update(
            bridges.loc[
                bridges["next_action"].fillna("").eq("ai_request"),
                "source_fragment_id",
            ]
            .dropna()
            .astype(str)
        )

    claimed_ids = set(claims["fragment_id"].astype(str))
    base_scope = fragments.loc[
        fragments["processable"]
        & ~fragments["technical_closing"]
        & (
            fragments["account_nature"].eq("损益")
            | fragments["account_code"].astype(str).str.startswith("6")
        )
    ]
    potential_adjustment_ids = set(
        base_scope.loc[
            text.loc[base_scope.index].str.contains(
                "固定资产|无形资产|在建工程|投资性房地产|使用权资产|"
                "生产性生物资产|油气资产|持有待售资产|减值|跌价|坏账|"
                "折旧|折耗|摊销|处置|出售|报废|毁损|盘亏|盘盈|"
                "公允价值|投资收益|汇兑|融资|递延所得税|股份支付|"
                "股权激励|债务重组|债转股|以资抵债|非货币性资产交换|"
                "政府补助|递延收益",
                regex=True,
            ),
            "fragment_id",
        ].astype(str)
    )
    unknown_ids = (
        potential_adjustment_ids - handled_ids - claimed_ids
    ) | ai_issue_ids | forced_ai_ids
    scope_ids = claimed_ids | unknown_ids
    scope = all_fragments.loc[
        all_fragments["fragment_id"].astype(str).isin(scope_ids)
    ].copy()

    candidates: list[dict] = []
    evidence: list[dict] = []
    for _, row in scope.loc[
        scope["fragment_id"].astype(str).isin(unknown_ids)
    ].iterrows():
        text = f"{row.get('account_name', '')} {row.get('summary', '')}"
        candidate_items = [6, 7] if "固定资产" in text else list(range(1, 17))
        for item_no in candidate_items:
            candidates.append(
                {
                    "fragment_id": row["fragment_id"],
                    "item_no": item_no,
                    "rule_id": f"AI-CANDIDATE-{item_no}",
                }
            )
        source_refs = [
            {
                "source_file": row.get("source_file", ""),
                "source_sheet": row.get("source_sheet", ""),
                "source_row": int(row.get("source_row", 0)),
            }
        ]
        for suffix, evidence_type, content in (
            ("account", "account", str(row.get("account_path", ""))),
            ("summary", "summary", str(row.get("summary", ""))),
        ):
            evidence.append(
                {
                    "fragment_id": row["fragment_id"],
                    "evidence_id": f"{row['fragment_id']}-{suffix}",
                    "evidence_type": evidence_type,
                    "content": content or "未提供",
                    "source_refs": source_refs,
                }
            )
    issues = (
        bridges.loc[
            bridges.get(
                "next_action",
                pd.Series("", index=bridges.index),
            )
            .fillna("")
            .ne("")
        ].copy()
        if not bridges.empty
        else pd.DataFrame()
    )
    return (
        all_fragments,
        scope,
        claims,
        pd.DataFrame(
            candidates,
            columns=["fragment_id", "item_no", "rule_id"],
        ),
        pd.DataFrame(
            evidence,
            columns=[
                "fragment_id",
                "evidence_id",
                "evidence_type",
                "content",
                "source_refs",
            ],
        ),
        bridges,
        issues,
    )


def _run_store_from_state(run_dir: Path, state: dict) -> RunStore:
    return RunStore(
        Path(run_dir),
        str(state["run_id"]),
        str(state["input_fingerprint"]),
        str(state["rule_version"]),
        str(state["code_version"]),
    )


def _write_state(store: RunStore, state: dict) -> None:
    store.write_json("", _STATE_FILE, state)


def prepare_run(config_path: Path) -> dict:
    """读取Excel并冻结到AI停点。"""
    started = time.perf_counter()
    tracemalloc.start()
    config_path = Path(config_path)
    config = _read_json(config_path)
    spec = _spec(config)
    output_mode = str(config.get("output_mode", "standard"))
    if output_mode not in {"standard", "rapid"}:
        raise InputProtocolError(
            f"输出模式必须是standard或rapid：{output_mode}"
        )
    source_paths = {
        "journal": Path(config["journal_path"]),
        "trial_balance": Path(config["trial_balance_path"]),
        "rules": Path(config["rules_path"]),
    }
    labels = {
        "journal": "标准序时账",
        "trial_balance": "科目余额表",
        "rules": "规则文件",
    }
    missing_sources = [
        labels[name] for name, path in source_paths.items() if not path.is_file()
    ]
    if missing_sources:
        raise InputProtocolError("输入文件不存在：" + "、".join(missing_sources))
    unsupported_workbooks = [
        labels[name]
        for name in ("journal", "trial_balance")
        if source_paths[name].suffix.lower() not in {".xlsx", ".xlsm"}
    ]
    if unsupported_workbooks:
        raise InputProtocolError(
            "输入工作簿必须是xlsx或xlsm格式："
            + "、".join(unsupported_workbooks)
        )
    input_hashes = {
        name: sha256_file(path) for name, path in source_paths.items()
    }
    rules = load_rule_book(source_paths["rules"])
    store = RunStore.create(
        Path(config["output_root"]),
        spec,
        input_hashes,
        rules.rule_version,
        str(config["code_version"]),
    )
    snapshots = _snapshot_inputs(store, source_paths)
    store.write_json(
        "01_input_snapshot",
        "config_snapshot.json",
        config,
    )

    journal_snapshot = Path(snapshots["journal"])
    balance_snapshot = Path(snapshots["trial_balance"])
    rules_snapshot = Path(snapshots["rules"])
    if sha256_file(rules_snapshot) != input_hashes["rules"]:
        raise InputProtocolError("规则文件在创建输入快照期间发生变化")
    rules = load_rule_book(rules_snapshot)
    if journal_snapshot.resolve() == balance_snapshot.resolve():
        try:
            with pd.ExcelFile(
                journal_snapshot,
                engine="openpyxl",
            ) as excel_file:
                journal_raw = read_excel_sheet(
                    journal_snapshot,
                    str(config["journal_sheet"]),
                    int(config.get("journal_header_row", 1)),
                    excel_file=excel_file,
                )
                balance_raw = read_excel_sheet(
                    balance_snapshot,
                    str(config["trial_balance_sheet"]),
                    int(config.get("trial_balance_header_row", 1)),
                    excel_file=excel_file,
                )
        except (OSError, ValueError) as exc:
            raise InputProtocolError(
                f"Excel工作簿无法读取：{journal_snapshot}"
            ) from exc
    else:
        journal_raw = read_excel_sheet(
            journal_snapshot,
            str(config["journal_sheet"]),
            int(config.get("journal_header_row", 1)),
        )
        balance_raw = read_excel_sheet(
            balance_snapshot,
            str(config["trial_balance_sheet"]),
            int(config.get("trial_balance_header_row", 1)),
        )
    journal = normalize_journal_frame(
        journal_raw,
        dict(config.get("journal_mapping", {})),
        spec,
    )
    trial_balance, account_nodes, auxiliary = normalize_trial_balance_frame(
        balance_raw,
        dict(config.get("trial_balance_mapping", {})),
        spec,
    )
    journal = align_journal_account_codes(journal, trial_balance)
    journal = _journal_with_nature(journal, trial_balance)
    store.write_parquet("02_normalized", "journal_lines.parquet", journal)
    store.write_parquet("02_normalized", "trial_balance.parquet", trial_balance)
    store.write_parquet("02_normalized", "account_nodes.parquet", account_nodes)
    store.write_parquet(
        "02_normalized",
        "auxiliary_dimensions.parquet",
        auxiliary,
    )

    fragments = register_initial_fragments(journal)
    fragments = mark_technical_closing_lines(
        fragments,
        {"本年利润", "利润分配"},
    )
    fragments = link_business_events(fragments)
    (
        fragments,
        scope,
        rule_claims,
        candidates,
        evidence,
        bridges,
        family_issues,
    ) = _family_results(
        fragments,
        trial_balance,
        auxiliary,
        spec,
        rules,
    )
    requests = build_ai_requests(
        scope,
        candidates,
        evidence,
        spec.performance_materiality_minor,
    )
    store.write_parquet("03_core", "amount_fragments.parquet", fragments)
    store.write_parquet("04_candidates", "candidate_scope.parquet", scope)
    store.write_parquet("04_candidates", "rule_claims.parquet", rule_claims)
    store.write_parquet("04_candidates", "ai_candidates.parquet", candidates)
    store.write_parquet("04_candidates", "evidence.parquet", evidence)
    store.write_parquet("04_candidates", "family_bridges.parquet", bridges)
    store.write_parquet("04_candidates", "family_issues.parquet", family_issues)
    requests_path = store.root / "05_ai" / "ai_requests.jsonl"
    _write_jsonl(requests_path, requests)
    store._written_files["ai_requests"] = requests_path.relative_to(store.root)
    checkpoint_names = (
        "journal_lines",
        "trial_balance",
        "account_nodes",
        "auxiliary_dimensions",
        "amount_fragments",
        "candidate_scope",
        "rule_claims",
        "ai_candidates",
        "evidence",
        "family_bridges",
        "family_issues",
        "ai_requests",
    )
    checkpoint_hashes = {
        name: sha256_file(store.root / store._written_files[name])
        for name in checkpoint_names
    }
    store.complete_stage("prepared", input_hashes, checkpoint_hashes)

    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    status = (
        RunStatus.AWAITING_AI.value
        if requests
        else RunStatus.READY_TO_FINALIZE.value
    )
    state = {
        "run_id": store.run_id,
        "status": status,
        "input_fingerprint": store.input_fingerprint,
        "snapshot_sources": {
            "journal": str(journal_snapshot.resolve()),
            "trial_balance": str(balance_snapshot.resolve()),
        },
        "journal_sheet": str(config["journal_sheet"]),
        "journal_header_row": int(config.get("journal_header_row", 1)),
        "journal_row_count": int(len(journal_raw)),
        "journal_column_count": int(len(journal_raw.columns) - 1),
        "input_sources": {
            name: {"path": str(path), "sha256": input_hashes[name]}
            for name, path in source_paths.items()
        },
        "input_hashes": input_hashes,
        "rule_version": rules.rule_version,
        "rules_path": str(rules_snapshot.resolve()),
        "code_version": str(config["code_version"]),
        "protocol_version": str(config["protocol_version"]),
        "output_mode": output_mode,
        "spec": asdict(spec),
        "ai_request_count": len(requests),
        "timings_seconds": {"prepare": elapsed},
        "peak_memory_bytes": peak,
    }
    _write_state(store, state)
    _append_audit_events(
        store,
        [
            {
                "stage": "prepare",
                "event_type": "run_started",
                "payload": {"config": str(config_path)},
            },
            {
                "stage": "prepare",
                "event_type": "normalization_completed",
                "payload": {
                    "journal_rows": len(journal),
                    "trial_balance_rows": len(trial_balance),
                },
            },
            {
                "stage": "prepare",
                "event_type": "ai_requests_frozen",
                "payload": {"request_count": len(requests)},
            },
        ],
    )
    return {
        "run_dir": str(store.root),
        "status": status,
        "ai_request_count": len(requests),
        "ai_requests_path": str(requests_path),
    }


def _write_blocked_diagnosis(
    output_dir: Path,
    occupancy_issues: pd.DataFrame,
    unclassified: int,
    exclusive_conflicts: int,
) -> None:
    """阻断时仍落一份机器诊断，便于按SKILL指引说明差异和修复动作。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    issue_records = (
        occupancy_issues.to_dict("records")
        if not occupancy_issues.empty
        else []
    )
    (output_dir / "reconciliation.json").write_text(
        json.dumps(
            {
                "status": "blocked",
                "unclassified_amount_minor": unclassified,
                "exclusive_conflict_count": exclusive_conflicts,
                "checks": [],
                "issues": issue_records,
                "next_action": (
                    "处理未分类金额：查看本目录issues.jsonl中的"
                    "UNCLASSIFIED_REMAINDER及next_action，补充AI结论或"
                    "修正占用申请后重新定稿"
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8-sig",
    )
    (output_dir / "issues.jsonl").write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in issue_records
        ),
        encoding="utf-8-sig",
    )


def get_run_status(run_dir: Path) -> dict:
    """只读取状态和输入哈希，不触发任何计算。"""
    run_dir = Path(run_dir)
    state = _read_json(run_dir / _STATE_FILE)
    try:
        _validated_source_confirmation(state["spec"])
    except InputProtocolError as exc:
        return {
            **state,
            "status": "invalidated",
            "invalidated_inputs": ["input_source_confirmation"],
            "invalidation_reason": str(exc),
        }
    changed = []
    for name, source in state["input_sources"].items():
        path = Path(source["path"])
        if not path.exists() or sha256_file(path) != source["sha256"]:
            changed.append(name)
    if changed:
        return {
            **state,
            "status": "invalidated",
            "invalidated_inputs": changed,
        }
    snapshot_changed = []
    for name, snapshot_path in state.get("snapshot_sources", {}).items():
        snapshot = Path(snapshot_path)
        expected = state["input_sources"][name]["sha256"]
        if not snapshot.exists() or sha256_file(snapshot) != expected:
            snapshot_changed.append(name)
    if snapshot_changed:
        return {
            **state,
            "status": "invalidated",
            "invalidated_inputs": snapshot_changed,
            "invalidation_reason": (
                "冻结输入快照缺失或已变化：" + "、".join(snapshot_changed)
            ),
        }
    rules_snapshot = Path(state["rules_path"])
    if (
        not rules_snapshot.exists()
        or sha256_file(rules_snapshot) != state["input_hashes"]["rules"]
    ):
        return {
            **state,
            "status": "invalidated",
            "invalidated_inputs": ["rules_snapshot"],
            "invalidation_reason": "冻结规则快照缺失或已变化",
        }
    store = _run_store_from_state(run_dir, state)
    if not store.can_resume(
        "prepared",
        state["input_hashes"],
        state["rule_version"],
        state["code_version"],
    ):
        return {
            **state,
            "status": "invalidated",
            "invalidated_inputs": ["checkpoint"],
            "invalidation_reason": "准备阶段中间文件缺失、已变化或检查点不一致",
        }
    try:
        validate_event_chain(run_dir / "07_audit" / "events.jsonl")
    except ValueError as exc:
        return {
            **state,
            "status": "invalidated",
            "invalidated_inputs": ["audit_events"],
            "invalidation_reason": str(exc),
        }
    return state


def finalize_run(
    run_dir: Path,
    ai_decisions_path: Path | None,
) -> dict:
    """校验AI结论，提交占用，汇总核对并输出。"""
    run_dir = Path(run_dir)
    state = get_run_status(run_dir)
    if state["status"] == "invalidated":
        if state.get("invalidation_reason"):
            raise InputProtocolError(str(state["invalidation_reason"]))
        raise ValueError(f"输入文件已变化，旧检查点失效：{state['invalidated_inputs']}")
    store = _run_store_from_state(run_dir, state)
    rules = load_rule_book(Path(state["rules_path"]))
    requests = _read_jsonl(run_dir / "05_ai" / "ai_requests.jsonl")
    if requests and ai_decisions_path is None:
        return {
            "run_dir": str(run_dir),
            "status": RunStatus.AWAITING_AI.value,
            "ai_request_count": len(requests),
        }

    decisions = (
        validate_ai_decisions(
            requests,
            _read_jsonl(Path(ai_decisions_path)),
        )
        if requests
        else pd.DataFrame()
    )
    rule_claims = pd.read_parquet(
        run_dir / "04_candidates" / "rule_claims.parquet"
    )
    validated_decisions_path = run_dir / "05_ai" / "ai_decisions_validated.jsonl"
    if requests:
        _write_jsonl(validated_decisions_path, decisions.to_dict("records"))
    ai_claims = []
    for _, decision in decisions.iterrows():
        ai_claims.append(
            {
                "fragment_id": decision["fragment_id"],
                "business_event_id": decision["business_event_id"],
                "item_no": int(decision["recommended_item"]),
                "claim_amount_minor": int(
                    decision["recommended_amount_minor"]
                ),
                "decision": (
                    "excluded"
                    if decision["recommended_status"] == "excluded"
                    else "review_required"
                    if bool(decision["human_review_required"])
                    else "ai_classified"
                ),
                "rule_id": str(decision["applicable_rule_ids"][0]),
                "reason": str(decision["reason"]),
                "confidence": float(decision["confidence"]),
                "paired_offset_id": None,
            }
        )
    claims = pd.concat(
        [rule_claims, pd.DataFrame(ai_claims)],
        ignore_index=True,
        sort=False,
    )
    scope = pd.read_parquet(
        run_dir / "04_candidates" / "candidate_scope.parquet"
    )
    committed, occupancy_issues = resolve_claims(scope, claims, rules)
    unclassified = int(
        occupancy_issues["remaining_amount_minor"].sum()
        if not occupancy_issues.empty
        else 0
    )
    exclusive_conflicts = int(
        occupancy_issues["issue_code"].eq(
            "EXCLUSIVE_CLAIM_CONFLICT"
        ).sum()
        if not occupancy_issues.empty
        else 0
    )
    if unclassified:
        state["status"] = RunStatus.BLOCKED.value
        _write_state(store, state)
        _write_blocked_diagnosis(
            run_dir / "08_output",
            occupancy_issues,
            unclassified,
            exclusive_conflicts,
        )
        return {
            "run_dir": str(run_dir),
            "status": RunStatus.BLOCKED.value,
            "unclassified_amount_minor": unclassified,
            "exclusive_conflict_count": exclusive_conflicts,
        }

    fragments = pd.read_parquet(
        run_dir / "03_core" / "amount_fragments.parquet"
    )
    details = committed.merge(
        fragments,
        on="fragment_id",
        how="left",
        suffixes=("", "_source"),
    )
    journal = pd.read_parquet(
        run_dir / "02_normalized" / "journal_lines.parquet"
    )
    trial_balance = pd.read_parquet(
        run_dir / "02_normalized" / "trial_balance.parquet"
    )
    bridges = pd.read_parquet(
        run_dir / "04_candidates" / "family_bridges.parquet"
    )
    ai_candidates = pd.read_parquet(
        run_dir / "04_candidates" / "ai_candidates.parquet"
    )
    evidence = pd.read_parquet(
        run_dir / "04_candidates" / "evidence.parquet"
    )
    decision_trace = build_decision_trace(
        rule_claims=rule_claims,
        ai_candidates=ai_candidates,
        ai_requests=requests,
        ai_decisions=decisions,
        committed=committed,
        fragments=fragments,
        evidence=evidence,
        family_bridges=bridges,
    )
    validate_decision_trace(decision_trace)
    store.write_parquet(
        "06_decisions",
        "decision_trace.parquet",
        decision_trace,
    )
    reconciliation_inputs = calculate_reconciliation_inputs(
        journal,
        trial_balance,
        committed,
        occupancy_issues,
        bridges,
    )
    family_issues = pd.read_parquet(
        run_dir / "04_candidates" / "family_issues.parquet"
    )
    issues = _compile_issues(
        decisions,
        family_issues,
        occupancy_issues,
        committed,
    )
    summary = calculate_summary(
        int(state["spec"]["net_profit_minor"]),
        committed,
        rules,
        issues,
    )
    reconciliation = reconcile_all(
        summary,
        direct_cfo_minor=state["spec"]["direct_cfo_minor"],
        **reconciliation_inputs,
    )
    output_dir = run_dir / "08_output"
    human_review_count = int(
        decisions.get(
            "human_review_required",
            pd.Series(False, index=decisions.index),
        ).fillna(False).astype(bool).sum()
    )
    final_status = (
        RunStatus.PROVISIONAL_REVIEW.value
        if human_review_count
        else RunStatus.EXPORTED.value
        if reconciliation["status"] in {"pass", "not_run"}
        else RunStatus.BLOCKED.value
    )
    completion_status = (
        "formal_completed"
        if reconciliation["status"] == "pass" and not human_review_count
        else "diagnostic_only"
    )
    completion_status_label = {
        "pass": (
            "临时结果、待人工复核"
            if human_review_count
            else "正式核对完成"
        ),
        "not_run": "仅诊断、待总对账",
    }.get(reconciliation["status"], "仅诊断、总对账未通过")
    file_status = RunStatus.EXPORTED.value
    run_info = {
        "entity_name": state["spec"]["entity_name"],
        "period_start": state["spec"]["period_start"],
        "period_end": state["spec"]["period_end"],
        "functional_currency": state["spec"]["functional_currency"],
        "display_unit": state["spec"]["display_unit"],
        "display_divisor_minor": _display_divisor_minor(
            RunSpec(**state["spec"])
        ),
        "performance_materiality_minor": state["spec"][
            "performance_materiality_minor"
        ],
        "protocol_version": state["protocol_version"],
        "run_status": final_status,
        "file_status": file_status,
        "completion_status": completion_status,
        "completion_status_label": completion_status_label,
        "run_id": state["run_id"],
        "rule_version": state["rule_version"],
        "code_version": state["code_version"],
    }
    export_results(
        run_store=store,
        summary=summary,
        details=details,
        occupancy=committed,
        bridges=bridges,
        issues=issues,
        reconciliation=reconciliation,
        run_info=run_info,
        output_dir=output_dir,
        decision_trace=decision_trace,
        journal_snapshot=Path(state["snapshot_sources"]["journal"]),
        journal_sheet=state["journal_sheet"],
        journal_header_row=int(state["journal_header_row"]),
        output_mode=str(state.get("output_mode", "standard")),
    )
    state["status"] = final_status
    state["file_status"] = file_status
    state["completion_status"] = completion_status
    state["unclassified_amount_minor"] = 0
    state["exclusive_conflict_count"] = exclusive_conflicts
    state["indirect_cfo_minor"] = reconciliation["indirect_cfo_minor"]
    _append_audit_events(
        store,
        [
            {
                "stage": "finalize",
                "event_type": "ai_decisions_validated",
                "payload": {
                    "decision_count": len(decisions),
                    "human_review_count": human_review_count,
                },
            },
            {
                "stage": "finalize",
                "event_type": "occupancy_committed",
                "payload": {"claim_count": len(committed)},
            },
            {
                "stage": "finalize",
                "event_type": "reconciliation_completed",
                "payload": {"status": reconciliation["status"]},
            },
            {
                "stage": "output",
                "event_type": "run_completed",
                "payload": {
                    "status": final_status,
                    "file_status": file_status,
                    "completion_status": completion_status,
                },
            },
        ],
    )
    publish_audit_evidence(
        output_dir,
        run_dir / "07_audit" / "events.jsonl",
    )
    _write_state(store, state)
    return {
        "run_dir": str(run_dir),
        "status": final_status,
        "file_status": file_status,
        "completion_status": completion_status,
        "completion_status_label": completion_status_label,
        "unclassified_amount_minor": 0,
        "exclusive_conflict_count": exclusive_conflicts,
        "indirect_cfo_minor": reconciliation["indirect_cfo_minor"],
        "workbook_path": str(output_dir / "现金流量表附表计算结果.xlsx"),
        "manifest_path": str(output_dir / "output_manifest.json"),
    }
