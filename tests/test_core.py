from importlib import import_module

import pandas as pd
import pytest


core = import_module("cashflow_indirect.core")


def normalized_journal():
    return pd.DataFrame(
        [
            {
                "line_id": "line-1",
                "source_file": "序时账.xlsx",
                "source_sheet": "分录",
                "source_row": 10,
                "voucher_id": "voucher-1",
                "voucher_date": "2026-01-31",
                "fiscal_period": "1月",
                "account_code": "660201",
                "account_name": "折旧费",
                "level1_account_code": "6602",
                "level1_account_name": "管理费用",
                "account_path": "管理费用/折旧费",
                "counterparty_level1": "累计折旧",
                "match_type": "算法生成(兜底拆分)",
                "summary": "计提折旧",
                "debit_minor": 6000,
                "credit_minor": 0,
                "signed_minor": 6000,
                "source_side": "debit",
                "source_amount_minor": 6000,
            },
            {
                "line_id": "line-2",
                "source_file": "序时账.xlsx",
                "source_sheet": "分录",
                "source_row": 11,
                "voucher_id": "voucher-2",
                "voucher_date": "2026-02-28",
                "fiscal_period": "2月",
                "account_code": "660201",
                "account_name": "折旧费",
                "level1_account_code": "6602",
                "level1_account_name": "管理费用",
                "account_path": "管理费用/折旧费",
                "counterparty_level1": "累计折旧",
                "match_type": "红字冲回",
                "summary": "冲回多提折旧",
                "debit_minor": -4000,
                "credit_minor": 0,
                "signed_minor": -4000,
                "source_side": "debit",
                "source_amount_minor": -4000,
            },
        ]
    )


def test_each_analyzer_output_row_becomes_one_initial_fragment():
    journal = normalized_journal()

    fragments = core.register_initial_fragments(journal)

    assert len(fragments) == len(journal)
    assert fragments["fragment_id"].is_unique
    signed = fragments.assign(
        signed_amount_minor=fragments["amount_minor"]
        * fragments["direction_sign"]
    )
    assert signed.groupby("line_id")["signed_amount_minor"].sum().to_dict() == {
        "line-1": 6000,
        "line-2": -4000,
    }
    assert fragments["source_amount_minor"].tolist() == [6000, -4000]


def test_zero_line_is_kept_but_not_processable():
    journal = normalized_journal().iloc[[0]].copy()
    journal[["debit_minor", "signed_minor", "source_amount_minor"]] = 0

    fragments = core.register_initial_fragments(journal)

    assert fragments.loc[0, "amount_minor"] == 0
    assert not fragments.loc[0, "processable"]
    assert fragments.loc[0, "effective_side"] == "none"


def test_only_profit_loss_line_is_marked_as_technical_closing():
    fragments = pd.DataFrame(
        [
            {
                "account_code": "6602",
                "account_name": "管理费用",
                "account_nature": "损益",
                "counterparty_level1": "本年利润",
                "signed_minor": -10000,
                "summary": "结转损益",
                "match_type": "结转损益规则",
            },
            {
                "account_code": "2232",
                "account_name": "应付股利",
                "account_nature": "负债",
                "counterparty_level1": "本年利润",
                "signed_minor": -10000,
                "summary": "分配股利",
                "match_type": "结转损益规则",
            },
        ]
    )

    marked = core.mark_technical_closing_lines(
        fragments, {"本年利润", "损益结转"}
    )

    assert marked.loc[
        marked["account_name"] == "管理费用", "technical_closing"
    ].item()
    assert not marked.loc[
        marked["account_name"] == "应付股利", "technical_closing"
    ].item()


def test_closing_direction_must_match_profit_or_loss_account():
    fragments = pd.DataFrame(
        [
            {
                "account_code": "6602",
                "account_name": "管理费用",
                "account_nature": "损益",
                "counterparty_level1": "本年利润",
                "signed_minor": 10000,
                "summary": "结转损益",
                "match_type": "算法生成",
            }
        ]
    )

    marked = core.mark_technical_closing_lines(fragments, {"本年利润"})

    assert not marked.loc[0, "technical_closing"]


def test_explicit_closing_rule_accepts_reverse_profit_or_loss_balance():
    fragments = pd.DataFrame(
        [
            {
                "account_code": "6801.02",
                "account_name": "所得税费用_递延所得税费用",
                "account_nature": "损益",
                "counterparty_level1": "本年利润",
                "signed_minor": 10000,
                "summary": "结转损益",
                "match_type": "结转损益规则",
            }
        ]
    )

    marked = core.mark_technical_closing_lines(fragments, {"本年利润"})

    assert marked.loc[0, "technical_closing"]


def test_explicit_asset_id_links_cross_voucher_fragments():
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f1",
                "voucher_id": "v1",
                "voucher_date": "2026-03-01",
                "event_type": "asset_disposal",
                "asset_id": "FA-001",
                "summary": "转入固定资产清理",
                "account_name": "固定资产",
                "counterparty_level1": "固定资产清理",
            },
            {
                "fragment_id": "f2",
                "voucher_id": "v2",
                "voucher_date": "2026-03-08",
                "event_type": "asset_disposal",
                "asset_id": "FA-001",
                "summary": "收到处置款",
                "account_name": "银行存款",
                "counterparty_level1": "固定资产清理",
            },
        ]
    )

    linked = core.link_business_events(fragments)

    assert linked["business_event_id"].nunique() == 1
    assert not linked["requires_ai"].any()


def test_weak_event_evidence_is_retained_for_ai_instead_of_forced():
    fragments = pd.DataFrame(
        [
            {
                "fragment_id": "f1",
                "voucher_id": "v1",
                "voucher_date": "2026-03-01",
                "event_type": "government_grant",
                "summary": "收到补助",
                "account_name": "银行存款",
                "counterparty_level1": "其他收益",
            }
        ]
    )

    linked = core.link_business_events(fragments)

    assert linked.loc[0, "business_event_id"]
    assert linked.loc[0, "requires_ai"]


def test_split_fragment_preserves_exact_physical_amount():
    fragment = pd.Series(
        {
            "fragment_id": "f1",
            "amount_minor": 10000,
            "direction_sign": 1,
            "source_side": "debit",
        }
    )

    result = core.split_fragment(
        fragment,
        [
            {"purpose": "损益", "amount_minor": 6000},
            {"purpose": "存货", "amount_minor": 4000},
        ],
    )

    assert result["amount_minor"].sum() == 10000
    assert result["parent_fragment_id"].unique().tolist() == ["f1"]
    assert result["fragment_id"].is_unique


@pytest.mark.parametrize(
    "allocations",
    [
        [{"amount_minor": 10001}],
        [{"amount_minor": 9000}],
        [{"amount_minor": -1}, {"amount_minor": 10001}],
    ],
)
def test_split_fragment_rejects_invalid_allocation(allocations):
    fragment = pd.Series(
        {"fragment_id": "f1", "amount_minor": 10000, "direction_sign": 1}
    )

    with pytest.raises(ValueError, match="分配"):
        core.split_fragment(fragment, allocations)


def test_forex_gain_account_606_is_treated_as_income_when_marking_closing():
    journal = pd.DataFrame(
        [
            {
                "line_id": "line-fx",
                "account_code": "6061",
                "account_name": "汇兑收益",
                "account_nature": "损益",
                "counterparty_level1": "本年利润",
                "match_type": "规则匹配",
                "summary": "结转损益",
                "signed_minor": 100,
            }
        ]
    )

    result = core.mark_technical_closing_lines(
        journal,
        {"本年利润", "利润分配"},
    )

    assert bool(result.loc[0, "technical_closing"])
