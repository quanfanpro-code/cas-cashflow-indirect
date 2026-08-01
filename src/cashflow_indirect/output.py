"""正式机器结果和Excel结果输出（标准18张表底稿或急速2表加MD）。"""

import json
import os
from pathlib import Path
import subprocess
from typing import Any

import pandas as pd

from .storage import sha256_file
from .workpaper import (
    write_audit_workbook,
    write_item_markdowns,
    write_rapid_workbook,
)


class OutputExportError(RuntimeError):
    pass


_DETAIL_HEADERS = {
    "item_no": "项目序号",
    "item_name": "项目名称",
    "business_event_id": "业务事件标识",
    "fragment_id": "金额片段标识",
    "voucher_date": "凭证日期",
    "voucher_no": "凭证号",
    "source_row": "原始行",
    "account_name": "科目",
    "counterparty_level1": "对方一级科目",
    "summary": "摘要",
    "source_amount_minor": "原始金额",
    "adjustment_amount_minor": "纳入金额",
    "decision": "处理结论",
    "rule_id": "规则编号",
    "reason": "纳入或排除理由",
}

_BRIDGE_HEADERS = {
    "item_no": "项目序号",
    "record_type": "记录类型",
    "source_kind": "来源类型",
    "account_code": "科目或辅助编码",
    "opening_minor": "期初余额",
    "closing_minor": "期末余额",
    "base_change_minor": "正常变化",
    "nonoperating_correction_minor": "非经营性修正",
    "corrections_minor": "非现金修正",
    "final_amount_minor": "最终调节额",
    "reason": "桥接说明",
}

_ISSUE_HEADERS = {
    "issue_code": "异常类型",
    "amount_minor": "金额",
    "item_no": "影响项目",
    "reason": "事实描述",
    "recommendation": "AI建议",
    "recommendation_reason": "建议理由",
    "supporting_evidence": "支持证据",
    "contrary_evidence": "相反证据",
    "alternatives": "备选处理及金额影响",
    "review_required": "是否达到人工升级门槛",
    "final_decision": "最终采用处理",
}


def _json_default(value: object) -> object:
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"无法序列化的输出值：{type(value).__name__}")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        + "\n",
        encoding="utf-8-sig",
    )


def _write_jsonl(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            default=_json_default,
        )
        for record in frame.to_dict("records")
    ]
    path.write_text(
        ("\n".join(lines) + "\n") if lines else "",
        encoding="utf-8-sig",
    )


def _frame_rows(
    frame: pd.DataFrame,
    header_map: dict[str, str],
    divisor: int,
) -> list[list[Any]]:
    columns = [column for column in header_map if column in frame.columns]
    if not columns:
        return [["说明"], ["本次运行无记录"]]
    rows: list[list[Any]] = [[header_map[column] for column in columns]]
    for record in frame[columns].to_dict("records"):
        row: list[Any] = []
        for column in columns:
            value = record[column]
            if pd.isna(value):
                row.append(None)
            elif column.endswith("_minor"):
                row.append(int(value) / divisor)
            elif hasattr(value, "item"):
                row.append(value.item())
            else:
                row.append(value)
        rows.append(row)
    return rows


