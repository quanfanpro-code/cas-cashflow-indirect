"""项目注释索引和审计底稿工作簿生成。"""

from pathlib import Path

import pandas as pd


ANNOTATION_FIELDS = (
    "项目筛选状态",
    "规则编号",
    "最终判断",
    "原始金额片段",
    "本项目调节金额",
    "计算过程说明",
    "规则判断理由",
    "是否人工智能判断",
    "人工智能建议",
    "建议理由",
    "支持证据",
    "相反证据",
    "未采用方案",
    "置信度",
    "是否人工升级",
    "最终采用处理",
    "申请占用金额",
    "实际消耗金额",
    "剩余未分类金额",
    "占用类型",
    "配套抵销标识",
    "防重复核对结论",
    "项目核对结论",
    "业务事件标识",
    "金额片段标识",
)

MACHINE_REFERENCE_SHEET = "机器用复核数据"

_FINAL_DECISION_TEXT = {
    "included": "算进本项目，影响经营活动现金流",
    "excluded": "不算进本项目，不影响经营活动现金流",
    "reclassified": "改归其他项目，本项目不算，由其他项目算",
    "review_required": "需人工复核",
    "paired_offset": "按准则需在两个项目同时反映，不是重复计算",
    "ai_classified": "算进本项目，影响经营活动现金流",
}

_ROW_RELATION_TEXT = {
    "算进本项目，影响经营活动现金流": "本行算进本项目，影响经营活动现金流",
    "不算进本项目，不影响经营活动现金流": "本行不算进本项目，不影响经营活动现金流",
    "改归其他项目，本项目不算，由其他项目算": "本行改归其他项目，本项目不算，由其他项目算",
    "按准则需在两个项目同时反映，不是重复计算": "本行按准则需在两个项目同时反映，不是重复计算",
}

_RULE_DISPLAY_TEXT = {
    "NONCASH-DEPRECIATION": "本期计提折旧，没有付现金，所以要加回",
    "NONCASH-LONG-PREPAID": "本期摊销长期待摊费用，没有付现金，所以要加回",
    "FIN-NONOPERATING-FINANCE-COST": "属于筹资、投资性质的财务费用，所以要加回",
    "FIN-INVESTMENT-RESULT": "投资损益不属于经营活动，所以要剔除",
    "WC-ITEM-13": "按存货科目期初、期末余额计算",
    "WC-ITEM-14": "按应收科目期初、期末余额计算",
    "WC-ITEM-15": "按应付科目期初、期末余额计算",
    "IMP-ASSET": "按资产减值损失的实际损益影响计算",
    "IMP-CREDIT": "按信用减值损失的实际损益影响计算",
    "TAX-DEFERRED-PROFIT-ROUTE": "按递延所得税变动中影响净利润的部分计算",
    "TAX-DEFERRED-BALANCE-FALLBACK": "按递延所得税科目期初、期末余额计算",
}

_RESULT_CHECK_STATUS_TEXT = {
    "pass": "通过",
    "fail": "未通过",
    "warning": "有提醒",
}

_RECONCILIATION_STATUS_TEXT = {
    "pass": "已通过",
    "fail": "未通过",
    "warning": "有提醒",
}

_JOURNAL_DISPLAY_HEADERS = {
    "Unnamed: 0": "原始行号",
    "匹配类型": "对方科目是怎么匹配出来的",
    "候选项目": "可能涉及哪些附表项目",
}

_MATCH_TYPE_DISPLAY = {
    "标准模板": "按标准模板匹配",
    "算法生成": "按金额算法匹配",
    "算法生成(兜底拆分)": "按金额算法匹配（拆分明细）",
    "算法生成(异常剩余)": "算法匹配后的剩余行（建议留意）",
    "结转损益规则": "损益结转规则",
}


def _display_header(header: object) -> str:
    text = "" if header is None else str(header)
    return _JOURNAL_DISPLAY_HEADERS.get(text, text)


def _display_journal_value(header: object, value: object) -> object:
    text = "" if header is None else str(header)
    if text == "匹配类型" and isinstance(value, str):
        return _MATCH_TYPE_DISPLAY.get(value, value)
    return value


def _unmatched_annotation() -> dict:
    return {
        "项目筛选状态": "不属于本项目",
        "规则编号": "",
        "最终判断": "不算进本项目，不影响经营活动现金流",
        "原始金额片段": "",
        "本项目调节金额": 0,
        "计算过程说明": "该行没有进入本项目，所以没有金额处理",
        "规则判断理由": "",
        "是否人工智能判断": "否",
        "人工智能建议": "",
        "建议理由": "",
        "支持证据": "",
        "相反证据": "未发现相反证据",
        "未采用方案": "",
        "置信度": "",
        "是否人工升级": "否",
        "最终采用处理": "本行不算进本项目，不影响经营活动现金流",
        "申请占用金额": 0,
        "实际消耗金额": 0,
        "剩余未分类金额": 0,
        "占用类型": "",
        "配套抵销标识": "",
        "防重复核对结论": "不适用",
        "项目核对结论": "不适用",
        "业务事件标识": "",
        "金额片段标识": "",
    }


def build_item_trace_index(
    trace: pd.DataFrame,
) -> dict[int, dict[int, dict]]:
    """把判断轨迹按项目编号和原始行号聚合为注释索引。"""
    index: dict[int, dict[int, dict]] = {}
    if trace is None or trace.empty:
        return index
    for item_no, item_rows in trace.groupby("item_no"):
        item_no = int(item_no)
        index[item_no] = {}
        for source_row, row_group in item_rows.groupby("source_row"):
            index[item_no][int(source_row)] = _annotate_rows(row_group)
    return index


def _ordered_rows(rows: pd.DataFrame) -> list[dict]:
    """按 fragment_id 和 trace_id 稳定排序，保证换行拼接顺序稳定。"""
    records = rows.to_dict("records")
    records.sort(
        key=lambda record: (
            str(record.get("fragment_id", "") or ""),
            str(record.get("trace_id", "") or ""),
        )
    )
    return records


def _join_unique(values: list[str]) -> str:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return "\n".join(seen)


