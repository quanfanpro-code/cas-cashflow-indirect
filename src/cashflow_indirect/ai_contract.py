"""AI请求包生成和结论包校验。"""

from collections import Counter
import hashlib
import json
import re

import pandas as pd


class AiDecisionError(ValueError):
    pass


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _stable_request_id(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_ai_requests(
    fragments: pd.DataFrame,
    candidates: pd.DataFrame,
    evidence: pd.DataFrame,
    performance_materiality_minor: int,
) -> list[dict]:
    fragment_required = {
        "fragment_id",
        "business_event_id",
        "amount_minor",
        "direction_sign",
    }
    candidate_required = {"fragment_id", "item_no", "rule_id"}
    evidence_required = {
        "fragment_id",
        "evidence_id",
        "evidence_type",
        "content",
        "source_refs",
    }
    for name, frame, required in (
        ("金额片段", fragments, fragment_required),
        ("候选项目", candidates, candidate_required),
        ("判断证据", evidence, evidence_required),
    ):
        missing = sorted(required - set(frame.columns))
        if missing:
            raise AiDecisionError(f"{name}缺少字段：{missing}")

    fragment_index = fragments.set_index("fragment_id", drop=False)
    unknown = sorted(set(candidates["fragment_id"]) - set(fragment_index.index))
    if unknown:
        raise AiDecisionError(f"候选项目引用了未知金额片段：{unknown}")
    if evidence["evidence_id"].duplicated().any():
        raise AiDecisionError("判断证据标识重复")

    context_fields = (
        "voucher_id",
        "voucher_date",
        "summary",
        "account_path",
        "counterparty_level1",
        "direction_sign",
    )
    requests: list[dict] = []
    for fragment_id, candidate_rows in candidates.groupby(
        "fragment_id",
        sort=True,
    ):
        fragment = fragment_index.loc[fragment_id]
        if isinstance(fragment, pd.DataFrame):
            raise AiDecisionError(f"金额片段标识重复：{fragment_id}")
        candidate_rows = candidate_rows.sort_values(["item_no", "rule_id"])
        evidence_rows = evidence.loc[evidence["fragment_id"].eq(fragment_id)]
        evidence_records = evidence_rows[
            ["evidence_id", "evidence_type", "content", "source_refs"]
        ].to_dict("records")
        request = {
            "fragment_id": str(fragment_id),
            "business_event_id": str(fragment["business_event_id"]),
            "candidate_items": sorted(
                {int(item_no) for item_no in candidate_rows["item_no"]}
            ),
            "candidate_rules": candidate_rows[
                ["item_no", "rule_id"]
            ].to_dict("records"),
            "available_amount_minor": int(fragment["amount_minor"]),
            "evidence_ids": [
                str(evidence_id) for evidence_id in evidence_rows["evidence_id"]
            ],
            "evidence": evidence_records,
            "performance_materiality_minor": int(
                performance_materiality_minor
            ),
            "context": {
                field: fragment[field]
                for field in context_fields
                if field in fragments.columns and not pd.isna(fragment[field])
            },
        }
        request = _json_safe(request)
        request["request_id"] = _stable_request_id(request)
        requests.append(request)
    return requests


def _duplicate_ids(records: list[dict], field: str) -> set[str]:
    counts = Counter(str(record.get(field, "")) for record in records)
    return {value for value, count in counts.items() if count > 1}


def _require_text(decision: dict, field: str, label: str) -> str:
    value = decision.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AiDecisionError(f"AI结论必须填写{label}")
    return value.strip()


def validate_ai_decisions(
    requests: list[dict],
    decisions: list[dict],
) -> pd.DataFrame:
    duplicate_requests = _duplicate_ids(requests, "request_id")
    if duplicate_requests:
        raise AiDecisionError(f"存在重复请求：{sorted(duplicate_requests)}")
    duplicate_decisions = _duplicate_ids(decisions, "request_id")
    if duplicate_decisions:
        raise AiDecisionError(
            f"存在重复AI结论：{sorted(duplicate_decisions)}"
        )

    request_by_id = {
        str(request.get("request_id", "")): request for request in requests
    }
    decision_by_id = {
        str(decision.get("request_id", "")): decision for decision in decisions
    }
    if not all(request_by_id):
        raise AiDecisionError("AI请求缺少请求标识")
    unknown = sorted(set(decision_by_id) - set(request_by_id))
    missing = sorted(set(request_by_id) - set(decision_by_id))
    if unknown:
        raise AiDecisionError(f"AI结论引用了未知请求：{unknown}")
    if missing:
        raise AiDecisionError(f"AI请求缺少结论：{missing}")

    rows: list[dict] = []
    for request_id, request in request_by_id.items():
        decision = dict(decision_by_id[request_id])
        candidate_items = {int(item) for item in request["candidate_items"]}
        try:
            recommended_item = int(decision["recommended_item"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AiDecisionError("AI结论缺少合法的首选项目") from exc
        if recommended_item not in candidate_items:
            raise AiDecisionError(
                f"首选项目不在候选项目内：{recommended_item}"
            )

        status = decision.get("recommended_status")
        if status not in {"included", "excluded"}:
            raise AiDecisionError(f"AI结论状态不合法：{status}")
        amount = decision.get("recommended_amount_minor")
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise AiDecisionError("AI建议金额必须是最小货币单位整数")
        available = int(request["available_amount_minor"])
        if abs(amount) > available:
            raise AiDecisionError(
                f"AI建议金额超过可用金额：{amount}，可用{available}"
            )

        decision["recommendation"] = _require_text(
            decision, "recommendation", "处理建议"
        )
        decision["reason"] = _require_text(decision, "reason", "建议理由")
        supporting = decision.get("supporting_evidence_ids")
        if not isinstance(supporting, list) or not supporting:
            raise AiDecisionError("AI结论必须引用支持证据")
        unknown_evidence = sorted(
            set(map(str, supporting)) - set(map(str, request["evidence_ids"]))
        )
        if unknown_evidence:
            raise AiDecisionError(f"AI结论引用了未知证据：{unknown_evidence}")
        contrary = decision.get("contrary_evidence")
        if not isinstance(contrary, list):
            raise AiDecisionError("相反证据必须是列表")

        alternatives = decision.get("rejected_alternatives")
        if not isinstance(alternatives, list):
            raise AiDecisionError("必须说明未采用的备选分类")
        alternative_items = {
            int(alternative.get("item_no"))
            for alternative in alternatives
            if isinstance(alternative, dict)
            and alternative.get("reason")
            and str(alternative["reason"]).strip()
        }
        expected_alternatives = candidate_items - {recommended_item}
        if alternative_items != expected_alternatives:
            raise AiDecisionError(
                f"备选分类理由不完整，期望{sorted(expected_alternatives)}"
            )

        applicable_rules = decision.get("applicable_rule_ids")
        selected_rules = {
            str(rule["rule_id"])
            for rule in request.get("candidate_rules", [])
            if int(rule["item_no"]) == recommended_item
        }
        if (
            not isinstance(applicable_rules, list)
            or not set(map(str, applicable_rules)) & selected_rules
        ):
            raise AiDecisionError("AI结论必须引用首选项目的适用规则")
        if isinstance(decision.get("cfo_impact_minor"), bool) or not isinstance(
            decision.get("cfo_impact_minor"), int
        ):
            raise AiDecisionError("AI结论必须给出经营活动现金流量影响")

        confidence = decision.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise AiDecisionError(f"置信度必须在0至1：{confidence}")
        strong_conflict = decision.get("strong_conflict")
        if not isinstance(strong_conflict, bool):
            raise AiDecisionError("强冲突判断必须是布尔值")
        if strong_conflict and not contrary:
            raise AiDecisionError("强冲突必须列明相反证据")

        materiality = int(request["performance_materiality_minor"])
        expected_review = abs(amount) >= materiality and strong_conflict
        if decision.get("human_review_required") is not expected_review:
            raise AiDecisionError(
                "人工升级只能在金额达到重要性且存在强冲突时触发"
            )

        for field, label in (
            ("model_id", "模型标识"),
            ("prompt_template_version", "提示词模板版本"),
        ):
            decision[field] = _require_text(decision, field, label)
        for field in ("prompt_hash", "response_hash"):
            value = decision.get(field)
            if not isinstance(value, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}",
                value,
            ):
                raise AiDecisionError(f"{field}必须是SHA-256")

        decision.update(
            {
                "fragment_id": request["fragment_id"],
                "business_event_id": request.get("business_event_id", ""),
                "candidate_items": sorted(candidate_items),
                "available_amount_minor": available,
                "performance_materiality_minor": materiality,
                "human_review_required": expected_review,
            }
        )
        rows.append(decision)
    return pd.DataFrame(rows)
