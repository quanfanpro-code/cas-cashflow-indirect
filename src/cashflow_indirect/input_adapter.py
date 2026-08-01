"""Excel输入适配和标准化。"""

import hashlib
import json
from pathlib import Path
from zipfile import BadZipFile

import pandas as pd

from .contracts import RunSpec
from .money import MoneyParseError, MoneyPrecisionError, parse_minor


class InputProtocolError(ValueError):
    pass


_JOURNAL_COLUMNS = {
    "记账时间": "voucher_date",
    "会计月": "fiscal_period",
    "凭证种类": "voucher_type",
    "凭证编号": "voucher_no",
    "编号": "original_line_id",
    "业务说明": "summary",
    "一级编号": "level1_account_code",
    "一级科目": "level1_account_name",
    "科目编号": "account_code",
    "科目名称": "account_name",
    "借方发生额": "debit",
    "贷方发生额": "credit",
    "对方科目": "counterparty_level1",
    "匹配类型": "match_type",
}

_BALANCE_COLUMNS = {
    "行类型": "row_type",
    "科目编码": "account_code",
    "科目名称": "account_name",
    "科目级次": "account_level",
    "上级科目编码": "parent_account_code",
    "科目性质": "account_nature",
    "是否末级": "is_leaf",
    "所属科目编码": "owner_account_code",
    "辅助类型": "dimension_type",
    "辅助编码": "dimension_code",
    "辅助名称": "dimension_name",
    "期初借方余额": "opening_debit",
    "期初贷方余额": "opening_credit",
    "本期借方发生额": "period_debit",
    "本期贷方发生额": "period_credit",
    "期末借方余额": "closing_debit",
    "期末贷方余额": "closing_credit",
    "期初余额": "opening_balance",
    "期初方向": "opening_direction",
    "期末余额": "closing_balance",
    "期末方向": "closing_direction",
}

_MULTIDIMENSIONAL_BALANCE_COLUMNS = {
    "科目层级",
    "科目",
    "科目描述",
    "方向",
    "期初余额",
    "本期借方",
    "本期贷方",
    "方向.1",
    "期末余额",
}

_ACCOUNT_LEVELS = {
    "一级": 1,
    "二级": 2,
    "三级": 3,
    "四级": 4,
    "五级": 5,
    "六级": 6,
    "七级": 7,
    "八级": 8,
    "九级": 9,
    "十级": 10,
}

_ACCOUNT_NATURES = {
    "1": "资产",
    "2": "负债",
    "3": "共同",
    "4": "权益",
    "5": "成本",
    "6": "损益",
}

_AUXILIARY_DIMENSIONS = (
    "部门",
    "内部",
    "经营业务",
    "往来",
    "合同",
    "子目",
    "线路",
    "项目",
    "备用",
)


def _is_blank(value: object) -> bool:
    return value is None or bool(pd.isna(value)) or str(value).strip() == ""


