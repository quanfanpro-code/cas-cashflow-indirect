"""项目索引与逐行注释测试。"""

from importlib import import_module

import pandas as pd
import pytest

from cashflow_indirect.audit_trace import TRACE_COLUMNS

workpaper = import_module("cashflow_indirect.workpaper")


def test_annotation_keeps_unmatched_journal_row():
    index = workpaper.build_item_trace_index(
        pd.DataFrame(columns=list(TRACE_COLUMNS))
    )
    annotation = workpaper.annotation_for_row(3, 88, index)
    assert annotation["项目筛选状态"] == "本项目未命中"
    assert annotation["本项目调节金额"] == 0
    assert annotation["防重复核对结论"] == "不适用"


def _trace_row(
    trace_id,
    fragment_id,
    source_row,
    item_no,
    claim_amount,
    consumed,
    remaining,
    rule_id="RULE-1",
    reason="规则判断理由",
    occupancy_type="exclusive",
):
    return {
        "trace_id": trace_id,
        "fragment_id": fragment_id,
        "business_event_id": "event-1",
        "source_row": source_row,
        "item_no": item_no,
        "candidate_status": "rule_classified",
        "final_decision": "included",
        "rule_id": rule_id,
        "decision_reason": reason,
        "confidence": 1.0,
        "recommendation": "",
        "recommendation_reason": "",
        "supporting_evidence": "",
        "contrary_evidence": "未发现相反证据",
        "rejected_alternatives": "",
        "human_review_required": False,
        "claim_amount_minor": claim_amount,
        "physical_consumed_minor": consumed,
        "remaining_amount_minor": remaining,
        "occupancy_type": occupancy_type,
        "paired_offset_id": None,
    }


def test_annotation_aggregates_multiple_fragments_of_one_row():
    trace = pd.DataFrame(
        [
            _trace_row(
                "trace-a",
                "frag-a",
                88,
                3,
                20000,
                20000,
                0,
                rule_id="NCA-DEPRECIATION",
                reason="折旧摊销计入第3项",
            ),
            _trace_row(
                "trace-b",
                "frag-b",
                88,
                3,
                5000,
                5000,
                0,
                rule_id="NCA-INTANGIBLE",
                reason="无形资产摊销计入第3项",
            ),
        ]
    )
    index = workpaper.build_item_trace_index(trace)
    annotation = workpaper.annotation_for_row(3, 88, index)

    assert annotation["项目筛选状态"] == "命中"
    assert annotation["本项目调节金额"] == 25000
    assert "frag-a" in annotation["金额片段标识"]
    assert "frag-b" in annotation["金额片段标识"]
    assert "折旧摊销计入第3项" in annotation["规则判断理由"]
    assert "无形资产摊销计入第3项" in annotation["规则判断理由"]



def test_annotation_keeps_items_isolated_for_same_row():
    trace = pd.DataFrame(
        [
            _trace_row(
                "trace-item1",
                "frag-1",
                88,
                1,
                20000,
                20000,
                0,
                rule_id="IMP-ASSET",
                reason="资产减值计入第1项",
            )
        ]
    )
    index = workpaper.build_item_trace_index(trace)

    item1 = workpaper.annotation_for_row(1, 88, index)
    item2 = workpaper.annotation_for_row(2, 88, index)

    assert item1["项目筛选状态"] == "命中"
    assert "资产减值计入第1项" in item1["规则判断理由"]
    assert item1["本项目调节金额"] == 20000
    assert item2["项目筛选状态"] == "本项目未命中"
    assert item2["本项目调节金额"] == 0
    assert item2["金额片段标识"] == ""
    assert "资产减值计入第1项" not in item2["规则判断理由"]


import openpyxl
from types import SimpleNamespace

from cashflow_indirect.reconciliation import calculate_summary, reconcile_all