def _annotate_rows(rows: pd.DataFrame) -> dict:
    ordered = _ordered_rows(rows)
    first = ordered[0]
    final_decision = str(first.get("final_decision", ""))
    decision_text = _FINAL_DECISION_TEXT.get(final_decision, final_decision)
    item_no = int(first.get("item_no", 0))
    claim_amount = sum(
        int(row.get("claim_amount_minor", 0) or 0) for row in ordered
    )
    consumed = sum(
        int(row.get("physical_consumed_minor", 0) or 0) for row in ordered
    )
    remaining = sum(
        int(row.get("remaining_amount_minor", 0) or 0) for row in ordered
    )
    occupancy_types = {
        str(row.get("occupancy_type", "") or "") for row in ordered
    }
    occupancy_type = (
        "paired_offset"
        if "paired_offset" in occupancy_types
        else "exclusive"
        if "exclusive" in occupancy_types
        else next(iter(occupancy_types), "")
    )
    paired_ids = {
        str(row.get("paired_offset_id"))
        for row in ordered
        if row.get("paired_offset_id") is not None
        and not pd.isna(row.get("paired_offset_id"))
        and str(row.get("paired_offset_id")).strip()
    }
    paired_text = "\n".join(sorted(paired_ids))
    confidences = [
        float(row["confidence"])
        for row in ordered
        if row.get("confidence") is not None
        and not pd.isna(row.get("confidence"))
    ]
    confidence_text = (
        f"{sum(confidences) / len(confidences) * 100:.0f}%" if confidences else ""
    )
    human_review = any(
        bool(row.get("human_review_required", False)) for row in ordered
    )
    is_ai = any(
        str(row.get("candidate_status", "") or "") == "ai_candidate"
        for row in ordered
    )
    return {
        "项目筛选状态": "属于本项目",
        "规则编号": _join_unique(
            [row.get("rule_id", "") for row in ordered]
        ),
        "最终判断": decision_text,
        "原始金额片段": claim_amount + remaining,
        "本项目调节金额": claim_amount,
        "计算过程说明": "这笔金额只出现一次，不会重复计算",
        "规则判断理由": _join_unique(
            [row.get("decision_reason", "") for row in ordered]
        ),
        "是否人工智能判断": "是" if is_ai else "否",
        "人工智能建议": _join_unique(
            [row.get("recommendation", "") for row in ordered]
        ),
        "建议理由": _join_unique(
            [row.get("recommendation_reason", "") for row in ordered]
        ),
        "支持证据": _join_unique(
            [row.get("supporting_evidence", "") for row in ordered]
        ),
        "相反证据": _join_unique(
            [row.get("contrary_evidence", "") for row in ordered]
        ),
        "未采用方案": _join_unique(
            [row.get("rejected_alternatives", "") for row in ordered]
        ),
        "置信度": confidence_text,
        "是否人工升级": "是" if human_review else "否",
        "最终采用处理": f"第{item_no}项{decision_text}",
        "申请占用金额": claim_amount,
        "实际消耗金额": consumed,
        "剩余未分类金额": remaining,
        "占用类型": occupancy_type,
        "配套抵销标识": paired_text,
        "防重复核对结论": (
            "按准则需在两个项目同时反映，不是重复计算" if occupancy_type == "paired_offset"
            else "这笔金额只出现一次，没有重复" if occupancy_type == "exclusive"
            else "不适用"
        ),
        "项目核对结论": "已计入本项目，请用明细合计和差异公式复核",
        "业务事件标识": _join_unique(
            [row.get("business_event_id", "") for row in ordered]
        ),
        "金额片段标识": _join_unique(
            [row.get("fragment_id", "") for row in ordered]
        ),
    }


def annotation_for_row(
    item_no: int,
    source_row: int,
    index: dict[int, dict[int, dict]],
) -> dict:
    """返回指定项目指定原始行的注释；没有轨迹时返回未命中注释。"""
    item_index = index.get(int(item_no))
    if item_index is None:
        return _unmatched_annotation()
    return item_index.get(
        int(source_row),
        _unmatched_annotation(),
    )


# ---------------------------------------------------------------------------
# 18张表审计底稿工作簿
# ---------------------------------------------------------------------------

import re

import openpyxl
import xlsxwriter


class WorkbookStructureError(ValueError):
    """工作簿结构校验失败。"""


EXCEL_MAX_ROWS = 1_048_576
_TOP_ROWS_PER_ITEM = 12
_RESULT_SHEET = "附表结果"
_FULL_JOURNAL_SHEET = "全量序时账"
_MONEY_FORMAT = "#,##0.00"


def _frame_items(frame: pd.DataFrame, item_no: int) -> pd.DataFrame:
    """按项目编号过滤，NaN 安全的 item_no 处理。"""
    if frame is None or frame.empty or "item_no" not in frame.columns:
        return pd.DataFrame()
    numbers = pd.to_numeric(frame["item_no"], errors="coerce")
    return frame.loc[numbers.fillna(-1).astype("int64").eq(int(item_no))]


def _sheet_name(item_no: int, item_name: str) -> str:
    name = f"{item_no:02d}{item_name}"
    if re.search(r"[\\/\?\*\[\]:]", name):
        raise WorkbookStructureError(f"项目名称含非法工作表字符：{item_name}")
    return name


def _formats(workbook) -> dict:
    """统一样式：标题、表头、正文、金额、加粗正文、超链接。"""
    return {
        "title": workbook.add_format(
            {"bold": True, "font_size": 14, "font_name": "等线"}
        ),
        "header": workbook.add_format(
            {
                "bold": True,
                "bg_color": "#D9E1F2",
                "border": 1,
                "align": "center",
                "valign": "vcenter",
                "text_wrap": True,
                "font_name": "等线",
                "font_size": 10,
            }
        ),
        "body": workbook.add_format(
            {
                "border": 1,
                "text_wrap": True,
                "valign": "top",
                "font_name": "等线",
                "font_size": 10,
            }
        ),
        "money": workbook.add_format(
            {
                "border": 1,
                "num_format": _MONEY_FORMAT,
                "align": "right",
                "valign": "top",
                "font_name": "等线",
                "font_size": 10,
            }
        ),
        "bold": workbook.add_format(
            {
                "border": 1,
                "bold": True,
                "text_wrap": True,
                "valign": "top",
                "font_name": "等线",
                "font_size": 10,
            }
        ),
        "link": workbook.add_format(
            {
                "border": 1,
                "font_color": "#0563C1",
                "underline": 1,
                "valign": "top",
                "font_name": "等线",
                "font_size": 10,
            }
        ),
    }


def _write_value(
    worksheet,
    row: int,
    col: int,
    value: object,
    fmt: dict,
    money: bool = False,
) -> None:
    """写一个单元格；None 跳过；数字写数字、其余写文本。"""
    if value is None:
        return
    if isinstance(value, float) and pd.isna(value):
        return
    cell_format = fmt["money"] if money else fmt["body"]
    if isinstance(value, bool):
        worksheet.write_string(row, col, "是" if value else "否", cell_format)
    elif isinstance(value, (int, float)):
        worksheet.write_number(row, col, value, cell_format)
    else:
        worksheet.write_string(row, col, str(value), cell_format)


def _bridge_source_text(
    bridge: pd.Series,
    account_labels: dict[str, str] | None,
) -> str:
    source_kind = str(bridge.get("source_kind", "") or "")
    account_code = str(bridge.get("account_code", "") or "")
    if source_kind == "auxiliary":
        return "辅助核算：" + account_code
    if source_kind == "correction":
        return "非现金、非经营修正"
    if account_code:
        labels = account_labels or {}
        account_name = labels.get(account_code, "")
        if account_name:
            return f"科目余额表：{account_code} {account_name}"
        return f"科目余额表：{account_code}"
    if source_kind == "trial_balance_fallback":
        return "科目余额表"
    return "其他来源"