def _workbook_payload(
    summary: pd.DataFrame,
    details: pd.DataFrame,
    bridges: pd.DataFrame,
    issues: pd.DataFrame,
    reconciliation: dict,
    run_store: object,
    run_info: dict,
) -> dict:
    divisor = int(run_info["display_divisor_minor"])
    completion_status_label = run_info.get(
        "completion_status_label",
        "正式核对完成"
        if reconciliation["status"] == "pass"
        else "仅诊断、待总对账",
    )
    result_rows = [
        [
            "项目名称",
            f"调节金额（{run_info['display_unit']}）",
            "取数方式",
            "明细笔数",
            "AI判断金额",
            "人工升级金额",
            "异常数量",
            "核对状态",
        ]
    ]
    net_profit = summary.loc[summary["item_no"].astype(int).eq(0)].iloc[0]
    result_rows.append(
        [
            "净利润",
            int(net_profit["amount_minor"]) / divisor,
            "输入",
            int(net_profit["detail_count"]),
            0,
            0,
            int(net_profit["issue_count"]),
            str(net_profit["check_status"]),
        ]
    )
    for _, row in summary.loc[summary["item_no"].astype(int).gt(0)].iterrows():
        method = (
            "AI判断"
            if int(row["ai_amount_minor"]) != 0
            else "规则或余额桥接"
        )
        result_rows.append(
            [
                str(row["item_name"]),
                int(row["amount_minor"]) / divisor,
                method,
                int(row["detail_count"]),
                int(row["ai_amount_minor"]) / divisor,
                int(row["review_amount_minor"]) / divisor,
                int(row["issue_count"]),
                str(row["check_status"]),
            ]
        )
    result_rows.extend(
        [
            [
                "16项调节合计",
                int(reconciliation["adjustments_minor"]) / divisor,
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            [
                "经营活动产生的现金流量净额",
                int(reconciliation["indirect_cfo_minor"]) / divisor,
                "间接法",
                "",
                "",
                "",
                "",
                reconciliation["status"],
            ],
            [
                "主表直接法经营活动现金流量净额",
                None
                if reconciliation["direct_cfo_minor"] is None
                else int(reconciliation["direct_cfo_minor"]) / divisor,
                "直接法",
                "",
                "",
                "",
                "",
                "",
            ],
            [
                "直接法与间接法差异",
                None
                if reconciliation["difference_minor"] is None
                else int(reconciliation["difference_minor"]) / divisor,
                "只报告，不倒轧",
                "",
                "",
                "",
                "",
                reconciliation["status"],
            ],
            [
                "总对账状态",
                reconciliation["status"],
                "",
                "",
                "",
                "",
                "",
                "",
            ],
        ]
    )

    check_rows = [
        [
            "核对编号",
            "核对事项",
            f"实际或差异（{run_info['display_unit']}）",
            "预期",
            "容差",
            "状态",
            "说明",
        ]
    ]
    for check in reconciliation["checks"]:
        check_rows.append(
            [
                check["check_id"],
                check["name"],
                None
                if check["difference_minor"] is None
                else check["difference_minor"] / divisor,
                check["expected_minor"],
                check["tolerance_minor"],
                check["status"],
                check["notes"],
            ]
        )

    run_rows = [["项目", "值"]]
    run_values = {
        "主体": run_info["entity_name"],
        "期间开始": run_info["period_start"],
        "期间结束": run_info["period_end"],
        "运行标识": run_store.run_id,
        "输入指纹": run_store.input_fingerprint,
        "规则版本": run_store.rule_version,
        "代码版本": run_store.code_version,
        "协议版本": run_info["protocol_version"],
        "本位币": run_info["functional_currency"],
        "展示单位": run_info["display_unit"],
        "实际执行重要性水平": run_info["performance_materiality_minor"]
        / divisor,
        "运行状态": run_info["run_status"],
        "成果性质": completion_status_label,
        "正式输出": "Excel、JSON、Parquet、JSONL及输出清单",
    }
    run_rows.extend([[key, value] for key, value in run_values.items()])

    return {
        "sheets": [
            {"name": "附表结果", "rows": result_rows},
            {
                "name": "调节明细",
                "rows": _frame_rows(details, _DETAIL_HEADERS, divisor),
            },
            {
                "name": "余额桥接",
                "rows": _frame_rows(bridges, _BRIDGE_HEADERS, divisor),
            },
            {"name": "核对结果", "rows": check_rows},
            {
                "name": "异常与建议",
                "rows": _frame_rows(issues, _ISSUE_HEADERS, divisor),
            },
            {"name": "运行信息", "rows": run_rows},
        ]
    }


def _artifact_runtime() -> tuple[Path, Path]:
    node = Path(
        os.environ.get(
            "CASHFLOW_NODE_PATH",
            Path.home()
            / ".cache"
            / "codex-runtimes"
            / "codex-primary-runtime"
            / "dependencies"
            / "node"
            / "bin"
            / "node.exe",
        )
    )
    node_modules = Path(
        os.environ.get(
            "CASHFLOW_NODE_MODULES",
            Path.home()
            / ".cache"
            / "codex-runtimes"
            / "codex-primary-runtime"
            / "dependencies"
            / "node"
            / "node_modules",
        )
    )
    if not node.is_file() or not node_modules.is_dir():
        raise OutputExportError(
            "Excel输出依赖不可用；请配置CASHFLOW_NODE_PATH和"
            "CASHFLOW_NODE_MODULES后单独重试输出阶段"
        )
    return node, node_modules


def _write_workbook(
    payload: dict,
    target: Path,
    run_store: object,
) -> dict:
    node, node_modules = _artifact_runtime()
    audit_dir = Path(run_store.root) / "07_audit"
    runtime_dir = audit_dir / "artifact_runtime"
    preview_dir = audit_dir / "output_previews"
    payload_path = audit_dir / "workbook_payload.json"
    _write_json(payload_path, payload)
    builder = Path(__file__).with_name("xlsx_builder.mjs")
    try:
        completed = subprocess.run(
            [
                str(node),
                str(builder),
                str(payload_path),
                str(target),
                str(node_modules),
                str(runtime_dir),
                str(preview_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OutputExportError(f"Excel工作簿生成失败：{exc}") from exc
    try:
        result_line = next(
            line
            for line in reversed(completed.stdout.splitlines())
            if line.strip()
        )
        return json.loads(result_line)
    except (StopIteration, json.JSONDecodeError) as exc:
        raise OutputExportError("Excel工作簿校验结果无法读取") from exc


def _manifest_entry(
    output_dir: Path,
    path: Path,
    record_count: int,
) -> dict:
    return {
        "path": path.relative_to(output_dir).as_posix(),
        "size_bytes": path.stat().st_size,
        "record_count": record_count,
        "sha256": sha256_file(path),
    }


def export_results(
    *,
    run_store: object,
    summary: pd.DataFrame,
    details: pd.DataFrame,
    occupancy: pd.DataFrame,
    bridges: pd.DataFrame,
    issues: pd.DataFrame,
    reconciliation: dict,
    run_info: dict,
    output_dir: Path,
    decision_trace: pd.DataFrame,
    journal_snapshot: Path,
    journal_sheet: str,
    journal_header_row: int,
    output_mode: str = "standard",
) -> dict:
    """先冻结机器结果，再生成Excel；Excel失败时机器结果保持可重试。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "output_status.json"
    _write_json(status_path, {"status": "writing", "run_id": run_store.run_id})

    item_summary = summary.loc[summary["item_no"].astype(int).gt(0)].copy()
    if item_summary["item_no"].astype(int).tolist() != list(range(1, 17)):
        raise ValueError("正式输出必须包含连续的第1至16项")
    completion_status = run_info.get(
        "completion_status",
        "formal_completed"
        if reconciliation["status"] == "pass"
        else "diagnostic_only",
    )
    completion_status_label = run_info.get(
        "completion_status_label",
        "正式核对完成"
        if reconciliation["status"] == "pass"
        else "仅诊断、待总对账",
    )
    result_summary = {
        "run_id": run_store.run_id,
        "entity_name": run_info["entity_name"],
        "period_start": run_info["period_start"],
        "period_end": run_info["period_end"],
        "functional_currency": run_info["functional_currency"],
        "display_unit": run_info["display_unit"],
        "net_profit_minor": int(reconciliation["net_profit_minor"]),
        "adjustments_minor": int(reconciliation["adjustments_minor"]),
        "indirect_cfo_minor": int(reconciliation["indirect_cfo_minor"]),
        "direct_cfo_minor": reconciliation["direct_cfo_minor"],
        "difference_minor": reconciliation["difference_minor"],
        "status": reconciliation["status"],
        "file_status": run_info.get("file_status", run_info["run_status"]),
        "completion_status": completion_status,
        "completion_status_label": completion_status_label,
        "items": item_summary[
            ["item_no", "item_name", "amount_minor"]
        ].to_dict("records"),
        "rule_version": run_store.rule_version,
        "code_version": run_store.code_version,
        "protocol_version": run_info["protocol_version"],
    }

    paths = {
        "result_summary": output_dir / "result_summary.json",
        "adjustment_summary": output_dir / "adjustment_summary.parquet",
        "adjustment_details": output_dir / "adjustment_details.parquet",
        "amount_occupancy": output_dir / "amount_occupancy.parquet",
        "reconciliation": output_dir / "reconciliation.json",
        "issues": output_dir / "issues.jsonl",
        "decision_trace": output_dir / "decision_trace.parquet",
        "workbook": output_dir / "现金流量表附表计算结果.xlsx",
    }
    _write_json(paths["result_summary"], result_summary)
    item_summary.to_parquet(paths["adjustment_summary"], index=False)
    details.to_parquet(paths["adjustment_details"], index=False)
    occupancy.to_parquet(paths["amount_occupancy"], index=False)
    decision_trace.to_parquet(paths["decision_trace"], index=False)
    _write_json(paths["reconciliation"], reconciliation)
    _write_jsonl(paths["issues"], issues)

    if output_mode not in {"standard", "rapid"}:
        raise ValueError(f"不支持的输出模式：{output_mode}")
    md_paths: list[Path] = []
    try:
        if output_mode == "rapid":
            workbook_validation = write_rapid_workbook(
                target=paths["workbook"],
                summary=summary,
                details=details,
                bridges=bridges,
                issues=issues,
                reconciliation=reconciliation,
                run_info=run_info,
                decision_trace=decision_trace,
                journal_snapshot=journal_snapshot,
                journal_sheet=journal_sheet,
                journal_header_row=journal_header_row,
            )
            md_result = write_item_markdowns(
                output_dir=output_dir / "底稿MD",
                summary=summary,
                details=details,
                bridges=bridges,
                issues=issues,
                decision_trace=decision_trace,
                journal_snapshot=journal_snapshot,
                journal_sheet=journal_sheet,
                journal_header_row=journal_header_row,
                run_info=run_info,
            )
            md_paths = [Path(path) for path in md_result["markdown_paths"]]
        else:
            workbook_validation = write_audit_workbook(
                target=paths["workbook"],
                summary=summary,
                details=details,
                bridges=bridges,
                issues=issues,
                reconciliation=reconciliation,
                run_info=run_info,
                decision_trace=decision_trace,
                journal_snapshot=journal_snapshot,
                journal_sheet=journal_sheet,
                journal_header_row=journal_header_row,
            )
    except (PermissionError, OutputExportError, ValueError) as exc:
        _write_json(
            status_path,
            {
                "status": "failed",
                "stage": "excel",
                "run_id": run_store.run_id,
                "error": str(exc),
                "retryable": True,
            },
        )
        raise OutputExportError(f"Excel输出失败，机器结果已保留：{exc}") from exc

    _write_json(
        status_path,
        {
            "status": "completed",
            "run_id": run_store.run_id,
            "workbook_validation": workbook_validation,
        },
    )
    workbook_sheet_count = 2 if output_mode == "rapid" else 18
    record_counts = {
        paths["result_summary"]: 1,
        paths["adjustment_summary"]: len(item_summary),
        paths["adjustment_details"]: len(details),
        paths["amount_occupancy"]: len(occupancy),
        paths["reconciliation"]: len(reconciliation["checks"]),
        paths["issues"]: len(issues),
        paths["decision_trace"]: len(decision_trace),
        paths["workbook"]: workbook_sheet_count,
        status_path: 1,
    }
    manifest_paths = [
        paths["workbook"],
        paths["result_summary"],
        paths["adjustment_summary"],
        paths["adjustment_details"],
        paths["amount_occupancy"],
        paths["reconciliation"],
        paths["issues"],
        paths["decision_trace"],
        status_path,
    ] + md_paths
    files = [
        _manifest_entry(output_dir, path, int(record_counts[path]))
        if path in record_counts
        else _manifest_entry(output_dir, path, 1)
        for path in manifest_paths
    ]
    manifest = {
        "status": "completed",
        "run_id": run_store.run_id,
        "files": files,
    }
    _write_json(output_dir / "output_manifest.json", manifest)
    return manifest
