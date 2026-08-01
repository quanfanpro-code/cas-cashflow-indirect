"""中央金额占用和防重复处理。"""

import pandas as pd

from .contracts import RuleBook


_OCCUPANCY_COLUMNS = [
    "fragment_id",
    "item_no",
    "adjustment_amount_minor",
    "claimed_amount_minor",
    "physical_consumed_minor",
    "remaining_amount_minor",
    "occupancy_type",
    "decision",
    "rule_id",
    "paired_offset_id",
]

_ISSUE_COLUMNS = [
    "fragment_id",
    "issue_code",
    "reason",
    "remaining_amount_minor",
    "next_action",
]


def _issue(
    fragment_id: str,
    issue_code: str,
    reason: str,
    remaining_amount_minor: int,
    next_action: str,
) -> dict:
    return {
        "fragment_id": fragment_id,
        "issue_code": issue_code,
        "reason": reason,
        "remaining_amount_minor": remaining_amount_minor,
        "next_action": next_action,
    }


def resolve_claims(
    fragments: pd.DataFrame,
    claims: pd.DataFrame,
    rule_book: RuleBook,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    fragment_required = {"fragment_id", "amount_minor", "processable"}
    claim_required = {
        "fragment_id",
        "item_no",
        "claim_amount_minor",
        "decision",
        "rule_id",
        "paired_offset_id",
    }
    missing_fragments = sorted(fragment_required - set(fragments.columns))
    missing_claims = sorted(claim_required - set(claims.columns))
    if missing_fragments:
        raise ValueError(f"金额片段缺少字段：{missing_fragments}")
    if missing_claims:
        raise ValueError(f"占用申请缺少字段：{missing_claims}")
    if fragments["fragment_id"].duplicated().any():
        raise ValueError("金额片段标识重复")

    included = claims.loc[
        claims["decision"].isin(
            {
                "included",
                "ai_classified",
                "paired_offset",
                "review_required",
            }
        )
    ].copy()
    exclusions = claims.loc[claims["decision"].eq("excluded")].copy()
    known_fragments = set(fragments["fragment_id"].astype(str))
    items = {item.item_no for item in rule_book.items}
    exclusive_groups = [set(group) for group in rule_book.exclusive_groups]
    paired_offsets = {tuple(sorted(pair)) for pair in rule_book.paired_offsets}
    committed_rows: list[dict] = []
    issue_rows: list[dict] = []

    unknown_claims = included.loc[
        ~included["fragment_id"].astype(str).isin(known_fragments)
    ]
    unknown_exclusions = exclusions.loc[
        ~exclusions["fragment_id"].astype(str).isin(known_fragments)
    ]
    for fragment_id in sorted(set(unknown_claims["fragment_id"].astype(str))):
        issue_rows.append(
            _issue(
                fragment_id,
                "UNKNOWN_FRAGMENT",
                "占用申请引用了未知金额片段",
                0,
                "fix_claim",
            )
        )
    included = included.loc[
        included["fragment_id"].astype(str).isin(known_fragments)
    ]
    for fragment_id in sorted(set(unknown_exclusions["fragment_id"].astype(str))):
        issue_rows.append(
            _issue(
                fragment_id,
                "UNKNOWN_FRAGMENT",
                "排除结论引用了未知金额片段",
                0,
                "fix_claim",
            )
        )
    exclusions = exclusions.loc[
        exclusions["fragment_id"].astype(str).isin(known_fragments)
    ]

    for _, fragment in fragments.sort_values("fragment_id").iterrows():
        fragment_id = str(fragment["fragment_id"])
        available = int(fragment["amount_minor"])
        processable = bool(fragment["processable"])
        fragment_claims = included.loc[
            included["fragment_id"].astype(str).eq(fragment_id)
        ].copy()
        fragment_exclusions = exclusions.loc[
            exclusions["fragment_id"].astype(str).eq(fragment_id)
        ].copy()
        fragment_claims = fragment_claims.sort_values(
            ["item_no", "rule_id"],
            kind="stable",
        )
        if not processable or available == 0:
            if not fragment_claims.empty or not fragment_exclusions.empty:
                issue_rows.append(
                    _issue(
                        fragment_id,
                        "NONPROCESSABLE_FRAGMENT_CLAIM",
                        "零金额或不可处理片段不得提交占用",
                        available,
                        "fix_claim",
                    )
                )
            continue
        if not fragment_exclusions.empty:
            valid_exclusion = (
                len(fragment_exclusions) == 1
                and fragment_claims.empty
                and int(fragment_exclusions.iloc[0]["item_no"]) in items
                and int(fragment_exclusions.iloc[0]["claim_amount_minor"]) == 0
            )
            if valid_exclusion:
                continue
            issue_rows.append(
                _issue(
                    fragment_id,
                    "INVALID_EXCLUSION",
                    "同一金额片段的排除结论必须唯一、金额为零且不能同时申请占用",
                    available,
                    "fix_claim",
                )
            )
            continue
        if fragment_claims.empty:
            issue_rows.append(
                _issue(
                    fragment_id,
                    "UNCLASSIFIED_REMAINDER",
                    "金额片段尚未形成最终占用",
                    available,
                    "ai_request",
                )
            )
            continue

        claim_items = [int(value) for value in fragment_claims["item_no"]]
        invalid_items = sorted(set(claim_items) - items)
        if invalid_items:
            issue_rows.append(
                _issue(
                    fragment_id,
                    "INVALID_ITEM",
                    f"项目编号超出1至16：{invalid_items}",
                    available,
                    "fix_claim",
                )
            )
            continue
        distinct_items = set(claim_items)
        if any(len(distinct_items & group) > 1 for group in exclusive_groups):
            issue_rows.append(
                _issue(
                    fragment_id,
                    "EXCLUSIVE_CLAIM_CONFLICT",
                    f"互斥项目同时申请同一片段：{sorted(distinct_items)}",
                    available,
                    "fix_classification",
                )
            )
            continue

        pair_ids = {
            str(value)
            for value in fragment_claims["paired_offset_id"]
            if not pd.isna(value) and str(value).strip()
        }
        if pair_ids:
            magnitudes = [
                abs(int(value))
                for value in fragment_claims["claim_amount_minor"]
            ]
            valid_pair = (
                len(fragment_claims) == 2
                and len(distinct_items) == 2
                and len(pair_ids) == 1
                and tuple(sorted(distinct_items)) in paired_offsets
                and len(set(magnitudes)) == 1
                and magnitudes[0] > 0
                and magnitudes[0] <= available
            )
            if not valid_pair:
                issue_rows.append(
                    _issue(
                        fragment_id,
                        "INVALID_PAIRED_OFFSET",
                        "配套抵销的项目、标识或金额不符合规则",
                        available,
                        "fix_claim",
                    )
                )
                continue
            consumed = magnitudes[0]
            remaining = available - consumed
            for index, (_, claim) in enumerate(fragment_claims.iterrows()):
                committed_rows.append(
                    {
                        "fragment_id": fragment_id,
                        "item_no": int(claim["item_no"]),
                        "adjustment_amount_minor": int(
                            claim["claim_amount_minor"]
                        ),
                        "claimed_amount_minor": consumed,
                        "physical_consumed_minor": consumed if index == 0 else 0,
                        "remaining_amount_minor": remaining,
                        "occupancy_type": "paired_offset",
                        "decision": claim["decision"],
                        "rule_id": claim["rule_id"],
                        "paired_offset_id": next(iter(pair_ids)),
                    }
                )
            if remaining:
                issue_rows.append(
                    _issue(
                        fragment_id,
                        "UNCLASSIFIED_REMAINDER",
                        "配套抵销后仍有未分类金额",
                        remaining,
                        "ai_request",
                    )
                )
            continue

        if len(fragment_claims) > 1:
            issue_code = (
                "DUPLICATE_CLAIM"
                if len(distinct_items) == 1
                else "MULTIPLE_ITEM_CLAIM"
            )
            issue_rows.append(
                _issue(
                    fragment_id,
                    issue_code,
                    "未切分的金额片段只能归入一个普通项目",
                    available,
                    "split_or_fix_claim",
                )
            )
            continue

        claim = fragment_claims.iloc[0]
        claim_amount = int(claim["claim_amount_minor"])
        consumed = abs(claim_amount)
        if consumed == 0:
            issue_rows.append(
                _issue(
                    fragment_id,
                    "ZERO_CLAIM",
                    "零金额占用申请没有业务意义",
                    available,
                    "fix_claim",
                )
            )
            continue
        if consumed > available:
            issue_rows.append(
                _issue(
                    fragment_id,
                    "CLAIM_EXCEEDS_AVAILABLE",
                    f"申请{consumed}超过可用金额{available}",
                    available,
                    "fix_claim",
                )
            )
            continue
        remaining = available - consumed
        committed_rows.append(
            {
                "fragment_id": fragment_id,
                "item_no": int(claim["item_no"]),
                "adjustment_amount_minor": claim_amount,
                "claimed_amount_minor": consumed,
                "physical_consumed_minor": consumed,
                "remaining_amount_minor": remaining,
                "occupancy_type": "exclusive",
                "decision": claim["decision"],
                "rule_id": claim["rule_id"],
                "paired_offset_id": None,
            }
        )
        if remaining:
            issue_rows.append(
                _issue(
                    fragment_id,
                    "UNCLASSIFIED_REMAINDER",
                    "部分占用后仍有未分类金额",
                    remaining,
                    "ai_request",
                )
            )

    committed = pd.DataFrame(committed_rows, columns=_OCCUPANCY_COLUMNS)
    issues = pd.DataFrame(issue_rows, columns=_ISSUE_COLUMNS)
    return committed, issues