def _bridge_reason_text(item_no: int, bridge: pd.Series) -> str:
    reason_code = str(bridge.get("reason_code", "") or "")
    if reason_code == "OPERATING_BALANCE_INCLUDED":
        return {
            13: "存货减少说明卖掉了存货、结转了成本，要加回；存货增加要扣减",
            14: "应收减少说明收回了钱，要加回；应收增加说明钱还没收到，要扣减",
            15: "应付增加说明钱还没付，要加回；应付减少说明多付了钱，要扣减",
        }.get(item_no, "科目余额变化中，真正和经营现金有关的部分")
    if reason_code == "NONOPERATING_BALANCE":
        return "这笔余额变动不属于经营活动，所以不计入本项目"
    if reason_code == "GOVERNMENT_GRANT_TO_ITEM16":
        return "政府补助单独在第16项处理，不在这里重复"
    if reason_code == "WORKING_CAPITAL_SUMMARY":
        return "上面各行加总，就是本项目最终金额"
    if reason_code == "ALLOWANCE_WRITEOFF":
        return "存货跌价准备核销原值，不能重复算作经营现金"
    if reason_code == "DIRECT_BAD_DEBT_ALREADY_ADJUSTED":
        return "坏账损失已经在第2项加回，应收减少要剔除，避免重复"
    if reason_code == "DEBT_RESTRUCTURING_BALANCE_CORRECTION":
        return "债务重组、债务豁免等没有发生现金收付，要从余额变化里剔除"
    if reason_code == "PROFIT_ROUTE":
        return "这笔递延所得税变动影响了净利润，计入本项目"
    if reason_code == "NON_PROFIT_ROUTE":
        return "这笔递延所得税变动没有影响净利润，不计入本项目"
    if reason_code == "TECHNICAL_CLOSING":
        return "结转分录，不影响本项目"
    if reason_code == "GROSS_BALANCE_FALLBACK":
        return "按科目期初、期末余额计算，已剔除非经营变化"
    return str(bridge.get("reason", "") or "")


def _item_top_area(
    worksheet,
    fmt: dict,
    item_no: int,
    item_name: str,
    frozen_amount: int,
    divisor: int,
    bridges: pd.DataFrame,
    issues: pd.DataFrame,
    display_unit: str = "元",
    account_labels: dict[str, str] | None = None,
) -> int:
    """写项目顶部区域，返回下一可用行（0-based）。"""
    row = 0
    worksheet.write(
        row, 0, f"第{item_no}项　{item_name}　审计底稿", fmt["title"]
    )
    row += 1
    worksheet.write_url(
        row,
        2,
        f"internal:'{_RESULT_SHEET}'!A1",
        fmt["link"],
        "返回附表结果",
    )
    row += 1
    worksheet.write(row, 0, "本项目最终金额", fmt["bold"])
    worksheet.write_number(row, 1, frozen_amount / divisor, fmt["money"])
    row += 2

    worksheet.write(row, 0, "从科目余额表怎么算出本项目", fmt["bold"])
    row += 1
    item_bridges = _frame_items(bridges, item_no)
    if item_bridges.empty:
        worksheet.write(row, 0, "本项目不用科目余额表计算", fmt["body"])
        row += 1
    else:
        bridge_headers = [
            "取数来源", "期初余额", "期末余额",
            "需要剔除的金额", "计入本项目金额",
            "一句话说明",
        ]
        for col, header in enumerate(bridge_headers):
            worksheet.write(row, col, header, fmt["header"])
        row += 1
        for _, bridge in item_bridges.iterrows():
            values = [
                _bridge_source_text(bridge, account_labels),
                float(bridge.get("opening_minor", 0) or 0) / divisor,
                float(bridge.get("closing_minor", 0) or 0) / divisor,
                float(bridge.get("corrections_minor", 0) or 0) / divisor,
                float(bridge.get("final_amount_minor", 0) or 0) / divisor,
                _bridge_reason_text(item_no, bridge),
            ]
            for col, value in enumerate(values):
                _write_value(worksheet, row, col, value, fmt, money=col in (1, 2, 3, 4))
            row += 1
    row += 1

    worksheet.write(row, 0, "需要留意的异常情况", fmt["bold"])
    row += 1
    item_issues = _frame_items(issues, item_no)
    if item_issues.empty:
        worksheet.write(row, 0, "没有发现异常", fmt["body"])
        row += 1
    else:
        issue_headers = [
            "异常类型", "金额", "事实描述",
            "AI建议", "最终怎么处理",
        ]
        for col, header in enumerate(issue_headers):
            worksheet.write(row, col, header, fmt["header"])
        row += 1
        for _, issue in item_issues.iterrows():
            final = issue.get("final_decision", "") or ""
            values = [
                issue.get("issue_code", "") or "",
                float(issue.get("amount_minor", 0) or 0) / divisor,
                issue.get("reason", "") or "",
                issue.get("recommendation", "") or "",
                _FINAL_DECISION_TEXT.get(str(final), str(final)),
            ]
            for col, value in enumerate(values):
                _write_value(worksheet, row, col, value, fmt, money=col == 1)
            row += 1
    row += 1
    return row


def _detail_reason(item_no: int, detail: pd.Series) -> str:
    reason = str(detail.get("reason", "") or "")
    if reason:
        return reason
    return {
        3: "计提折旧没有付现金，但已经减少了利润，所以要加回",
        4: "无形资产摊销没有付现金，但已经减少了利润，所以要加回",
        5: "长期待摊费用摊销没有付现金，但已经减少了利润，所以要加回",
        14: "应收科目余额变化中，真正和经营现金有关的部分",
        15: "应付科目余额变化中，真正和经营现金有关的部分",
    }.get(item_no, "按已确认规则处理")


def _write_item_detail_region(
    worksheet,
    fmt: dict,
    start_row: int,
    item_no: int,
    details: pd.DataFrame,
    divisor: int,
    display_unit: str = "元",
) -> int:
    """写逐笔计算明细和合计、差异公式，返回下一可用行（0-based）。"""
    row = start_row
    worksheet.write(row, 0, "逐笔计算明细", fmt["bold"])
    row += 1
    detail_headers = [
        "凭证日期", "凭证号", "摘要",
        f"原始金额",
        "计入本项目金额",
        "处理结果", "取数口径", "为什么这样处理",
    ]
    for col, header in enumerate(detail_headers):
        worksheet.write(row, col, header, fmt["header"])
    row += 1
    detail_start_excel = row + 1
    item_details = _frame_items(details, item_no)
    if item_details.empty:
        worksheet.write(row, 0, "没有逐笔明细", fmt["body"])
        row += 1
        detail_end_excel = row
    else:
        for _, detail in item_details.iterrows():
            decision = detail.get("decision", "") or ""
            rule_id = detail.get("rule_id", "") or ""
            values = [
                detail.get("voucher_date", "") or "",
                detail.get("voucher_no", "") or "",
                detail.get("summary", "") or "",
                float(detail.get("source_amount_minor", 0) or 0) / divisor,
                float(detail.get("adjustment_amount_minor", 0) or 0) / divisor,
                _FINAL_DECISION_TEXT.get(str(decision), str(decision)),
                _RULE_DISPLAY_TEXT.get(str(rule_id), "按已确认规则处理"),
                _detail_reason(item_no, detail),
            ]
            for col, value in enumerate(values):
                _write_value(worksheet, row, col, value, fmt, money=col in (3, 4))
            row += 1
        detail_end_excel = row

    worksheet.write(row, 4, "明细合计", fmt["bold"])
    worksheet.write_formula(
        row,
        5,
        f"=SUM(F{detail_start_excel}:F{detail_end_excel})",
        fmt["money"],
    )
    total_row_excel = row + 1
    row += 1
    worksheet.write(row, 4, "差异（本项目最终金额减明细合计）", fmt["body"])
    worksheet.write_formula(
        row, 5, f"=B3-F{total_row_excel}", fmt["money"]
    )
    row += 1
    return row


