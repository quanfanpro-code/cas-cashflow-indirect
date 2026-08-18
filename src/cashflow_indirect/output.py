"""正式机器结果和Excel结果输出（标准19张表底稿或急速3表加MD）。"""

import json
from pathlib import Path
import shutil

import pandas as pd
from xlsxwriter.exceptions import XlsxWriterException

from .storage import sha256_file, validate_event_chain
from .workpaper import (
    write_audit_workbook,
    write_item_markdowns,
    write_rapid_workbook,
)


class OutputExportError(RuntimeError):
    pass


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


def publish_audit_evidence(output_dir: Path, events_path: Path) -> dict:
    """把最终事件链及查询索引加入正式输出清单。"""
    output_dir = Path(output_dir)
    events_path = Path(events_path)
    validate_event_chain(events_path)
    target_events = output_dir / "events.jsonl"
    shutil.copy2(events_path, target_events)
    event_rows = []
    for line in target_events.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        payload = event.get("payload", {})
        event_rows.append(
            {
                "sequence_no": int(event["sequence_no"]),
                "event_hash": str(event["event_hash"]),
                "stage": str(event.get("stage", "")),
                "event_type": str(event.get("event_type", "")),
                "fragment_id": str(payload.get("fragment_id", "")),
                "business_event_id": str(payload.get("business_event_id", "")),
                "item_no": payload.get("item_no"),
                "source_row": payload.get("source_row"),
                "active": True,
            }
        )
    event_index = pd.DataFrame(
        event_rows,
        columns=[
            "sequence_no",
            "event_hash",
            "stage",
            "event_type",
            "fragment_id",
            "business_event_id",
            "item_no",
            "source_row",
            "active",
        ],
    )
    index_path = output_dir / "event_index.parquet"
    event_index.to_parquet(index_path, index=False)

    manifest_path = output_dir / "output_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    retained = [
        entry
        for entry in manifest.get("files", [])
        if entry.get("path") not in {"events.jsonl", "event_index.parquet"}
    ]
    retained.extend(
        [
            _manifest_entry(output_dir, target_events, len(event_rows)),
            _manifest_entry(output_dir, index_path, len(event_index)),
        ]
    )
    manifest["files"] = retained
    _write_json(manifest_path, manifest)
    return manifest


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
    account_labels: dict[str, str] | None = None,
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
                account_labels=account_labels,
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
                account_labels=account_labels,
            )
    except (
        OSError,
        XlsxWriterException,
        OutputExportError,
        ValueError,
    ) as exc:
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
    workbook_sheet_count = 3 if output_mode == "rapid" else 19
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