def _write_journal_snapshot(path: Path) -> None:
    """生成带空标题列和扩展辅助列的序时账快照，共3条数据。"""
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "序时账"
    header = [
        "",
        "记账时间",
        "会计月",
        "凭证种类",
        "凭证编号",
        "编号",
        "业务说明",
        "科目编号",
        "科目名称",
        "借方发生额",
        "贷方发生额",
        "对方科目",
        "匹配类型",
        "核算维度",
        "部门",
    ]
    worksheet.append(header)
    data = [
        [
            None, "2026-12-31", "12月", "记", "001", "1",
            "计提资产减值准备", "671101", "资产减值损失",
            20000, 0, "存货跌价准备", "算法生成", "存货", "财务部",
        ],
        [
            None, "2026-12-31", "12月", "记", "001", "2",
            "结转损益", "410401", "本年利润",
            0, 20000, "资产减值损失", "结转损益规则", "损益", "财务部",
        ],
        [
            None, "2026-12-31", "12月", "记", "002", "3",
            "支付办公费", "660201", "办公费",
            5000, 0, "银行存款", "算法生成", "费用", "行政部",
        ],
    ]
    for row in data:
        worksheet.append(row)
    workbook.save(path)
    workbook.close()


@pytest.fixture
def workpaper_input(tmp_path, rule_book):
    journal_snapshot = tmp_path / "序时账快照.xlsx"
    _write_journal_snapshot(journal_snapshot)
    occupancy = pd.DataFrame(
        [
            {
                "fragment_id": "f-1",
                "item_no": 1,
                "adjustment_amount_minor": 20000,
                "claimed_amount_minor": 20000,
                "physical_consumed_minor": 20000,
                "remaining_amount_minor": 0,
                "decision": "included",
                "rule_id": "IMP-ASSET",
                "paired_offset_id": None,
            }
        ]
    )
    summary = calculate_summary(1_000_000, occupancy, rule_book)
    reconciliation = reconcile_all(summary, direct_cfo_minor=1_020_000)
    details = pd.DataFrame(
        [
            {
                "item_no": 1,
                "item_name": "资产减值准备",
                "business_event_id": "event-1",
                "fragment_id": "f-1",
                "voucher_date": "2026-12-31",
                "voucher_no": "记-001",
                "source_row": 2,
                "account_name": "资产减值损失",
                "counterparty_level1": "存货跌价准备",
                "summary": "计提跌价准备",
                "source_amount_minor": 20000,
                "adjustment_amount_minor": 20000,
                "decision": "included",
                "rule_id": "IMP-ASSET",
                "reason": "计入当期损益的减值",
            }
        ]
    )
    bridges = pd.DataFrame(
        [
            {
                "item_no": 13,
                "record_type": "summary",
                "opening_minor": 500000,
                "closing_minor": 450000,
                "base_change_minor": 50000,
                "corrections_minor": 0,
                "final_amount_minor": 50000,
                "reason": "存货原值余额桥接",
            }
        ]
    )
    issues = pd.DataFrame(
        [
            {
                "issue_code": "ACCOUNTING_WARNING",
                "amount_minor": 1000,
                "item_no": 3,
                "reason": "示例会计异常",
                "recommendation": "复核折旧政策",
                "recommendation_reason": "资产状态与折旧政策不一致",
                "supporting_evidence": "固定资产卡片",
                "contrary_evidence": "无",
                "alternatives": "按实际损益调整",
                "review_required": False,
                "final_decision": "保留调整并提示异常",
            }
        ]
    )
    run_store = SimpleNamespace(
        root=tmp_path,
        run_id="run-test",
        input_fingerprint="input-hash",
        rule_version=rule_book.rule_version,
        code_version="test-code",
    )
    return {
        "target": tmp_path / "审计底稿.xlsx",
        "summary": summary,
        "details": details,
        "bridges": bridges,
        "issues": issues,
        "reconciliation": reconciliation,
        "run_info": {
            "entity_name": "甲公司",
            "period_start": "2026-01-01",
            "period_end": "2026-12-31",
            "functional_currency": "CNY",
            "display_unit": "元",
            "display_divisor_minor": 100,
            "performance_materiality_minor": 50000,
            "protocol_version": "1.0",
            "run_status": "exported",
        },
        "decision_trace": pd.DataFrame(
            [
                {
                    "trace_id": "trace-1",
                    "fragment_id": "f-1",
                    "business_event_id": "event-1",
                    "source_row": 2,
                    "item_no": 1,
                    "candidate_status": "rule_classified",
                    "final_decision": "included",
                    "rule_id": "IMP-ASSET",
                    "decision_reason": "计入当期损益的减值",
                    "confidence": 1.0,
                    "recommendation": "",
                    "recommendation_reason": "",
                    "supporting_evidence": "",
                    "contrary_evidence": "未发现相反证据",
                    "rejected_alternatives": "",
                    "human_review_required": False,
                    "claim_amount_minor": 20000,
                    "physical_consumed_minor": 20000,
                    "remaining_amount_minor": 0,
                    "occupancy_type": "exclusive",
                    "paired_offset_id": None,
                }
            ],
            columns=[
                "trace_id", "fragment_id", "business_event_id", "source_row",
                "item_no", "candidate_status", "final_decision", "rule_id",
                "decision_reason", "confidence", "recommendation",
                "recommendation_reason", "supporting_evidence",
                "contrary_evidence", "rejected_alternatives",
                "human_review_required", "claim_amount_minor",
                "physical_consumed_minor", "remaining_amount_minor",
                "occupancy_type", "paired_offset_id",
            ],
        ),
        "journal_snapshot": journal_snapshot,
        "journal_sheet": "序时账",
        "journal_header_row": 1,
    }


