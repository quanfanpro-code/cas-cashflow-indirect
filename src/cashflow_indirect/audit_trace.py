"""判断轨迹：把规则、人工智能、金额占用和证据合并为稳定判断轨迹。"""

import hashlib

import pandas as pd


TRACE_COLUMNS = (
    "trace_id",
    "fragment_id",
    "business_event_id",
    "source_row",
    "item_no",
    "candidate_status",
    "final_decision",
    "rule_id",
    "decision_reason",
    "confidence",
    "recommendation",
    "recommendation_reason",
    "supporting_evidence",
    "contrary_evidence",
    "rejected_alternatives",
    "human_review_required",
    "claim_amount_minor",
    "physical_consumed_minor",
    "remaining_amount_minor",
    "occupancy_type",
    "paired_offset_id",
    "recommended_status",
    "recommended_amount_minor",
    "cfo_impact_minor",
    "model_id",
    "prompt_template_version",
    "prompt_hash",
    "response_hash",
)


def _stable_trace_id(*parts: object) -> str:
    text = "\x1f".join("" if pd.isna(part) else str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _frame_records(frame: pd.DataFrame) -> list[dict]:
    return [] if frame is None or frame.empty else frame.to_dict("records")


def _evidence_by_id(evidence: pd.DataFrame) -> dict[str, dict]:
    return {
        str(row["evidence_id"]): row for row in _frame_records(evidence)
    }


def _join_texts(values: list[str]) -> str:
    return "；".join(
        value for value in values if isinstance(value, str) and value.strip()
    )


def _row_value(row: dict, field: str, default: object = "") -> object:
    value = row.get(field, default)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    return value


def build_decision_trace(
    rule_claims: pd.DataFrame,
    ai_candidates: pd.DataFrame,
    ai_requests: list[dict],
    ai_decisions: pd.DataFrame,
    committed: pd.DataFrame,
    fragments: pd.DataFrame,
    evidence: pd.DataFrame,
    family_bridges: pd.DataFrame,
) -> pd.DataFrame:
    """把规则申请、AI结论、金额占用和证据合并为稳定判断轨迹。"""
    fragments_by_id = {
        str(row["fragment_id"]): row for row in _frame_records(fragments)
    }
    committed_by_key = {
        (str(row["fragment_id"]), int(row["item_no"])): row
        for row in _frame_records(committed)
    }
    evidence_by_id = _evidence_by_id(evidence)
    requests_by_id = {
        str(request.get("request_id", "")): request
        for request in (ai_requests or [])
    }
    rows: list[dict] = []

    def occupancy_fields(
        fragment_id: str, item_no: int
    ) -> dict:
        occupied = committed_by_key.get((fragment_id, item_no))
        return {
            "claim_amount_minor": (
                int(occupied.get("claimed_amount_minor", 0))
                if occupied is not None
                else 0
            ),
            "physical_consumed_minor": (
                int(occupied.get("physical_consumed_minor", 0))
                if occupied is not None
                else 0
            ),
            "remaining_amount_minor": (
                int(occupied.get("remaining_amount_minor", 0))
                if occupied is not None
                else 0
            ),
            "occupancy_type": (
                str(occupied.get("occupancy_type", ""))
                if occupied is not None
                else ""
            ),
            "paired_offset_id": (
                occupied.get("paired_offset_id")
                if occupied is not None
                else None
            ),
        }

    def common_fields(
        fragment_id: str, item_no: int
    ) -> dict:
        fragment = fragments_by_id.get(fragment_id, {})
        return {
            "fragment_id": fragment_id,
            "business_event_id": str(
                _row_value(fragment, "business_event_id", "")
            ),
            "source_row": int(_row_value(fragment, "source_row", 0) or 0),
            "item_no": item_no,
            **occupancy_fields(fragment_id, item_no),
        }

    for claim in _frame_records(rule_claims):
        fragment_id = str(claim["fragment_id"])
        item_no = int(claim["item_no"])
        occupied = committed_by_key.get((fragment_id, item_no))
        rows.append(
            {
                **common_fields(fragment_id, item_no),
                "trace_id": _stable_trace_id(
                    fragment_id, item_no, claim.get("rule_id", ""), "rule"
                ),
                "candidate_status": "rule_classified",
                "final_decision": (
                    str(occupied.get("decision", claim.get("decision", "")))
                    if occupied is not None
                    else str(claim.get("decision", ""))
                ),
                "rule_id": str(claim.get("rule_id", "")),
                "decision_reason": str(claim.get("reason", "")),
                "confidence": (
                    float(claim["confidence"])
                    if claim.get("confidence") is not None
                    else 0.0
                ),
                "recommendation": "",
                "recommendation_reason": "",
                "supporting_evidence": "",
                "contrary_evidence": "未发现相反证据",
                "rejected_alternatives": "",
                "human_review_required": False,
                "recommended_status": "",
                "recommended_amount_minor": 0,
                "cfo_impact_minor": 0,
                "model_id": "",
                "prompt_template_version": "",
                "prompt_hash": "",
                "response_hash": "",
            }
        )

    for decision in _frame_records(ai_decisions):
        fragment_id = str(decision.get("fragment_id", ""))
        request = requests_by_id.get(str(decision.get("request_id", "")), {})
        if not request:
            request = {"candidate_items": decision.get("candidate_items", [])}
        candidate_items = [
            int(value) for value in request.get("candidate_items", [])
        ]
        recommended = int(decision.get("recommended_item", 0))
        supporting_ids = decision.get("supporting_evidence_ids", []) or []
        supporting_texts = []
        for evidence_id in supporting_ids:
            evidence_row = evidence_by_id.get(str(evidence_id))
            if evidence_row is not None:
                supporting_texts.append(
                    str(_row_value(evidence_row, "content", ""))
                )
        supporting_evidence = _join_texts(supporting_texts)
        contrary = decision.get("contrary_evidence", []) or []
        contrary_evidence = (
            _join_texts(contrary) if contrary else "未发现相反证据"
        )
        alternatives = decision.get("rejected_alternatives", []) or []
        alternative_texts = [
            f"第{int(alt['item_no'])}项：{alt['reason']}"
            for alt in alternatives
            if isinstance(alt, dict)
            and alt.get("item_no") is not None
            and alt.get("reason")
        ]
        rejected_alternatives = _join_texts(alternative_texts)
        recommendation = str(_row_value(decision, "recommendation", ""))
        recommendation_reason = str(_row_value(decision, "reason", ""))
        confidence = float(_row_value(decision, "confidence", 0.0) or 0.0)
        human_review = bool(_row_value(decision, "human_review_required", False))
        recommended_status = str(
            _row_value(decision, "recommended_status", "included")
        )

        for item_no in sorted(candidate_items):
            occupied = committed_by_key.get((fragment_id, item_no))
            if item_no == recommended:
                if recommended_status == "excluded":
                    final_decision = "excluded"
                else:
                    occupied_decision = (
                        str(occupied.get("decision", "ai_classified"))
                        if occupied is not None
                        else "ai_classified"
                    )
                    # 内部占用状态映射为业务最终结论：AI 已分类落数视为纳入
                    final_decision = (
                        "included"
                        if occupied_decision == "ai_classified"
                        else occupied_decision
                    )
                decision_reason = recommendation_reason
            else:
                final_decision = "reclassified"
                decision_reason = f"候选后改归第{recommended}项"
            rows.append(
                {
                    **common_fields(fragment_id, item_no),
                    "trace_id": _stable_trace_id(
                        fragment_id, item_no, "ai", decision.get("request_id", "")
                    ),
                    "candidate_status": "ai_candidate",
                    "final_decision": final_decision,
                    "rule_id": "",
                    "decision_reason": decision_reason,
                    "confidence": confidence,
                    "recommendation": recommendation,
                    "recommendation_reason": recommendation_reason,
                    "supporting_evidence": supporting_evidence,
                    "contrary_evidence": contrary_evidence,
                    "rejected_alternatives": rejected_alternatives,
                    "human_review_required": human_review,
                    "recommended_status": recommended_status,
                    "recommended_amount_minor": int(
                        _row_value(decision, "recommended_amount_minor", 0) or 0
                    ),
                    "cfo_impact_minor": int(
                        _row_value(decision, "cfo_impact_minor", 0) or 0
                    ),
                    "model_id": str(_row_value(decision, "model_id", "")),
                    "prompt_template_version": str(
                        _row_value(decision, "prompt_template_version", "")
                    ),
                    "prompt_hash": str(_row_value(decision, "prompt_hash", "")),
                    "response_hash": str(
                        _row_value(decision, "response_hash", "")
                    ),
                }
            )
    return pd.DataFrame(rows, columns=list(TRACE_COLUMNS))


def validate_decision_trace(trace: pd.DataFrame) -> None:
    """校验判断轨迹完整性，发现错误时给出具体中文错误。"""
    if trace.empty:
        return
    missing = sorted(set(TRACE_COLUMNS) - set(trace.columns))
    if missing:
        raise ValueError(f"判断轨迹缺少字段：{missing}")
    empty_fragments = trace["fragment_id"].fillna("").astype(str).eq("")
    if empty_fragments.any():
        raise ValueError("判断轨迹引用了未知金额片段：片段标识为空")
    duplicate = trace["trace_id"].duplicated()
    if duplicate.any():
        raise ValueError(
            "判断轨迹主键重复：" + "、".join(
                sorted(trace.loc[duplicate, "trace_id"].astype(str).unique())
            )
        )
    included = trace["final_decision"].astype(str).eq("included")
    no_occupancy = (
        trace["occupancy_type"].fillna("").astype(str).eq("")
        | trace["physical_consumed_minor"].fillna(0).eq(0)
    )
    if (included & no_occupancy).any():
        raise ValueError(
            "已纳入金额无法关联最终占用："
            + "、".join(
                sorted(
                    trace.loc[included & no_occupancy, "trace_id"]
                    .astype(str)
                    .unique()
                )
            )
        )
    candidate = trace["candidate_status"].astype(str).eq("ai_candidate")
    no_explanation = (
        trace["final_decision"].fillna("").astype(str).eq("")
        | trace["decision_reason"].fillna("").astype(str).eq("")
    )
    if (candidate & no_explanation).any():
        raise ValueError(
            "候选项目缺少最终纳入或改归说明："
            + "、".join(
                sorted(
                    trace.loc[candidate & no_explanation, "trace_id"]
                    .astype(str)
                    .unique()
                )
            )
        )
    ai_involved = trace["recommendation_reason"].fillna("").astype(str).ne("")
    no_evidence = trace["supporting_evidence"].fillna("").astype(str).eq("")
    if (ai_involved & no_evidence).any():
        raise ValueError(
            "人工智能证据无法解析："
            + "、".join(
                sorted(
                    trace.loc[ai_involved & no_evidence, "trace_id"]
                    .astype(str)
                    .unique()
                )
            )
        )