def _row_relation_text(annotation: dict) -> str:
    final = str(annotation.get("最终判断", ""))
    return _ROW_RELATION_TEXT.get(
        final,
        "本行不算进本项目，不影响经营活动现金流",
    )


def _write_full_journal_region(
    worksheet,
    fmt: dict,
    start_row: int,
    journal_header: list,
    journal_records: list,
    journal_header_row: int,
    item_no: int,
    annotation_index: dict,
    relevant_rows: set[int],
) -> tuple[int, int]:
    """写本项目相关凭证行，右侧只加一列人话关系说明。"""
    row = start_row
    total = len(journal_records)
    unmatched = total - len(relevant_rows)
    worksheet.write(
        row,
        0,
        (
            f"本项目相关的凭证行（序时账共{total}行，本项目涉及"
            f"{len(relevant_rows)}行，其余{unmatched}行不逐行展示）"
        ),
        fmt["bold"],
    )
    row += 1
    header_row = row
    for col, header in enumerate(journal_header):
        display = _display_header(header)
        if display:
            worksheet.write(row, col, display, fmt["header"])
    relation_col = len(journal_header)
    worksheet.write(row, relation_col, "本行和本项目的关系", fmt["header"])
    row += 1
    data_rows = 0
    write_number = worksheet.write_number
    write_string = worksheet.write_string
    body_fmt = fmt["body"]
    for record_index, record in enumerate(journal_records):
        source_row = journal_header_row + record_index + 1
        if source_row not in relevant_rows:
            continue
        annotation = annotation_for_row(
            item_no, source_row, annotation_index
        )
        col = 0
        for header, value in zip(journal_header, record):
            if value is not None:
                shown = _display_journal_value(header, value)
                if isinstance(shown, (int, float)) and not isinstance(shown, bool):
                    write_number(row, col, shown, body_fmt)
                else:
                    write_string(row, col, str(shown), body_fmt)
            col += 1
        write_string(row, col, _row_relation_text(annotation), body_fmt)
        row += 1
        data_rows += 1
    return header_row, data_rows


def _candidate_items_text(
    source_row: int,
    relevant_rows_by_item: dict[int, set[int]],
) -> str:
    """返回某原始行进入了哪些项目的候选（升序、逗号分隔），无则“无”。"""
    items = sorted(
        item_no
        for item_no, rows in relevant_rows_by_item.items()
        if source_row in rows
    )
    return "、".join(str(item_no) for item_no in items) if items else "无"


def _write_full_journal_sheet(
    worksheet,
    fmt: dict,
    journal_header: list,
    journal_records: list,
    journal_header_row: int,
    relevant_rows_by_item: dict[int, set[int]],
) -> tuple[int, int]:
    """写入完整全量序时账（原始全部列＋候选项目列），供人工复核。"""
    row = 0
    worksheet.write(
        row, 0, "全量输入序时账（每行右侧标注可能涉及的附表项目，供人工复核）",
        fmt["bold"],
    )
    row += 1
    for col, header in enumerate(journal_header):
        display = _display_header(header)
        if display:
            worksheet.write(row, col, display, fmt["header"])
    candidate_col = len(journal_header)
    worksheet.write(row, candidate_col, "可能涉及哪些附表项目", fmt["header"])
    header_row = row
    row += 1
    data_rows = 0
    write_number = worksheet.write_number
    write_string = worksheet.write_string
    body_fmt = fmt["body"]
    for record_index, record in enumerate(journal_records):
        source_row = journal_header_row + record_index + 1
        col = 0
        for header, value in zip(journal_header, record):
            if value is not None:
                shown = _display_journal_value(header, value)
                if isinstance(shown, (int, float)) and not isinstance(shown, bool):
                    write_number(row, col, shown, body_fmt)
                else:
                    write_string(row, col, str(shown), body_fmt)
            col += 1
        write_string(
            row,
            col,
            _candidate_items_text(source_row, relevant_rows_by_item),
            body_fmt,
        )
        row += 1
        data_rows += 1
    return header_row, data_rows


def _result_method(item_no: int, amount_minor: int, ai_amount_minor: int) -> str:
    if int(ai_amount_minor) != 0:
        return "AI判断后确定"
    if int(amount_minor) == 0:
        return "本期无此事项"
    if int(item_no) in {13, 14, 15}:
        return "按科目期初、期末余额计算"
    return "逐笔账务计算"


def _machine_reference_records(
    decision_trace: pd.DataFrame,
    details: pd.DataFrame,
    item_summary: pd.DataFrame,
) -> list[dict]:
    """从判断轨迹摘取定位号和关联关系，不复制完整机器证据。"""
    if decision_trace is None or decision_trace.empty:
        return []
    item_names = {
        int(row["item_no"]): str(row["item_name"])
        for _, row in item_summary.iterrows()
    }
    detail_by_fragment = {}
    if details is not None and not details.empty and "fragment_id" in details:
        detail_by_fragment = {
            str(row["fragment_id"]): row
            for _, row in details.iterrows()
        }
    records: list[dict] = []
    for _, row in decision_trace.iterrows():
        fragment_id = str(row.get("fragment_id", "") or "")
        detail = detail_by_fragment.get(fragment_id)
        item_no = int(row.get("item_no", 0))
        records.append(
            {
                "项目号": item_no,
                "项目名称": item_names.get(item_no, ""),
                "原始行号": int(row.get("source_row", 0) or 0),
                "凭证日期": "" if detail is None else str(detail.get("voucher_date", "") or ""),
                "凭证号": "" if detail is None else str(detail.get("voucher_no", "") or ""),
                "机器金额编号": fragment_id,
                "机器业务编号": str(row.get("business_event_id", "") or ""),
                "内部规则编号": str(row.get("rule_id", "") or ""),
            }
        )
    return records


def _write_machine_reference_sheet(
    worksheet,
    fmt: dict,
    decision_trace: pd.DataFrame,
    details: pd.DataFrame,
    item_summary: pd.DataFrame,
) -> None:
    headers = [
        "项目号", "项目名称", "原始行号", "凭证日期", "凭证号",
        "机器金额编号", "机器业务编号", "内部规则编号",
    ]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, fmt["header"])
    records = _machine_reference_records(decision_trace, details, item_summary)
    for row, record in enumerate(records, start=1):
        for col, header in enumerate(headers):
            value = record[header]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                worksheet.write_number(row, col, value, fmt["body"])
            else:
                worksheet.write_string(row, col, str(value), fmt["body"])
    worksheet.set_column(0, 7, 18)
    worksheet.freeze_panes(1, 0)


