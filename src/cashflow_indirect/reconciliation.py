"""16项汇总和直接法、间接法总对账。"""

import pandas as pd

from .contracts import RuleBook


_SUMMARY_COLUMNS = [
    "item_no",
    "item_name",
    "amount_minor",
    "detail_count",
    "rule_amount_minor",
    "ai_amount_minor",
    "review_amount_minor",
    "issue_count",
    "check_status",
]


def calculate_summary(
    net_profit_minor: int,
    occupancy: pd.DataFrame,
    rule_book: RuleBook,
) -> pd.DataFrame:
    """只汇总已经提交的正式占用，不在汇总层重跑项目规则。"""
    if not occupancy.empty:
        required = {
            "fragment_id",
            "item_no",
            "adjustment_amount_minor",
            "decision",
        }
        missing = sorted(required - set(occupancy.columns))
        if missing:
            raise ValueError(f"正式占用缺少字段：{missing}")
        invalid = sorted(
            set(occupancy["item_no"].astype(int)) - set(range(1, 17))
        )
        if invalid:
            raise ValueError(f"正式占用项目编号超出1至16：{invalid}")

    rows = [
        {
            "item_no": 0,
            "item_name": "净利润",
            "amount_minor": int(net_profit_minor),
            "detail_count": 1,
            "rule_amount_minor": 0,
            "ai_amount_minor": 0,
            "review_amount_minor": 0,
            "issue_count": 0,
            "check_status": "pass",
        }
    ]
    for item in rule_book.items:
        if occupancy.empty:
            matched = occupancy
        else:
            matched = occupancy.loc[
                occupancy["item_no"].astype(int).eq(item.item_no)
            ]
        amount = (
            int(matched["adjustment_amount_minor"].sum())
            if not matched.empty
            else 0
        )
        rule_mask = (
            matched["decision"].isin({"included", "paired_offset"})
            if not matched.empty
            else pd.Series(dtype=bool)
        )
        ai_mask = (
            matched["decision"].isin({"ai_classified", "review_required"})
            if not matched.empty
            else pd.Series(dtype=bool)
        )
        review_mask = (
            matched["decision"].eq("review_required")
            if not matched.empty
            else pd.Series(dtype=bool)
        )
        rows.append(
            {
                "item_no": item.item_no,
                "item_name": item.name,
                "amount_minor": amount,
                "detail_count": int(len(matched)),
                "rule_amount_minor": int(
                    matched.loc[rule_mask, "adjustment_amount_minor"].sum()
                )
                if not matched.empty
                else 0,
                "ai_amount_minor": int(
                    matched.loc[ai_mask, "adjustment_amount_minor"].sum()
                )
                if not matched.empty
                else 0,
                "review_amount_minor": int(
                    matched.loc[review_mask, "adjustment_amount_minor"].sum()
                )
                if not matched.empty
                else 0,
                "issue_count": 0,
                "check_status": "pass",
            }
        )
    return pd.DataFrame(rows, columns=_SUMMARY_COLUMNS)


