"""金额片段、结转标记和业务事件。"""

import hashlib
import re

import pandas as pd


def _stable_id(*parts: object) -> str:
    text = "\x1f".join("" if pd.isna(part) else str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def register_initial_fragments(journal: pd.DataFrame) -> pd.DataFrame:
    required = {"line_id", "signed_minor", "source_side", "source_amount_minor"}
    missing = sorted(required - set(journal.columns))
    if missing:
        raise ValueError(f"标准序时账缺少金额片段字段：{missing}")
    fragments = journal.copy()
    fragments["fragment_index"] = 0
    fragments["amount_minor"] = fragments["signed_minor"].abs().astype("int64")
    fragments["direction_sign"] = (
        fragments["signed_minor"].ge(0).map({True: 1, False: -1}).astype("int8")
    )
    fragments["processable"] = fragments["amount_minor"].gt(0)
    fragments["effective_side"] = "none"
    fragments.loc[
        fragments["processable"] & fragments["direction_sign"].eq(1),
        "effective_side",
    ] = "debit"
    fragments.loc[
        fragments["processable"] & fragments["direction_sign"].eq(-1),
        "effective_side",
    ] = "credit"
    fragments["fragment_id"] = [
        _stable_id(line_id, source_side, source_amount, 0)
        for line_id, source_side, source_amount in zip(
            fragments["line_id"],
            fragments["source_side"],
            fragments["source_amount_minor"],
            strict=True,
        )
    ]
    fragments["technical_closing"] = False
    fragments["business_event_id"] = ""
    fragments["requires_ai"] = False
    return fragments.reset_index(drop=True)


def mark_technical_closing_lines(
    fragments: pd.DataFrame,
    closing_accounts: set[str],
) -> pd.DataFrame:
    result = fragments.copy()
    codes = result["account_code"].fillna("").astype(str)
    nature = (
        result["account_nature"].fillna("").astype(str)
        if "account_nature" in result
        else pd.Series("", index=result.index)
    )
    is_profit_loss = nature.eq("损益") | codes.str.startswith("6")
    has_closing_counterparty = result["counterparty_level1"].fillna("").map(
        lambda value: any(
            account in {
                part
                for part in re.split(r"[,，;；、/|\\\s]+", str(value))
                if part
            }
            for account in closing_accounts
        )
    )
    signed = result["signed_minor"].astype("int64")
    income = codes.str.startswith(("600", "601", "605", "606", "610", "611", "630"))
    expense = codes.str.startswith(("640", "660", "670", "671", "680"))
    direction_matches = (income & signed.gt(0)) | (expense & signed.lt(0))
    explicit_closing_rule = (
        result["summary"].fillna("").astype(str).str.contains("结转损益")
        & result["match_type"]
        .fillna("")
        .astype(str)
        .str.contains("结转损益规则")
    )
    contrary = (
        result["summary"]
        .fillna("")
        .astype(str)
        .str.contains(r"利润分配|分配股利|应付股利|提取盈余", regex=True)
    )
    result["technical_closing"] = (
        is_profit_loss
        & has_closing_counterparty
        & (direction_matches | explicit_closing_rule)
        & ~contrary
    )
    # 同一凭证内同一一级科目的明细行金额对称互抵（如利息收支互抵、不过本年利润），
    # 不产生新损益，按技术性行过滤，避免串入调节项目；
    # 分组字段缺失时（如单测夹具）跳过该规则。
    summary_text = result["summary"].fillna("").astype(str)
    if {"voucher_id", "level1_account_code"}.issubset(result.columns):
        group_by = [result["voucher_id"], result["level1_account_code"]]
        signed = result["signed_minor"]
        # 仅当凭证内没有本年利润/利润分配行承接结转时，
        # 同科目互抵才认定为内部对转；正常结转凭证不受影响。
        has_pl_line = (
            result["level1_account_code"].isin(["4103", "4104"])
            | result["counterparty_level1"]
            .fillna("")
            .astype(str)
            .str.contains("本年利润|利润分配", regex=True)
        )
        voucher_without_pl = ~has_pl_line.groupby(
            result["voucher_id"]
        ).transform("any")
        group_offset = (
            summary_text.str.contains("结转", regex=False)
            & voucher_without_pl
            & signed.groupby(group_by).transform("sum").eq(0)
            & signed.groupby(group_by).transform("size").ge(2)
            & signed.gt(0).groupby(group_by).transform("any")
            & signed.lt(0).groupby(group_by).transform("any")
            & is_profit_loss
        )
        # 仅排除组内费用转出方向（signed<0）的结转行：它们会冲掉第9项
        # 对利息支出的加回；收益转出行（signed>0）保留，与计提行对冲，
        # 维持利息收入不调整的口径。
        internal_offset = group_offset & signed.lt(0)
    else:
        internal_offset = pd.Series(False, index=result.index)
    newly_offset = internal_offset & ~result["technical_closing"]
    result["closing_evidence"] = result.get("closing_evidence", "")
    result["closing_evidence"] = result["closing_evidence"].fillna("")
    result.loc[newly_offset, "closing_evidence"] = (
        "同凭证同科目明细互抵为零＋摘要含结转＋无本年利润对面行"
    )
    result["technical_closing"] = (
        result["technical_closing"] | internal_offset
    )
    default_evidence = "损益类科目＋结转对方＋方向或显式结转规则成立＋无反证"
    mask_default = (
        result["technical_closing"].fillna(False)
        & result["closing_evidence"].eq("")
    )
    result.loc[mask_default, "closing_evidence"] = default_evidence
    return result


def link_business_events(fragments: pd.DataFrame) -> pd.DataFrame:
    result = fragments.copy()
    explicit_fields = (
        "asset_id",
        "grant_id",
        "contract_id",
        "document_no",
        "auxiliary_id",
    )
    event_ids: list[str] = []
    requires_ai: list[bool] = []
    for _, row in result.iterrows():
        event_type = str(row.get("event_type", "") or "unclassified")
        explicit = next(
            (
                (field, str(row[field]).strip())
                for field in explicit_fields
                if field in result.columns
                and not pd.isna(row.get(field))
                and str(row.get(field)).strip()
            ),
            None,
        )
        if explicit:
            event_ids.append(_stable_id("event", event_type, *explicit))
            requires_ai.append(False)
            continue
        normalized_summary = re.sub(r"\W+", "", str(row.get("summary", "")))
        event_ids.append(
            _stable_id(
                "candidate_event",
                event_type,
                row.get("voucher_date", ""),
                normalized_summary,
                row.get("account_name", ""),
                row.get("counterparty_level1", ""),
                row.get("fragment_id", ""),
            )
        )
        requires_ai.append(True)
    result["business_event_id"] = event_ids
    result["requires_ai"] = requires_ai
    return result


def split_fragment(
    fragment: pd.Series,
    allocations: list[dict],
) -> pd.DataFrame:
    original_amount = int(fragment["amount_minor"])
    amounts = [allocation.get("amount_minor") for allocation in allocations]
    if (
        not amounts
        or any(
            not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount < 0
            for amount in amounts
        )
        or sum(amounts) != original_amount
    ):
        raise ValueError(
            f"分配金额必须是非负整数且合计等于原片段：{original_amount}"
        )
    parent_id = str(fragment["fragment_id"])
    rows: list[dict] = []
    for index, allocation in enumerate(allocations):
        row = fragment.to_dict()
        row.update(allocation)
        row["parent_fragment_id"] = parent_id
        row["fragment_index"] = index
        row["fragment_id"] = _stable_id(
            parent_id,
            index,
            allocation["amount_minor"],
            allocation.get("purpose", ""),
        )
        row["processable"] = allocation["amount_minor"] > 0
        rows.append(row)
    return pd.DataFrame(rows)