def write_audit_workbook(
    *,
    target: Path,
    summary: pd.DataFrame,
    details: pd.DataFrame,
    bridges: pd.DataFrame,
    issues: pd.DataFrame,
    reconciliation: dict,
    run_info: dict,
    decision_trace: pd.DataFrame,
    journal_snapshot: Path,
    journal_sheet: str,
    journal_header_row: int,
    account_labels: dict[str, str] | None = None,
) -> dict:
    """流式生成18张表审计底稿并做生成后结构校验。"""
    target = Path(target)
    divisor = int(run_info["display_divisor_minor"])
    completion_status_label = run_info.get(
        "completion_status_label",
        "正式核对完成"
        if reconciliation["status"] == "pass"
        else "仅诊断、待总对账",
    )
    item_summary = summary.loc[summary["item_no"].astype(int).gt(0)].copy()
    item_summary = item_summary.sort_values("item_no")
    if item_summary["item_no"].astype(int).tolist() != list(range(1, 17)):
        raise ValueError("正式输出必须包含连续的第1至16项")

    annotation_index = build_item_trace_index(decision_trace)
    relevant_rows_by_item: dict[int, set[int]] = {}
    if decision_trace is not None and not decision_trace.empty:
        for item_no, group in decision_trace.groupby("item_no"):
            relevant_rows_by_item[int(item_no)] = {
                int(value)
                for value in group["source_row"]
                # 排除非序时账来源（余额桥接片段 source_row<=0）
                if not pd.isna(value) and int(value) > 0
            }

    journal_line_count = 0
    probe = openpyxl.load_workbook(
        journal_snapshot, read_only=True, data_only=False
    )
    try:
        probe_sheet = probe[journal_sheet]
        journal_header = [
            cell.value
            for cell in probe_sheet[journal_header_row]
        ]
        journal_records = [
            record
            for record in probe_sheet.iter_rows(
                min_row=journal_header_row + 1, values_only=True
            )
        ]
        journal_line_count = len(journal_records)
    finally:
        probe.close()
    required_rows = _TOP_ROWS_PER_ITEM + journal_line_count + 20
    if required_rows > EXCEL_MAX_ROWS:
        raise ValueError(
            f"输入序时账{journal_line_count}行加顶部底稿区域超出单表行数上限"
            f"（{EXCEL_MAX_ROWS}行），请拆分序时账后重试"
        )

    item_sheets = [
        _sheet_name(int(row["item_no"]), str(row["item_name"]))
        for _, row in item_summary.iterrows()
    ]
    if len(set(item_sheets)) != 16:
        raise WorkbookStructureError("项目工作表名称重复或项目编号不连续")

    workbook = xlsxwriter.Workbook(str(target), {"constant_memory": True})
    fmt = _formats(workbook)
    result_sheet = workbook.add_worksheet(_RESULT_SHEET)
    full_sheet = workbook.add_worksheet(_FULL_JOURNAL_SHEET)
    full_header_row, _full_data_rows = _write_full_journal_sheet(
        full_sheet,
        fmt,
        journal_header,
        journal_records,
        journal_header_row,
        relevant_rows_by_item,
    )
    full_sheet.freeze_panes(full_header_row + 1, 3)
    full_sheet.set_column(
        0, len(journal_header) + 1 - 1, 16
    )

    # ---- 附表结果表 ----
    result_headers = [
        "项目", "金额",
        "金额是怎么来的", "涉及笔数", "AI判断金额",
        "需人工复核金额", "异常说明数量", "核对结果",
    ]
    for col, header in enumerate(result_headers):
        result_sheet.write(0, col, header, fmt["header"])
    net_profit = summary.loc[summary["item_no"].astype(int).eq(0)].iloc[0]
    result_sheet.write(1, 0, "净利润", fmt["body"])
    result_sheet.write_number(
        1, 1, int(net_profit["amount_minor"]) / divisor, fmt["money"]
    )
    result_sheet.write(1, 2, "客户确认的报表净利润", fmt["body"])
    result_sheet.write_number(1, 3, int(net_profit["detail_count"]), fmt["body"])
    result_sheet.write_number(1, 4, 0, fmt["money"])
    result_sheet.write_number(1, 5, 0, fmt["money"])
    result_sheet.write_number(1, 6, int(net_profit["issue_count"]), fmt["body"])
    result_sheet.write(
        1, 7, _RESULT_CHECK_STATUS_TEXT.get(
            str(net_profit["check_status"]), str(net_profit["check_status"])
        ), fmt["body"]
    )
    row = 2
    for _, summary_row in item_summary.iterrows():
        item_no = int(summary_row["item_no"])
        method = _result_method(
            item_no,
            int(summary_row["amount_minor"]),
            int(summary_row["ai_amount_minor"]),
        )
        sheet = item_sheets[item_no - 1]
        result_sheet.write_url(
            row,
            0,
            f"internal:'{sheet}'!A1",
            fmt["link"],
            str(summary_row["item_name"]),
        )
        result_sheet.write_number(
            row, 1, int(summary_row["amount_minor"]) / divisor, fmt["money"]
        )
        result_sheet.write(row, 2, method, fmt["body"])
        result_sheet.write_number(
            row, 3, int(summary_row["detail_count"]), fmt["body"]
        )
        result_sheet.write_number(
            row, 4, int(summary_row["ai_amount_minor"]) / divisor, fmt["money"]
        )
        result_sheet.write_number(
            row, 5, int(summary_row["review_amount_minor"]) / divisor,
            fmt["money"],
        )
        result_sheet.write_number(
            row, 6, int(summary_row["issue_count"]), fmt["body"]
        )
        result_sheet.write(
            row,
            7,
            _RESULT_CHECK_STATUS_TEXT.get(
                str(summary_row["check_status"]),
                str(summary_row["check_status"]),
            ),
            fmt["body"],
        )
        row += 1
    result_sheet.write(row, 0, "16项调节合计", fmt["bold"])
    result_sheet.write_number(
        row, 1, int(reconciliation["adjustments_minor"]) / divisor, fmt["money"]
    )
    row += 1
    result_sheet.write(row, 0, "经营活动产生的现金流量净额", fmt["body"])
    result_sheet.write_number(
        row, 1, int(reconciliation["indirect_cfo_minor"]) / divisor, fmt["money"]
    )
    row += 1
    result_sheet.write(row, 0, "主表直接法经营活动现金流量净额", fmt["body"])
    if reconciliation["direct_cfo_minor"] is not None:
        result_sheet.write_number(
            row, 1, int(reconciliation["direct_cfo_minor"]) / divisor,
            fmt["money"],
        )
    row += 1
    result_sheet.write(row, 0, "直接法与间接法差异", fmt["body"])
    if reconciliation["difference_minor"] is not None:
        result_sheet.write_number(
            row, 1, int(reconciliation["difference_minor"]) / divisor,
            fmt["money"],
        )
    row += 1
    result_sheet.write(row, 0, "总对账状态", fmt["body"])
    result_sheet.write(
        row,
        1,
        _RECONCILIATION_STATUS_TEXT.get(
            str(reconciliation["status"]), str(reconciliation["status"])
        ),
        fmt["body"],
    )
    row += 1
    result_sheet.write(row, 0, "成果性质", fmt["body"])
    result_sheet.write(
        row, 1, completion_status_label, fmt["body"]
    )
    result_sheet.set_column(0, 7, 22)
    result_sheet.freeze_panes(1, 0)

    # ---- 16张项目底稿 ----
    display_unit = str(run_info["display_unit"])
    for item_index, (_, summary_row) in enumerate(item_summary.iterrows()):
        item_no = int(summary_row["item_no"])
        item_name = str(summary_row["item_name"])
        frozen_amount = int(summary_row["amount_minor"])
        sheet = workbook.add_worksheet(item_sheets[item_index])
        next_row = _item_top_area(
            sheet, fmt, item_no, item_name, frozen_amount, divisor,
            bridges, issues, display_unit, account_labels,
        )
        next_row = _write_item_detail_region(
            sheet, fmt, next_row, item_no, details, divisor, display_unit,
        )
        header_row, data_rows = _write_full_journal_region(
            sheet,
            fmt,
            next_row,
            journal_header,
            journal_records,
            journal_header_row,
            item_no,
            annotation_index,
            relevant_rows_by_item.get(item_no, set()),
        )
        sheet.freeze_panes(header_row + 1, 3)
        total_cols = len(journal_header) + 1
        sheet.autofilter(
            header_row,
            0,
            header_row + data_rows,
            total_cols - 1,
        )
        sheet.set_column(0, total_cols - 1, 16)
        sheet.set_landscape()
        sheet.set_paper(9)

    machine_sheet = workbook.add_worksheet(MACHINE_REFERENCE_SHEET)
    _write_machine_reference_sheet(
        machine_sheet, fmt, decision_trace, details, item_summary
    )

    workbook.close()

    expected_sheets = (
        [_RESULT_SHEET, _FULL_JOURNAL_SHEET] + item_sheets
        + [MACHINE_REFERENCE_SHEET]
    )
    validation = validate_workbook_structure(
        target=target,
        expected_sheets=expected_sheets,
        item_sheets=item_sheets,
        journal_line_count=journal_line_count,
        journal_header_row=journal_header_row,
    )
    return {
        "sheets": expected_sheets,
        "journal_row_count": journal_line_count,
        "journal_column_count": len(journal_header) + 1,
        "link_count": 32,
        "formula_error_count": validation["formula_error_count"],
        "path": str(target),
    }