def _text(value: object) -> str:
    if _is_blank(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _stable_id(*parts: object) -> str:
    text = "\x1f".join(_text(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _apply_mapping(
    raw: pd.DataFrame,
    defaults: dict[str, str],
    mapping: dict[str, str],
) -> pd.DataFrame:
    rename = {source: target for source, target in defaults.items() if source in raw}
    for left, right in mapping.items():
        if left in raw.columns:
            rename[left] = right
        elif right in raw.columns:
            rename[right] = left
    return raw.rename(columns=rename).copy()


def _require_columns(
    frame: pd.DataFrame,
    required: set[str],
    source_name: str,
) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise InputProtocolError(f"{source_name}缺少必填字段：{missing}")


def _parse_amounts(
    series: pd.Series,
    scale: int,
    field_name: str,
) -> pd.Series:
    values: list[int] = []
    for row_index, value in series.items():
        try:
            values.append(0 if _is_blank(value) else parse_minor(value, scale))
        except (MoneyParseError, MoneyPrecisionError) as exc:
            raise InputProtocolError(
                f"{field_name}第{row_index}行金额无法精确解析：{value!r}"
            ) from exc
    return pd.Series(values, index=series.index, dtype="int64")


def normalize_journal_frame(
    raw: pd.DataFrame,
    mapping: dict[str, str],
    spec: RunSpec,
) -> pd.DataFrame:
    source_file = _text(raw.attrs.get("source_file"))
    source_sheet = _text(raw.attrs.get("source_sheet"))
    frame = _apply_mapping(raw, _JOURNAL_COLUMNS, mapping)
    if "fiscal_period" not in frame and "voucher_date" in frame:
        try:
            frame["fiscal_period"] = pd.to_datetime(
                frame["voucher_date"],
                errors="raise",
            ).dt.strftime("%Y-%m")
        except (TypeError, ValueError) as exc:
            raise InputProtocolError("序时账存在无法识别的凭证日期") from exc
    if "voucher_type" not in frame:
        frame["voucher_type"] = "记"
    if "original_line_id" not in frame:
        source_rows = (
            frame["__source_row__"]
            if "__source_row__" in frame
            else pd.Series(range(2, len(frame) + 2), index=frame.index)
        )
        frame["original_line_id"] = source_rows.map(
            lambda value: f"source-row-{_text(value)}"
        )
    _require_columns(
        frame,
        {
            "voucher_date",
            "fiscal_period",
            "voucher_type",
            "voucher_no",
            "original_line_id",
            "summary",
            "level1_account_code",
            "level1_account_name",
            "account_code",
            "account_name",
            "debit",
            "credit",
            "counterparty_level1",
            "match_type",
        },
        "序时账",
    )

    result = pd.DataFrame(index=frame.index)
    result["source_file"] = source_file
    result["source_sheet"] = source_sheet
    if "__source_row__" in frame:
        result["source_row"] = frame["__source_row__"].astype("int64")
    else:
        result["source_row"] = pd.Series(
            range(2, len(frame) + 2),
            index=frame.index,
            dtype="int64",
        )
    try:
        voucher_dates = pd.to_datetime(
            frame["voucher_date"],
            errors="raise",
        )
        period_start = pd.Timestamp(spec.period_start)
        period_end = pd.Timestamp(spec.period_end)
    except (TypeError, ValueError) as exc:
        raise InputProtocolError("序时账存在无法识别的凭证日期") from exc
    if period_start > period_end:
        raise InputProtocolError("会计期间开始日期不得晚于结束日期")
    outside_period = ~voucher_dates.between(period_start, period_end)
    if outside_period.any():
        rows = result.loc[outside_period, "source_row"].tolist()
        raise InputProtocolError(f"序时账凭证日期超出会计期间：{rows}")
    result["voucher_date"] = voucher_dates.dt.strftime("%Y-%m-%d")

    for field in (
        "fiscal_period",
        "voucher_type",
        "voucher_no",
        "original_line_id",
        "summary",
        "level1_account_code",
        "level1_account_name",
        "account_code",
        "account_name",
        "counterparty_level1",
        "match_type",
    ):
        result[field] = frame[field].map(_text)
    required_text = (
        "fiscal_period",
        "voucher_type",
        "voucher_no",
        "original_line_id",
        "account_code",
        "account_name",
        "level1_account_code",
        "level1_account_name",
    )
    missing_text = [
        field for field in required_text if result[field].eq("").any()
    ]
    if missing_text:
        raise InputProtocolError(f"序时账存在空白必填字段：{missing_text}")

    result["debit_minor"] = _parse_amounts(
        frame["debit"], spec.currency_scale, "借方发生额"
    )
    result["credit_minor"] = _parse_amounts(
        frame["credit"], spec.currency_scale, "贷方发生额"
    )
    two_sided = result["debit_minor"].ne(0) & result["credit_minor"].ne(0)
    if two_sided.any():
        rows = result.loc[two_sided, "source_row"].tolist()
        raise InputProtocolError(f"同一分录行借贷方同时存在金额：{rows}")
    result["signed_minor"] = result["debit_minor"] - result["credit_minor"]
    result["source_side"] = "none"
    result.loc[result["debit_minor"].ne(0), "source_side"] = "debit"
    result.loc[result["credit_minor"].ne(0), "source_side"] = "credit"
    result["source_amount_minor"] = result["debit_minor"].where(
        result["debit_minor"].ne(0),
        result["credit_minor"],
    )
    result["voucher_id"] = [
        _stable_id(
            spec.entity_name,
            spec.period_start,
            fiscal_period,
            voucher_type,
            voucher_no,
        )
        for fiscal_period, voucher_type, voucher_no in zip(
            result["fiscal_period"],
            result["voucher_type"],
            result["voucher_no"],
            strict=True,
        )
    ]
    result["split_index"] = (
        result.groupby(
            ["voucher_id", "original_line_id"],
            sort=False,
        )
        .cumcount()
        .astype("int64")
    )
    result["line_no"] = (
        result.groupby("voucher_id", sort=False).cumcount().add(1).astype("int64")
    )
    result["line_id"] = [
        _stable_id(source_file, source_sheet, source_row, split_index)
        for source_row, split_index in zip(
            result["source_row"],
            result["split_index"],
            strict=True,
        )
    ]
    result["account_path"] = [
        level1 if level1 == account else f"{level1}/{account}"
        for level1, account in zip(
            result["level1_account_name"],
            result["account_name"],
            strict=True,
        )
    ]
    return result.reset_index(drop=True)


def _normalize_balance_amounts(
    frame: pd.DataFrame,
    scale: int,
) -> pd.DataFrame:
    result = frame.copy()
    for prefix in ("opening", "closing"):
        debit = f"{prefix}_debit"
        credit = f"{prefix}_credit"
        balance = f"{prefix}_balance"
        direction = f"{prefix}_direction"
        if debit in result and credit in result:
            result[f"{debit}_minor"] = _parse_amounts(
                result[debit], scale, debit
            )
            result[f"{credit}_minor"] = _parse_amounts(
                result[credit], scale, credit
            )
            continue
        _require_columns(result, {balance, direction}, "余额表")
        amounts = _parse_amounts(result[balance], scale, balance)
        directions = result[direction].map(_text)
        invalid = ~directions.isin({"借", "贷", "借方", "贷方", "debit", "credit"})
        if invalid.any():
            raise InputProtocolError(f"余额方向无法识别：{directions[invalid].tolist()}")
        is_debit = directions.isin({"借", "借方", "debit"})
        result[f"{debit}_minor"] = amounts.where(is_debit, 0).astype("int64")
        result[f"{credit}_minor"] = amounts.where(~is_debit, 0).astype("int64")
    _require_columns(result, {"period_debit", "period_credit"}, "余额表")
    result["period_debit_minor"] = _parse_amounts(
        result["period_debit"], scale, "本期借方发生额"
    )
    result["period_credit_minor"] = _parse_amounts(
        result["period_credit"], scale, "本期贷方发生额"
    )
    return result


def _account_paths(accounts: pd.DataFrame) -> dict[str, str]:
    names = dict(zip(accounts["account_code"], accounts["account_name"], strict=True))
    parents = dict(
        zip(
            accounts["account_code"],
            accounts["parent_account_code"],
            strict=True,
        )
    )
    paths: dict[str, str] = {}

    def build(code: str, trail: frozenset[str]) -> str:
        if code in paths:
            return paths[code]
        if code in trail:
            raise InputProtocolError(f"科目层级存在循环：{code}")
        parent = parents[code]
        if not parent:
            paths[code] = names[code]
        elif parent not in names:
            raise InputProtocolError(f"科目{code}的上级科目不存在：{parent}")
        else:
            paths[code] = f"{build(parent, trail | {code})}/{names[code]}"
        return paths[code]

    for account_code in names:
        build(account_code, frozenset())
    return paths


def _is_multidimensional_balance_export(frame: pd.DataFrame) -> bool:
    return _MULTIDIMENSIONAL_BALANCE_COLUMNS <= set(frame.columns)


def _normalize_multidimensional_balance_export(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """把科目级次和多列辅助组合恢复为标准余额字段。"""
    result = frame.copy()
    result["account_code"] = result["科目"].map(_text)
    result["account_name"] = result["科目描述"].map(_text)
    result["account_nature"] = result["account_code"].str[:1].map(
        {**_ACCOUNT_NATURES, "9": "重分类"}
    )
    unknown = sorted(
        set(result.loc[result["account_nature"].isna(), "account_code"])
    )
    if unknown:
        raise InputProtocolError(f"无法按科目编码判断科目性质：{unknown}")
    result["opening_direction"] = result["方向"].map(_text)
    result["closing_direction"] = result["方向.1"].map(_text)
    result["period_debit"] = result["本期借方"]
    result["period_credit"] = result["本期贷方"]

    row_types: list[str] = []
    account_levels: list[int] = []
    parent_codes: list[str] = []
    owner_codes: list[str] = []
    dimension_types: list[str] = []
    dimension_codes: list[str] = []
    dimension_names: list[str] = []
    stacks: dict[str, dict[int, str]] = {}

    for _, row in result.iterrows():
        level_text = _text(row["科目层级"])
        account_code = _text(row["account_code"])
        if level_text == "辅助":
            row_types.append("辅助")
            account_levels.append(0)
            parent_codes.append("")
            owner_codes.append(account_code)
            payload: dict[str, dict[str, str]] = {}
            labels: list[str] = []
            for dimension in _AUXILIARY_DIMENSIONS:
                code = _text(row.get(dimension))
                name = _text(row.get(f"{dimension}描述"))
                if code in {"", "0", "无"} and name in {"", "无"}:
                    continue
                payload[dimension] = {"编码": code, "描述": name}
                label = " ".join(value for value in (code, name) if value)
                labels.append(f"{dimension}:{label}")
            dimension_types.append("组合")
            dimension_codes.append(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
            dimension_names.append("；".join(labels) or "无有效辅助值")
            continue

        if level_text not in _ACCOUNT_LEVELS:
            raise InputProtocolError(f"科目层级无法识别：{level_text}")
        account_level = _ACCOUNT_LEVELS[level_text]
        company = _text(row.get("公司编码")) or "默认主体"
        stack = stacks.setdefault(company, {})
        if account_level > 1 and account_level - 1 not in stack:
            raise InputProtocolError(f"科目{account_code}缺少上一级科目")
        parent_code = stack.get(account_level - 1, "")
        for stale_level in [level for level in stack if level >= account_level]:
            del stack[stale_level]
        stack[account_level] = account_code

        row_types.append("科目")
        account_levels.append(account_level)
        parent_codes.append(parent_code)
        owner_codes.append("")
        dimension_types.append("")
        dimension_codes.append("")
        dimension_names.append("")

    result["row_type"] = row_types
    result["account_level"] = account_levels
    result["parent_account_code"] = parent_codes
    result["owner_account_code"] = owner_codes
    result["dimension_type"] = dimension_types
    result["dimension_code"] = dimension_codes
    result["dimension_name"] = dimension_names
    return result


def _derive_balance_metadata(frame: pd.DataFrame) -> pd.DataFrame:
    if "account_code" not in frame:
        return frame
    result = frame.copy()
    codes = result["account_code"].map(_text)
    dimensions = (
        result["核算维度"].map(_text)
        if "核算维度" in result
        else pd.Series("", index=result.index)
    )
    auxiliary_mask = dimensions.ne("")
    if "row_type" not in result:
        result["row_type"] = auxiliary_mask.map(
            {False: "科目", True: "辅助"}
        )
    if "account_level" not in result:
        result["account_level"] = codes.map(lambda value: value.count(".") + 1)
    if "parent_account_code" not in result:
        result["parent_account_code"] = codes.map(
            lambda value: value.rsplit(".", 1)[0] if "." in value else ""
        )
    if "account_nature" not in result:
        result["account_nature"] = codes.str[:1].map(_ACCOUNT_NATURES)
        unknown = sorted(set(codes[result["account_nature"].isna()]))
        if unknown:
            raise InputProtocolError(f"无法按科目编码判断科目性质：{unknown}")
    if "owner_account_code" not in result:
        result["owner_account_code"] = ""
        result.loc[auxiliary_mask, "owner_account_code"] = codes[auxiliary_mask]
    if "dimension_type" not in result:
        result["dimension_type"] = ""
        result.loc[auxiliary_mask, "dimension_type"] = "组合"
    if "dimension_code" not in result:
        result["dimension_code"] = ""
        result.loc[auxiliary_mask, "dimension_code"] = dimensions[auxiliary_mask]
    if "dimension_name" not in result:
        result["dimension_name"] = ""
        result.loc[auxiliary_mask, "dimension_name"] = dimensions[auxiliary_mask]
    return result


def normalize_trial_balance_frame(
    raw: pd.DataFrame,
    mapping: dict[str, str],
    spec: RunSpec,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    source = (
        _normalize_multidimensional_balance_export(raw)
        if _is_multidimensional_balance_export(raw)
        else raw
    )
    mapped = _apply_mapping(source, _BALANCE_COLUMNS, mapping)
    if "account_code" in mapped:
        code_blank = mapped["account_code"].map(_text).eq("")
        owner_blank = (
            mapped["owner_account_code"].map(_text).eq("")
            if "owner_account_code" in mapped
            else pd.Series(True, index=mapped.index)
        )
        mapped = mapped.loc[~(code_blank & owner_blank)].copy()
    frame = _derive_balance_metadata(
        mapped
    )
    row_type = (
        frame["row_type"].map(_text)
        if "row_type" in frame
        else pd.Series("科目", index=frame.index)
    )
    auxiliary_mask = row_type.isin({"辅助", "auxiliary"})
    if "owner_account_code" in frame:
        auxiliary_mask |= frame["owner_account_code"].map(_text).ne("")

    account_rows = frame.loc[~auxiliary_mask].copy()
    _require_columns(
        account_rows,
        {
            "account_code",
            "account_name",
            "account_level",
            "parent_account_code",
            "account_nature",
        },
        "科目余额表",
    )
    if account_rows.empty:
        raise InputProtocolError("科目余额表没有科目行")
    for field in (
        "account_code",
        "account_name",
        "parent_account_code",
        "account_nature",
    ):
        account_rows[field] = account_rows[field].map(_text)
    if account_rows["account_code"].eq("").any() or account_rows[
        "account_name"
    ].eq("").any():
        raise InputProtocolError("科目余额表存在空白科目编码或名称")
    try:
        account_rows["account_level"] = account_rows["account_level"].astype("int64")
    except (TypeError, ValueError) as exc:
        raise InputProtocolError("科目级次必须是整数") from exc

    account_rows = _normalize_balance_amounts(
        account_rows,
        spec.currency_scale,
    )
    amount_columns = [
        "opening_debit_minor",
        "opening_credit_minor",
        "period_debit_minor",
        "period_credit_minor",
        "closing_debit_minor",
        "closing_credit_minor",
    ]
    duplicate_codes = account_rows.loc[
        account_rows["account_code"].duplicated(keep=False),
        "account_code",
    ].unique()
    comparison_columns = [
        "account_name",
        "account_level",
        "parent_account_code",
        "account_nature",
        *amount_columns,
    ]
    for account_code in duplicate_codes:
        duplicates = account_rows.loc[
            account_rows["account_code"].eq(account_code), comparison_columns
        ]
        if not duplicates.eq(duplicates.iloc[0]).all().all():
            raise InputProtocolError(f"科目余额表存在冲突的重复科目编码：{account_code}")
    if len(duplicate_codes):
        account_rows = account_rows.drop_duplicates("account_code", keep="first")

    parent_codes = set(account_rows["parent_account_code"]) - {""}
    account_rows["is_leaf"] = ~account_rows["account_code"].isin(parent_codes)
    account_rows["account_path"] = account_rows["account_code"].map(
        _account_paths(account_rows)
    )
    node_columns = [
        "account_code",
        "account_name",
        "account_path",
        "account_level",
        "parent_account_code",
        "is_leaf",
        "account_nature",
        *amount_columns,
    ]
    account_nodes = account_rows[node_columns].reset_index(drop=True)
    trial_balance = account_nodes.loc[account_nodes["is_leaf"]].reset_index(drop=True)

    auxiliary_rows = frame.loc[auxiliary_mask].copy()
    auxiliary_columns = [
        "owner_account_code",
        "dimension_type",
        "dimension_code",
        "dimension_name",
        "dimension_combination_id",
        *amount_columns,
    ]
    if auxiliary_rows.empty:
        auxiliary_dimensions = pd.DataFrame(columns=auxiliary_columns)
    else:
        _require_columns(
            auxiliary_rows,
            {
                "owner_account_code",
                "dimension_type",
                "dimension_code",
                "dimension_name",
            },
            "辅助余额表",
        )
        for field in (
            "owner_account_code",
            "dimension_type",
            "dimension_code",
            "dimension_name",
        ):
            auxiliary_rows[field] = auxiliary_rows[field].map(_text)
        leaf_codes = set(trial_balance["account_code"])
        invalid_owners = sorted(
            set(auxiliary_rows["owner_account_code"]) - leaf_codes
        )
        if invalid_owners:
            raise InputProtocolError(
                f"辅助项目必须关联末级科目：{invalid_owners}"
            )
        auxiliary_rows = _normalize_balance_amounts(
            auxiliary_rows,
            spec.currency_scale,
        )
        auxiliary_rows["dimension_combination_id"] = [
            _stable_id(owner, kind, code, name)
            for owner, kind, code, name in zip(
                auxiliary_rows["owner_account_code"],
                auxiliary_rows["dimension_type"],
                auxiliary_rows["dimension_code"],
                auxiliary_rows["dimension_name"],
                strict=True,
            )
        ]
        auxiliary_dimensions = auxiliary_rows[auxiliary_columns].reset_index(
            drop=True
        )
        parent_amounts = trial_balance.set_index("account_code")[amount_columns]
        auxiliary_amounts = auxiliary_dimensions.groupby(
            "owner_account_code", sort=False
        )[amount_columns].sum()
        for owner, amounts in auxiliary_amounts.iterrows():
            for column in amount_columns:
                if int(amounts[column]) != int(parent_amounts.loc[owner, column]):
                    raise InputProtocolError(
                        f"辅助维度与所属科目{owner}的{column}合计不一致"
                    )
    return trial_balance, account_nodes, auxiliary_dimensions


def align_journal_account_codes(
    journal: pd.DataFrame,
    trial_balance: pd.DataFrame,
) -> pd.DataFrame:
    result = journal.copy()
    result["source_account_code"] = result["account_code"]
    result["source_account_name"] = result["account_name"]
    leaves = list(
        zip(
            trial_balance["account_code"].map(_text),
            trial_balance["account_name"].map(_text),
            strict=True,
        )
    )
    leaf_codes = {code for code, _ in leaves}
    mappings: dict[tuple[str, str], str] = {}
    distinct_accounts = result[
        ["account_code", "account_name"]
    ].drop_duplicates()
    for code, name in distinct_accounts.itertuples(index=False, name=None):
        code = _text(code)
        name = _text(name)
        if code in leaf_codes:
            mappings[(code, name)] = code
            continue
        candidates = [
            leaf_code
            for leaf_code, leaf_name in leaves
            if (
                code.startswith(f"{leaf_code}.")
                or leaf_code.startswith(f"{code}.")
            )
            and (
                name == leaf_name
                or name.startswith(f"{leaf_name}_")
                or leaf_name.startswith(f"{name}_")
            )
        ]
        mappings[(code, name)] = candidates[0] if len(candidates) == 1 else code
    result["account_code"] = [
        mappings[(_text(code), _text(name))]
        for code, name in zip(
            result["account_code"],
            result["account_name"],
            strict=True,
        )
    ]
    return result


def read_excel_sheet(
    path: Path,
    sheet_name: str,
    header_row: int,
    *,
    excel_file: pd.ExcelFile | None = None,
) -> pd.DataFrame:
    if header_row < 1:
        raise InputProtocolError("表头行号必须从1开始")
    try:
        frame = pd.read_excel(
            excel_file or path,
            sheet_name=sheet_name,
            header=header_row - 1,
            dtype=object,
            engine="openpyxl",
        )
    except (OSError, ValueError, BadZipFile) as exc:
        raise InputProtocolError(f"Excel工作表无法读取：{path}::{sheet_name}") from exc
    frame["__source_row__"] = range(header_row + 1, header_row + 1 + len(frame))
    frame.attrs.update(
        source_file=Path(path).name,
        source_sheet=sheet_name,
    )
    return frame