def calculate_reconciliation_inputs(
    journal: pd.DataFrame,
    trial_balance: pd.DataFrame,
    occupancy: pd.DataFrame,
    occupancy_issues: pd.DataFrame,
    bridges: pd.DataFrame,
) -> dict[str, int]:
    """从冻结的标准数据和桥接证据计算六类项目内核对输入。"""
    required_frames = {
        "序时账": (journal, {"debit_minor", "credit_minor"}),
        "余额表": (
            trial_balance,
            {"period_debit_minor", "period_credit_minor"},
        ),
        "金额占用": (occupancy, {"item_no", "adjustment_amount_minor"}),
    }
    for name, (frame, required) in required_frames.items():
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"{name}缺少核对字段：{missing}")

    voucher_difference = int(
        journal["debit_minor"].sum() - journal["credit_minor"].sum()
    )
    journal_trial_balance_difference = int(
        abs(
            int(journal["debit_minor"].sum())
            - int(trial_balance["period_debit_minor"].sum())
        )
        + abs(
            int(journal["credit_minor"].sum())
            - int(trial_balance["period_credit_minor"].sum())
        )
    )

    occupied = (
        occupancy.loc[occupancy["item_no"].astype(int).isin({13, 14, 15})]
        .groupby("item_no")["adjustment_amount_minor"]
        .sum()
        .astype("int64")
        .to_dict()
    )
    expected = {13: 0, 14: 0, 15: 0}
    if not bridges.empty:
        family = bridges.get(
            "family",
            pd.Series("", index=bridges.index),
        ).fillna("")
        record_type = bridges.get(
            "record_type",
            pd.Series("", index=bridges.index),
        ).fillna("")
        target_item = pd.to_numeric(
            bridges.get(
                "target_item_no",
                pd.Series(index=bridges.index, dtype="float64"),
            ),
            errors="coerce",
        )
        bridge_item = pd.to_numeric(
            bridges.get(
                "item_no",
                pd.Series(index=bridges.index, dtype="float64"),
            ),
            errors="coerce",
        )

        working = bridges.loc[
            family.eq("working_capital")
            & record_type.eq("summary")
            & bridge_item.isin({13, 14, 15})
        ].copy()
        working["_item_no"] = bridge_item.loc[working.index].astype(int)
        working["_amount"] = pd.to_numeric(
            working.get(
                "final_amount_minor",
                pd.Series(0, index=working.index),
            ),
            errors="coerce",
        ).fillna(0)
        for item_no, amount in working.groupby("_item_no")["_amount"].sum().items():
            expected[int(item_no)] += int(amount)

        paired = bridges.loc[
            family.eq("noncash_assets")
            & record_type.eq("pairing")
            & target_item.isin({13, 14, 15})
        ].copy()
        paired["_item_no"] = target_item.loc[paired.index].astype(int)
        paired["_amount"] = pd.to_numeric(
            paired.get(
                "amount_minor",
                pd.Series(0, index=paired.index),
            ),
            errors="coerce",
        ).fillna(0)
        for item_no, amount in paired.groupby("_item_no")["_amount"].sum().items():
            expected[int(item_no)] -= int(amount)

        other = bridges.loc[
            family.eq("other_adjustments")
            & target_item.isin({13, 14, 15})
        ].copy()
        other["_item_no"] = target_item.loc[other.index].astype(int)
        other["_amount"] = pd.to_numeric(
            other.get(
                "adjustment_minor",
                pd.Series(0, index=other.index),
            ),
            errors="coerce",
        ).fillna(0)
        for item_no, amount in other.groupby("_item_no")["_amount"].sum().items():
            expected[int(item_no)] += int(amount)

    balance_bridge_difference = sum(
        abs(int(occupied.get(item_no, 0)) - expected[item_no])
        for item_no in (13, 14, 15)
    )
    deferred_tax_actual = int(
        occupancy.loc[
            occupancy["item_no"].astype(int).isin({11, 12}),
            "adjustment_amount_minor",
        ].sum()
    )
    deferred_tax_expected = 0
    if not bridges.empty and "profit_or_loss_effect_minor" in bridges:
        family = bridges.get(
            "family",
            pd.Series("", index=bridges.index),
        ).fillna("")
        deferred_tax_expected = int(
            pd.to_numeric(
                bridges.loc[
                    family.eq("deferred_tax"),
                    "profit_or_loss_effect_minor",
                ],
                errors="coerce",
            )
            .fillna(0)
            .sum()
        )
    exclusive_conflicts = int(
        occupancy_issues.get(
            "issue_code",
            pd.Series("", index=occupancy_issues.index),
        )
        .eq("EXCLUSIVE_CLAIM_CONFLICT")
        .sum()
    )
    return {
        "voucher_balance_difference_minor": voucher_difference,
        "journal_trial_balance_difference_minor": (
            journal_trial_balance_difference
        ),
        "occupancy_issue_count": len(occupancy_issues),
        "exclusive_conflict_count": exclusive_conflicts,
        "balance_bridge_difference_minor": balance_bridge_difference,
        "deferred_tax_difference_minor": (
            deferred_tax_actual - deferred_tax_expected
        ),
    }