def validate_workbook_structure(
    *,
    target: Path,
    expected_sheets: list[str],
    item_sheets: list[str],
    journal_line_count: int,
    journal_header_row: int,
) -> dict:
    """直接扫描 xlsx 内部 XML 校验结构、链接和公式，不实例化单元格。"""
    import re as _re
    import zipfile as _zipfile

    valid_forward = {f"'{sheet}'!A1" for sheet in item_sheets}
    back_target = f"'{_RESULT_SHEET}'!A1"
    result_links = 0
    back_links = 0
    formula_error_count = 0
    try:
        archive = _zipfile.ZipFile(target)
    except (OSError, ValueError) as exc:
        raise WorkbookStructureError(f"输出工作簿无法重新打开：{exc}") from exc
    try:
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names:
            raise WorkbookStructureError("输出工作簿缺少 xl/workbook.xml")
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        sheet_names = _re.findall(
            r"<sheet[^>]*name=\"([^\"]*)\"[^>]*/>",
            workbook_xml,
        )
        if sheet_names != expected_sheets:
            raise WorkbookStructureError(
                f"工作表顺序与期望不一致：{sheet_names}"
            )
        for sheet_name in expected_sheets:
            hidden_pattern = (
                '<sheet[^>]*name="' + _re.escape(sheet_name) + '"[^>]*state="hidden"'
            )
            if _re.search(hidden_pattern, workbook_xml):
                raise WorkbookStructureError(f"工作表不可见：{sheet_name}")
        # 每张表 XML 扫描超链接与公式错误
        for index in range(1, len(expected_sheets) + 1):
            sheet_path = f"xl/worksheets/sheet{index}.xml"
            if sheet_path not in names:
                raise WorkbookStructureError(f"缺少工作表文件：{sheet_path}")
            sheet_xml = archive.read(sheet_path).decode("utf-8")
            for location in _re.findall(
                r'<hyperlink[^>]*location="([^"]*)"',
                sheet_xml,
            ):
                if index == 1:
                    result_links += 1
                    if location not in valid_forward:
                        raise WorkbookStructureError(
                            f"结果表链接目标无效：{location}"
                        )
                elif sheet_names[index - 1] in item_sheets:
                    back_links += 1
                    if location != back_target:
                        raise WorkbookStructureError(
                            f"{sheet_names[index - 1]}返回链接目标无效："
                            f"{location}"
                        )
            formula_error_count += (
                sheet_xml.count("#REF!")
                + sheet_xml.count("#VALUE!")
            )
        if result_links != 16:
            raise WorkbookStructureError(
                f"结果表进入链接数量应为16，实际{result_links}"
            )
        if back_links != 16:
            raise WorkbookStructureError(
                f"项目底稿返回链接数量应为16，实际{back_links}"
            )
        return {"formula_error_count": formula_error_count}
    finally:
        archive.close()


# ---------------------------------------------------------------------------
# 急速模式：2 sheet xlsx + 16 个同名 MD 底稿
# ---------------------------------------------------------------------------


def _rapid_formats(workbook) -> dict:
    return {
        "title": workbook.add_format(
            {"bold": True, "font_size": 14, "font_name": "等线"}
        ),
        "header": workbook.add_format(
            {
                "bold": True,
                "bg_color": "#D9E1F2",
                "border": 1,
                "align": "center",
                "valign": "vcenter",
                "text_wrap": True,
                "font_name": "等线",
                "font_size": 10,
            }
        ),
        "body": workbook.add_format(
            {
                "border": 1,
                "text_wrap": True,
                "valign": "top",
                "font_name": "等线",
                "font_size": 10,
            }
        ),
        "money": workbook.add_format(
            {
                "border": 1,
                "num_format": _MONEY_FORMAT,
                "align": "right",
                "valign": "top",
                "font_name": "等线",
                "font_size": 10,
            }
        ),
        "bold": workbook.add_format(
            {
                "border": 1,
                "bold": True,
                "text_wrap": True,
                "valign": "top",
                "font_name": "等线",
                "font_size": 10,
            }
        ),
    }