@pytest.fixture
def expected_17_sheets(rule_book):
    names = [
        f"{item.item_no:02d}{item.name}" for item in rule_book.items
    ]
    return ["附表结果", "全量序时账"] + names


def test_workbook_has_result_and_sixteen_item_sheets(
    workpaper_input,
    expected_17_sheets,
):
    result = workpaper.write_audit_workbook(**workpaper_input)
    workbook = openpyxl.load_workbook(result["path"], read_only=False)
    assert workbook.sheetnames == expected_17_sheets



def test_workbook_has_thirty_two_bidirectional_links(
    workpaper_input,
    expected_17_sheets,
):
    result = workpaper.write_audit_workbook(**workpaper_input)
    workbook = openpyxl.load_workbook(result["path"], read_only=False)
    try:
        assert result["link_count"] == 32
        result_sheet = workbook["附表结果"]
        result_links = [
            cell
            for row in result_sheet.iter_rows(min_row=2)
            for cell in row
            if cell.hyperlink is not None
        ]
        assert len(result_links) == 16
        item_sheets = expected_17_sheets[2:]
        for link in result_links:
            assert link.hyperlink.location in {
                f"'{sheet}'!A1" for sheet in item_sheets
            }
        for sheet_name in item_sheets:
            sheet = workbook[sheet_name]
            back_links = [
                cell
                for row in sheet.iter_rows(max_row=2)
                for cell in row
                if cell.hyperlink is not None
            ]
            assert len(back_links) == 1
            assert back_links[0].hyperlink.location == "'附表结果'!A1"
    finally:
        workbook.close()



def test_every_item_sheet_keeps_only_its_relevant_journal_rows(
    workpaper_input,
    expected_17_sheets,
):
    result = workpaper.write_audit_workbook(**workpaper_input)
    workbook = openpyxl.load_workbook(result["path"], read_only=False)
    try:
        for sheet_name in expected_17_sheets[2:]:
            sheet = workbook[sheet_name]
            # 找到序时账区域标题行
            title_row = None
            for row_index, row in enumerate(sheet.iter_rows(), start=1):
                if (
                    row[0].value
                    and "相关序时账" in str(row[0].value)
                ):
                    title_row = row_index
                    break
            assert title_row is not None, f"{sheet_name}缺少序时账区域"
            header_row_index = title_row + 1
            headers = [
                cell.value
                for cell in sheet[header_row_index]
            ]
            # 空标题仍占据原列位置
            assert headers[0] is None or headers[0] == ""
            assert headers[1] == "记账时间"
            assert "核算维度" in headers
            assert "部门" in headers
            assert "项目筛选状态" in headers
            data_rows = []
            for row in sheet.iter_rows(
                min_row=header_row_index + 1,
                max_col=len(headers),
            ):
                values = [cell.value for cell in row]
                if any(value is not None and str(value) != "" for value in values):
                    data_rows.append(values)
            # 01资产减值准备有1条相关行（f-1在原始行2），其余项目无候选
            if sheet_name == "01资产减值准备":
                assert len(data_rows) == 1, f"{sheet_name}数据行数{len(data_rows)}"
                dimension_col = headers.index("核算维度") + 1
                assert any(
                    row[dimension_col - 1] == "存货" for row in data_rows
                )
                status_col = headers.index("项目筛选状态") + 1
                assert any(
                    row[status_col - 1] == "命中" for row in data_rows
                )
            else:
                assert len(data_rows) == 0, (
                    f"{sheet_name}不应出现无关行，实际{len(data_rows)}"
                )
    finally:
        workbook.close()