def _check(
    check_id: str,
    name: str,
    difference_minor: int,
    *,
    notes: str,
) -> dict:
    return {
        "check_id": check_id,
        "name": name,
        "actual_minor": difference_minor,
        "expected_minor": 0,
        "difference_minor": difference_minor,
        "tolerance_minor": 0,
        "status": "pass" if difference_minor == 0 else "fail",
        "notes": notes,
    }


def reconcile_all(
    summary: pd.DataFrame,
    *,
    direct_cfo_minor: int | None,
    voucher_balance_difference_minor: int = 0,
    journal_trial_balance_difference_minor: int = 0,
    occupancy_issue_count: int = 0,
    exclusive_conflict_count: int = 0,
    balance_bridge_difference_minor: int = 0,
    deferred_tax_difference_minor: int = 0,
) -> dict:
    """汇总七类核对；差异只报告，不分配到任何调节项目。"""
    required = {"item_no", "amount_minor"}
    missing = sorted(required - set(summary.columns))
    if missing:
        raise ValueError(f"调节汇总缺少字段：{missing}")
    item_numbers = summary["item_no"].astype(int).tolist()
    if item_numbers != list(range(17)):
        raise ValueError("调节汇总必须按净利润及1至16项完整排列")

    net_profit = int(summary.loc[summary["item_no"].eq(0), "amount_minor"].item())
    adjustments = int(
        summary.loc[summary["item_no"].astype(int).gt(0), "amount_minor"].sum()
    )
    indirect_cfo = net_profit + adjustments
    checks = [
        _check(
            "voucher_balance",
            "凭证借贷平衡",
            int(voucher_balance_difference_minor),
            notes="全部凭证借方发生额与贷方发生额差异",
        ),
        _check(
            "journal_trial_balance",
            "序时账与余额表发生额",
            int(journal_trial_balance_difference_minor),
            notes="序时账发生额与余额表本期发生额差异",
        ),
        _check(
            "amount_occupancy",
            "金额占用完整性",
            int(occupancy_issue_count),
            notes="存在未分类、超额占用或占用引用错误的数量",
        ),
        _check(
            "exclusive_items",
            "项目互斥",
            int(exclusive_conflict_count),
            notes="互斥项目重复占用金额片段的数量",
        ),
        _check(
            "balance_bridge",
            "余额桥接",
            int(balance_bridge_difference_minor),
            notes="营运资本余额变动与桥接明细差异",
        ),
        _check(
            "deferred_tax",
            "递延所得税",
            int(deferred_tax_difference_minor),
            notes="第11、12项与递延所得税费用或收益差异",
        ),
    ]
    if direct_cfo_minor is None:
        direct_difference = None
        checks.append(
            {
                "check_id": "direct_indirect_cfo",
                "name": "直接法与间接法总对账",
                "actual_minor": indirect_cfo,
                "expected_minor": None,
                "difference_minor": None,
                "tolerance_minor": 0,
                "status": "not_run",
                "notes": "未提供主表直接法经营活动现金流量净额",
            }
        )
    else:
        direct_difference = indirect_cfo - int(direct_cfo_minor)
        checks.append(
            _check(
                "direct_indirect_cfo",
                "直接法与间接法总对账",
                direct_difference,
                notes="间接法经营活动现金流量净额减主表直接法金额",
            )
        )

    return {
        "net_profit_minor": net_profit,
        "adjustments_minor": adjustments,
        "indirect_cfo_minor": indirect_cfo,
        "direct_cfo_minor": direct_cfo_minor,
        "difference_minor": direct_difference,
        "status": (
            "fail"
            if any(check["status"] == "fail" for check in checks)
            else "not_run"
            if direct_cfo_minor is None
            else "pass"
        ),
        "checks": checks,
    }