def write_rapid_workbook(
    *,
    target: Path,
    summary: pd.DataFrame,
    details: pd.DataFrame,
    bridges: pd.DataFrame,
    issues: pd.DataFrame,
    reconciliation: dict,
    run_info: dict,
    decision_trace: pd.DataFrame,
    journal_snapshot: Path,
    journal_sheet: str,
    journal_header_row: int,
    account_labels: dict[str, str] | None = None,
) -> dict:
    """急速模式 xlsx：输出结果表、简报和机器用复核数据三张表。"""
    target = Path(target)
    divisor = int(run_info["display_divisor_minor"])
    completion_status_label = run_info.get(
        "completion_status_label",
        "正式核对完成"
        if reconciliation["status"] == "pass"
        else "仅诊断、待总对账",
    )
    item_summary = summary.loc[summary["item_no"].astype(int).gt(0)].copy()
    item_summary = item_summary.sort_values("item_no")
    if item_summary["item_no"].astype(int).tolist() != list(range(1, 17)):
        raise ValueError("正式输出必须包含连续的第1至16项")

    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlsxwriter.Workbook(str(target), {"constant_memory": True})
    fmt = _rapid_formats(workbook)
    result_sheet = workbook.add_worksheet(_RESULT_SHEET)

    result_headers = [
        "项目", "金额", "金额是怎么来的", "涉及笔数", "AI判断金额",
        "需人工复核金额", "异常说明数量", "核对结果",
    ]
    for col, header in enumerate(result_headers):
        result_sheet.write(0, col, header, fmt["header"])
    net_profit = summary.loc[summary["item_no"].astype(int).eq(0)].iloc[0]
    result_sheet.write(1, 0, "净利润", fmt["body"])
    result_sheet.write_number(
        1, 1, int(net_profit["amount_minor"]) / divisor, fmt["money"]
    )
    result_sheet.write(1, 2, "客户确认的报表净利润", fmt["body"])
    result_sheet.write_number(1, 3, int(net_profit["detail_count"]), fmt["body"])
    result_sheet.write_number(1, 4, 0, fmt["money"])
    result_sheet.write_number(1, 5, 0, fmt["money"])
    result_sheet.write_number(1, 6, int(net_profit["issue_count"]), fmt["body"])
    result_sheet.write(
        1, 7, _RESULT_CHECK_STATUS_TEXT.get(
            str(net_profit["check_status"]), str(net_profit["check_status"])
        ), fmt["body"]
    )
    row = 2
    for _, summary_row in item_summary.iterrows():
        item_no = int(summary_row["item_no"])
        method = _result_method(
            item_no,
            int(summary_row["amount_minor"]),
            int(summary_row["ai_amount_minor"]),
        )
        result_sheet.write(row, 0, str(summary_row["item_name"]), fmt["body"])
        result_sheet.write_number(
            row, 1, int(summary_row["amount_minor"]) / divisor, fmt["money"]
        )
        result_sheet.write(row, 2, method, fmt["body"])
        result_sheet.write_number(
            row, 3, int(summary_row["detail_count"]), fmt["body"]
        )
        result_sheet.write_number(
            row, 4, int(summary_row["ai_amount_minor"]) / divisor, fmt["money"]
        )
        result_sheet.write_number(
            row, 5, int(summary_row["review_amount_minor"]) / divisor,
            fmt["money"],
        )
        result_sheet.write_number(
            row, 6, int(summary_row["issue_count"]), fmt["body"]
        )
        result_sheet.write(
            row,
            7,
            _RESULT_CHECK_STATUS_TEXT.get(
                str(summary_row["check_status"]),
                str(summary_row["check_status"]),
            ),
            fmt["body"],
        )
        row += 1
    result_sheet.write(row, 0, "16项调节合计", fmt["bold"])
    result_sheet.write_number(
        row, 1, int(reconciliation["adjustments_minor"]) / divisor, fmt["money"]
    )
    row += 1
    result_sheet.write(row, 0, "经营活动产生的现金流量净额", fmt["body"])
    result_sheet.write_number(
        row, 1, int(reconciliation["indirect_cfo_minor"]) / divisor, fmt["money"]
    )
    row += 1
    result_sheet.write(row, 0, "主表直接法经营活动现金流量净额", fmt["body"])
    if reconciliation["direct_cfo_minor"] is not None:
        result_sheet.write_number(
            row, 1, int(reconciliation["direct_cfo_minor"]) / divisor,
            fmt["money"],
        )
    row += 1
    result_sheet.write(row, 0, "直接法与间接法差异", fmt["body"])
    if reconciliation["difference_minor"] is not None:
        result_sheet.write_number(
            row, 1, int(reconciliation["difference_minor"]) / divisor,
            fmt["money"],
        )
    row += 1
    result_sheet.write(row, 0, "总对账状态", fmt["body"])
    result_sheet.write(
        row,
        1,
        _RECONCILIATION_STATUS_TEXT.get(
            str(reconciliation["status"]), str(reconciliation["status"])
        ),
        fmt["body"],
    )
    row += 1
    result_sheet.write(row, 0, "成果性质", fmt["body"])
    result_sheet.write(
        row, 1, completion_status_label, fmt["body"]
    )
    result_sheet.set_column(0, 7, 22)
    result_sheet.freeze_panes(1, 0)

    brief_sheet = workbook.add_worksheet("简报")
    brief_rows = [
        ["项目", "值"],
        ["主体", run_info["entity_name"]],
        ["期间开始", run_info["period_start"]],
        ["期间结束", run_info["period_end"]],
        ["运行标识", run_info.get("run_id", "")],
        ["规则版本", run_info.get("rule_version", "")],
        ["代码版本", run_info.get("code_version", "")],
        ["展示单位", run_info["display_unit"]],
        ["净利润", int(net_profit["amount_minor"]) / divisor],
        ["16项调节合计", int(reconciliation["adjustments_minor"]) / divisor],
        ["间接法经营活动现金流量净额", int(reconciliation["indirect_cfo_minor"]) / divisor],
        ["直接法与间接法差异", None if reconciliation["difference_minor"] is None else int(reconciliation["difference_minor"]) / divisor],
        ["总对账状态", _RECONCILIATION_STATUS_TEXT.get(str(reconciliation["status"]), str(reconciliation["status"]))],
        ["成果性质", completion_status_label],
        ["输出模式", "急速模式：3个工作表xlsx＋16个同名MD底稿"],
        ["MD底稿说明", "16个项目底稿以同名Markdown文件输出，见输出目录下的“底稿MD”文件夹"],
    ]
    for index, (label, value) in enumerate(brief_rows, start=0):
        brief_sheet.write(index, 0, label, fmt["bold"] if index == 0 else fmt["body"])
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            brief_sheet.write_number(index, 1, value, fmt["money"])
        else:
            brief_sheet.write(index, 1, value, fmt["body"])
    brief_sheet.set_column(0, 1, 40)

    machine_sheet = workbook.add_worksheet(MACHINE_REFERENCE_SHEET)
    _write_machine_reference_sheet(
        machine_sheet, fmt, decision_trace, details, item_summary
    )

    workbook.close()
    return {"path": str(target), "sheets": [_RESULT_SHEET, "简报", MACHINE_REFERENCE_SHEET]}


def _md_escape(text: str) -> str:
    return (
        str(text)
        .replace("|", "\\|")
        .replace("\n", "<br>")
        .replace("\r", "")
    )