def test_result_amounts_formulas_and_zero_item_sheets(
    workpaper_input,
    expected_17_sheets,
):
    result = workpaper.write_audit_workbook(**workpaper_input)
    workbook = openpyxl.load_workbook(result["path"], read_only=False)
    try:
        # 结果表金额等于冻结机器结果
        summary = workpaper_input["summary"]
        amount_by_name = {
            str(row["item_name"]): int(row["amount_minor"]) / 100
            for _, row in summary.iterrows()
            if int(row["item_no"]) > 0
        }
        result_sheet = workbook["附表结果"]
        for row in result_sheet.iter_rows(min_row=2):
            name = row[0].value
            if name in amount_by_name:
                assert row[1].value == amount_by_name[name], (
                    f"{name}结果金额不一致"
                )
        # 项目顶部冻结金额立即可读、明细合计公式、差异公式
        for sheet_name in expected_17_sheets[2:]:
            sheet = workbook[sheet_name]
            assert sheet["B3"].value is not None
            assert sheet["B3"].number_format == "#,##0.00"
            # 找到明细合计公式和差异公式
            formulas = []
            for row in sheet.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and cell.value.startswith("="):
                        formulas.append((cell.coordinate, cell.value))
            total_formulas = [
                (coord, value)
                for coord, value in formulas
                if "SUM(G" in value
            ]
            assert len(total_formulas) == 1, f"{sheet_name}缺少明细合计公式"
            diff_formulas = [
                (coord, value)
                for coord, value in formulas
                if value.startswith("=B3-")
            ]
            assert len(diff_formulas) == 1, f"{sheet_name}缺少差异公式"
        # 零金额项目仍有完整工作表、完整序时账和零金额结论
        zero_sheet = workbook["16其他明确调整"]
        assert zero_sheet["B3"].value == 0
        title_rows = [
            row[0].value
            for row in zero_sheet.iter_rows()
            if row[0].value and "相关序时账" in str(row[0].value)
        ]
        assert len(title_rows) == 1
        # 打开时自动重算
        assert workbook.calculation.fullCalcOnLoad is True
    finally:
        workbook.close()



def test_workbook_rejects_journal_over_excel_row_limit(workpaper_input):
    huge = workpaper_input["journal_snapshot"].with_name("超限序时账.xlsx")
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "序时账"
    worksheet.append(["摘要", "金额"])
    for index in range(1_048_570):
        worksheet.append([f"行{index}", index])
    workbook.save(huge)
    workbook.close()
    inputs = dict(workpaper_input)
    inputs["journal_snapshot"] = huge

    with pytest.raises(ValueError, match="超出单表行数上限"):
        workpaper.write_audit_workbook(**inputs)


def test_workbook_structure_validation_rejects_invalid_links(
    workpaper_input,
    expected_17_sheets,
):
    result = workpaper.write_audit_workbook(**workpaper_input)
    workbook = openpyxl.load_workbook(result["path"], read_only=False)
    try:
        workbook["01资产减值准备"]["C2"].hyperlink.location = "'不存在的工作表'!A1"
        broken_path = workpaper_input["target"].with_name("断链.xlsx")
        workbook.save(broken_path)
    finally:
        workbook.close()
    with pytest.raises(workpaper.WorkbookStructureError, match="链接目标无效"):
        workpaper.validate_workbook_structure(
            target=broken_path,
            expected_sheets=expected_17_sheets,
            item_sheets=expected_17_sheets[2:],
            journal_line_count=3,
            journal_header_row=1,
        )