def write_item_markdowns(
    *,
    output_dir: Path,
    summary: pd.DataFrame,
    details: pd.DataFrame,
    bridges: pd.DataFrame,
    issues: pd.DataFrame,
    decision_trace: pd.DataFrame,
    journal_snapshot: Path,
    journal_sheet: str,
    journal_header_row: int,
    run_info: dict,
    account_labels: dict[str, str] | None = None,
) -> dict:
    """急速模式：为16个项目各写一个同名Markdown审计底稿。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    divisor = int(run_info["display_divisor_minor"])
    item_summary = summary.loc[summary["item_no"].astype(int).gt(0)].copy()
    item_summary = item_summary.sort_values("item_no")

    annotation_index = build_item_trace_index(decision_trace)
    relevant_rows_by_item: dict[int, set[int]] = {}
    if decision_trace is not None and not decision_trace.empty:
        for item_no, group in decision_trace.groupby("item_no"):
            relevant_rows_by_item[int(item_no)] = {
                int(value)
                for value in group["source_row"]
                # 排除非序时账来源（余额桥接片段 source_row<=0）
                if not pd.isna(value) and int(value) > 0
            }
    journal_records = []
    probe = openpyxl.load_workbook(
        journal_snapshot, read_only=True, data_only=False
    )
    try:
        probe_sheet = probe[journal_sheet]
        journal_header = [cell.value for cell in probe_sheet[journal_header_row]]
        journal_records = [
            record
            for record in probe_sheet.iter_rows(
                min_row=journal_header_row + 1, values_only=True
            )
        ]
    finally:
        probe.close()
    header_names = [str(h or "") for h in journal_header]

    md_paths = []
    for _, summary_row in item_summary.iterrows():
        item_no = int(summary_row["item_no"])
        item_name = str(summary_row["item_name"])
        frozen = int(summary_row["amount_minor"])
        filename = _sheet_name(item_no, item_name) + ".md"
        path = output_dir / filename

        lines = [f"# {_sheet_name(item_no, item_name)} 审计底稿", ""]
        lines.append(f"- 本项目最终金额：{frozen / divisor:.2f} {run_info['display_unit']}")
        lines.append(f"- 涉及笔数：{int(summary_row['detail_count'])}")
        lines.append(f"- 金额是怎么来的：{_result_method(item_no, int(summary_row['amount_minor']), int(summary_row['ai_amount_minor']))}")
        lines.append(f"- 核对结果：{_RESULT_CHECK_STATUS_TEXT.get(str(summary_row['check_status']), str(summary_row['check_status']))}")
        lines.append("")

        item_bridges = _frame_items(bridges, item_no)
        lines.append("## 从科目余额表怎么算出本项目")
        if item_bridges.empty:
            lines.append("本项目不用科目余额表计算")
        else:
            lines.append("| 取数来源 | 期初余额 | 期末余额 | 需要剔除的金额 | 计入本项目金额 | 一句话说明 |")
            lines.append("|---|---|---|---|---|---|")
            for _, bridge in item_bridges.iterrows():
                lines.append(
                    "| {} | {:.2f} | {:.2f} | {:.2f} | {:.2f} | {} |".format(
                        _md_escape(_bridge_source_text(bridge, account_labels)),
                        float(bridge.get("opening_minor", 0) or 0) / divisor,
                        float(bridge.get("closing_minor", 0) or 0) / divisor,
                        float(bridge.get("corrections_minor", 0) or 0) / divisor,
                        float(bridge.get("final_amount_minor", 0) or 0) / divisor,
                        _md_escape(_bridge_reason_text(item_no, bridge)),
                    )
                )
        lines.append("")

        item_issues = _frame_items(issues, item_no)
        lines.append("## 需要留意的异常情况")
        if item_issues.empty:
            lines.append("没有发现异常")
        else:
            lines.append("| 异常类型 | 金额 | 事实描述 | AI建议 | 最终怎么处理 |")
            lines.append("|---|---|---|---|---|")
            for _, issue in item_issues.iterrows():
                final = issue.get("final_decision", "") or ""
                lines.append(
                    "| {} | {:.2f} | {} | {} | {} |".format(
                        _md_escape(issue.get("issue_code", "") or ""),
                        float(issue.get("amount_minor", 0) or 0) / divisor,
                        _md_escape(issue.get("reason", "") or ""),
                        _md_escape(issue.get("recommendation", "") or ""),
                        _md_escape(_FINAL_DECISION_TEXT.get(str(final), str(final))),
                    )
                )
        lines.append("")

        item_details = _frame_items(details, item_no)
        lines.append("## 逐笔计算明细")
        if item_details.empty:
            lines.append("没有逐笔明细")
        else:
            lines.append("| 凭证日期 | 凭证号 | 摘要 | 原始金额 | 计入本项目金额 | 处理结果 | 取数口径 | 为什么这样处理 |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for _, detail in item_details.iterrows():
                decision = detail.get("decision", "") or ""
                rule_id = detail.get("rule_id", "") or ""
                lines.append(
                    "| {} | {} | {} | {:.2f} | {:.2f} | {} | {} | {} |".format(
                        _md_escape(detail.get("voucher_date", "") or ""),
                        _md_escape(detail.get("voucher_no", "") or ""),
                        _md_escape(detail.get("summary", "") or ""),
                        float(detail.get("source_amount_minor", 0) or 0) / divisor,
                        float(detail.get("adjustment_amount_minor", 0) or 0) / divisor,
                        _md_escape(_FINAL_DECISION_TEXT.get(str(decision), str(decision))),
                        _md_escape(_RULE_DISPLAY_TEXT.get(str(rule_id), "按已确认规则处理")),
                        _md_escape(_detail_reason(item_no, detail)),
                    )
                )
        lines.append("")

        lines.append("## 本项目相关的凭证行")
        lines.append("| 原始行号 | 凭证日期 | 凭证号 | 摘要 | 科目名称 | 借方发生额 | 贷方发生额 | 本行和本项目的关系 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        hit_count = 0
        relevant = relevant_rows_by_item.get(item_no, set())
        for index, record in enumerate(journal_records):
            source_row = journal_header_row + index + 1
            if source_row not in relevant:
                continue
            annotation = annotation_for_row(item_no, source_row, annotation_index)
            hit_count += 1
            record_map = {
                str(header or ""): value
                for header, value in zip(journal_header, record)
            }
            voucher_date = (
                record_map.get("记账时间", "")
                or record_map.get("会计月", "")
                or ""
            )
            summary = record_map.get("业务说明", "") or record_map.get("摘要", "")
            lines.append(
                "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                    source_row,
                    _md_escape(voucher_date or ""),
                    _md_escape(record_map.get("凭证编号", "") or ""),
                    _md_escape(summary or ""),
                    _md_escape(record_map.get("科目名称", "") or ""),
                    _md_escape(record_map.get("借方发生额", "") or ""),
                    _md_escape(record_map.get("贷方发生额", "") or ""),
                    _md_escape(_row_relation_text(annotation)),
                )
            )
        lines.append("")
        unmatched = len(journal_records) - hit_count
        lines.append("## 其余凭证行说明")
        lines.append(
            f"序时账共 {len(journal_records)} 行，本项目涉及 {hit_count} 行，"
            f"其余 {unmatched} 行不在此展开。"
        )
        lines.append("")

        text = "\n".join(lines)
        raw = text.encode("utf-8")
        if not raw.startswith(b"\xef\xbb\xbf"):
            raw = b"\xef\xbb\xbf" + raw
        path.write_bytes(raw)
        md_paths.append(str(path))

    return {"markdown_paths": md_paths, "output_dir": str(output_dir)}
