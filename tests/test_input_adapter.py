from datetime import datetime
from importlib import import_module
import json

import pandas as pd
import pytest

from cashflow_indirect.contracts import RunSpec


adapter = import_module("cashflow_indirect.input_adapter")


def sample_spec():
    return RunSpec(
        "甲公司",
        "2026-01-01",
        "2026-12-31",
        "CNY",
        2,
        "元",
        0,
        100000,
    )


def sample_journal():
    frame = pd.DataFrame(
        [
            {
                "__source_row__": 10,
                "记账时间": "2026-01-31",
                "会计月": "1月",
                "凭证种类": "记",
                "凭证编号": "001",
                "编号": 7,
                "业务说明": "计提折旧",
                "一级编号": "6602",
                "一级科目": "管理费用",
                "科目编号": "660201",
                "科目名称": "折旧费",
                "借方发生额": "60.00",
                "贷方发生额": 0,
                "对方科目": "累计折旧",
                "匹配类型": "算法生成(兜底拆分)",
            },
            {
                "__source_row__": 11,
                "记账时间": "2026-01-31",
                "会计月": "1月",
                "凭证种类": "记",
                "凭证编号": "001",
                "编号": 7,
                "业务说明": "计提折旧",
                "一级编号": "6602",
                "一级科目": "管理费用",
                "科目编号": "660201",
                "科目名称": "折旧费",
                "借方发生额": "40.00",
                "贷方发生额": 0,
                "对方科目": "累计折旧",
                "匹配类型": "算法生成(兜底拆分)",
            },
        ]
    )
    frame.attrs.update(source_file="序时账.xlsx", source_sheet="分录")
    return frame


def test_normalize_analyzer_output_preserves_split_rows_and_source_line():
    result = adapter.normalize_journal_frame(sample_journal(), {}, sample_spec())

    assert result["original_line_id"].tolist() == ["7", "7"]
    assert result["split_index"].tolist() == [0, 1]
    assert result["debit_minor"].tolist() == [6000, 4000]
    assert result["signed_minor"].tolist() == [6000, 4000]
    assert result["counterparty_level1"].tolist() == ["累计折旧", "累计折旧"]
    assert result["source_row"].tolist() == [10, 11]
    assert result["line_id"].is_unique
    assert result["voucher_id"].nunique() == 1


def test_journal_rejects_two_sided_amount_on_one_row():
    raw = sample_journal().iloc[[0]].copy()
    raw["贷方发生额"] = "1.00"

    with pytest.raises(adapter.InputProtocolError, match="借贷方同时"):
        adapter.normalize_journal_frame(raw, {}, sample_spec())


def test_real_analyzer_export_derives_missing_journal_technical_fields():
    raw = pd.DataFrame(
        [
            {
                "__source_row__": 27,
                "会计月": "2025-01-02",
                "凭证编号": 1,
                "一级编号": 1221,
                "一级科目": "其他应收款",
                "科目名称": "其他应收款_个人往来",
                "借方发生额": 1710,
                "贷方发生额": 0,
                "对方科目": "银行存款",
                "匹配类型": "标准模板",
                "摘要": "借备用金购买电脑",
                "科目编码": "1221.01",
            }
        ]
    )
    raw.attrs.update(source_file="真实序时账.xlsx", source_sheet="生成结果")
    mapping = {
        "会计月": "voucher_date",
        "摘要": "summary",
        "科目编码": "account_code",
    }

    result = adapter.normalize_journal_frame(raw, mapping, sample_spec())

    assert result.loc[0, "voucher_date"] == "2025-01-02"
    assert result.loc[0, "fiscal_period"] == "2025-01"
    assert result.loc[0, "voucher_type"] == "记"
    assert result.loc[0, "original_line_id"] == "source-row-27"
    assert result.loc[0, "summary"] == "借备用金购买电脑"
    assert result.loc[0, "account_code"] == "1221.01"


def test_trial_balance_keeps_auxiliary_dimension_out_of_account_total():
    raw = pd.DataFrame(
        [
            {
                "行类型": "科目",
                "科目编码": "1000",
                "科目名称": "资产",
                "科目级次": 1,
                "上级科目编码": "",
                "科目性质": "资产",
                "期初借方余额": "8,000.00",
                "期初贷方余额": 0,
                "本期借方发生额": "4,000.00",
                "本期贷方发生额": "2,000.00",
                "期末借方余额": "10,000.00",
                "期末贷方余额": 0,
            },
            {
                "行类型": "科目",
                "科目编码": "1405",
                "科目名称": "库存商品",
                "科目级次": 2,
                "上级科目编码": "1000",
                "科目性质": "资产",
                "期初借方余额": "8,000.00",
                "期初贷方余额": 0,
                "本期借方发生额": "4,000.00",
                "本期贷方发生额": "2,000.00",
                "期末借方余额": "10,000.00",
                "期末贷方余额": 0,
            },
            {
                "行类型": "辅助",
                "所属科目编码": "1405",
                "辅助类型": "项目",
                "辅助编码": "P01",
                "辅助名称": "甲项目",
                "期初借方余额": "8,000.00",
                "期初贷方余额": 0,
                "本期借方发生额": "4,000.00",
                "本期贷方发生额": "2,000.00",
                "期末借方余额": "10,000.00",
                "期末贷方余额": 0,
            },
        ]
    )

    trial_balance, account_nodes, auxiliary_dimensions = (
        adapter.normalize_trial_balance_frame(raw, {}, sample_spec())
    )

    assert account_nodes.query("is_leaf")["closing_debit_minor"].sum() == 1000000
    assert auxiliary_dimensions["closing_debit_minor"].sum() == 1000000
    assert trial_balance["closing_debit_minor"].sum() == 1000000
    assert trial_balance.loc[0, "account_path"] == "资产/库存商品"


def test_trial_balance_rejects_auxiliary_period_amount_mismatch():
    raw = pd.DataFrame(
        [
            {
                "行类型": "科目",
                "科目编码": "1000",
                "科目名称": "资产",
                "科目级次": 1,
                "上级科目编码": "",
                "科目性质": "资产",
                "期初借方余额": 100,
                "期初贷方余额": 0,
                "本期借方发生额": 50,
                "本期贷方发生额": 20,
                "期末借方余额": 130,
                "期末贷方余额": 0,
            },
            {
                "行类型": "科目",
                "科目编码": "1002",
                "科目名称": "银行存款",
                "科目级次": 2,
                "上级科目编码": "1000",
                "科目性质": "资产",
                "期初借方余额": 100,
                "期初贷方余额": 0,
                "本期借方发生额": 50,
                "本期贷方发生额": 20,
                "期末借方余额": 130,
                "期末贷方余额": 0,
            },
            {
                "行类型": "辅助",
                "所属科目编码": "1002",
                "辅助类型": "银行账户",
                "辅助编码": "A01",
                "辅助名称": "基本户",
                "期初借方余额": 100,
                "期初贷方余额": 0,
                "本期借方发生额": 40,
                "本期贷方发生额": 20,
                "期末借方余额": 130,
                "期末贷方余额": 0,
            },
        ]
    )

    with pytest.raises(adapter.InputProtocolError, match="period_debit_minor"):
        adapter.normalize_trial_balance_frame(raw, {}, sample_spec())


def test_real_balance_export_derives_account_tree_and_auxiliary_combination():
    raw = pd.DataFrame(
        [
            {
                "科目编码": "1002",
                "科目名称": "银行存款",
                "核算维度": None,
                "年初借方余额": 100,
                "年初贷方余额": None,
                "本年借方发生额": 50,
                "本年贷方发生额": 20,
                "年末借方余额": 130,
                "年末贷方余额": None,
            },
            {
                "科目编码": "1002.01",
                "科目名称": "银行存款_人民币",
                "核算维度": None,
                "年初借方余额": 100,
                "年初贷方余额": None,
                "本年借方发生额": 50,
                "本年贷方发生额": 20,
                "年末借方余额": 130,
                "年末贷方余额": None,
            },
            {
                "科目编码": "1002.01",
                "科目名称": "银行存款_人民币",
                "核算维度": "银行账户:001,基本户",
                "年初借方余额": 100,
                "年初贷方余额": None,
                "本年借方发生额": 50,
                "本年贷方发生额": 20,
                "年末借方余额": 130,
                "年末贷方余额": None,
            },
        ]
    )
    mapping = {
        "年初借方余额": "opening_debit",
        "年初贷方余额": "opening_credit",
        "本年借方发生额": "period_debit",
        "本年贷方发生额": "period_credit",
        "年末借方余额": "closing_debit",
        "年末贷方余额": "closing_credit",
    }

    trial_balance, account_nodes, auxiliary = (
        adapter.normalize_trial_balance_frame(raw, mapping, sample_spec())
    )

    assert account_nodes["account_code"].tolist() == ["1002", "1002.01"]
    assert trial_balance.loc[0, "account_path"] == "银行存款/银行存款_人民币"
    assert trial_balance.loc[0, "account_nature"] == "资产"
    assert auxiliary.loc[0, "owner_account_code"] == "1002.01"
    assert auxiliary.loc[0, "dimension_type"] == "组合"
    assert auxiliary.loc[0, "dimension_name"] == "银行账户:001,基本户"


def test_multidimensional_balance_export_restores_tree_and_combination():
    raw = pd.DataFrame(
        [
            [17047, "一级", "1123", "预付账款", "借", 100, 40, 10, "借", 130, None, None, None, None, None, None],
            [17047, "二级", "112301", "预付账款", "借", 100, 40, 10, "借", 130, None, None, None, None, None, None],
            [17047, "辅助", "112301", "预付账款", "借", 60, 20, 5, "借", 75, "P1", "甲方", "C1", "合同一", "J1", "项目一"],
            [17047, "辅助", "112301", "预付账款", "借", 40, 20, 5, "借", 55, "P2", "乙方", "C2", "合同二", "J2", "项目二"],
        ],
        columns=[
            "公司编码",
            "科目层级",
            "科目",
            "科目描述",
            "方向",
            "期初余额",
            "本期借方",
            "本期贷方",
            "方向.1",
            "期末余额",
            "往来",
            "往来描述",
            "合同",
            "合同描述",
            "项目",
            "项目描述",
        ],
    )

    trial_balance, account_nodes, auxiliary = (
        adapter.normalize_trial_balance_frame(raw, {}, sample_spec())
    )

    assert account_nodes[["account_code", "parent_account_code"]].values.tolist() == [
        ["1123", ""],
        ["112301", "1123"],
    ]
    assert trial_balance["account_code"].tolist() == ["112301"]
    assert trial_balance.loc[0, "opening_debit_minor"] == 10000
    assert trial_balance.loc[0, "closing_debit_minor"] == 13000
    assert auxiliary["owner_account_code"].tolist() == ["112301", "112301"]
    assert json.loads(auxiliary.loc[0, "dimension_code"]) == {
        "往来": {"编码": "P1", "描述": "甲方"},
        "合同": {"编码": "C1", "描述": "合同一"},
        "项目": {"编码": "J1", "描述": "项目一"},
    }
    assert auxiliary.loc[0, "dimension_name"] == (
        "往来:P1 甲方；合同:C1 合同一；项目:J1 项目一"
    )


def test_multidimensional_balance_export_rejects_missing_parent_level():
    raw = pd.DataFrame(
        [
            {
                "公司编码": 17047,
                "科目层级": "二级",
                "科目": "112301",
                "科目描述": "预付账款",
                "方向": "借",
                "期初余额": 0,
                "本期借方": 0,
                "本期贷方": 0,
                "方向.1": "借",
                "期末余额": 0,
            }
        ]
    )

    with pytest.raises(adapter.InputProtocolError, match="缺少上一级科目"):
        adapter.normalize_trial_balance_frame(raw, {}, sample_spec())


def test_multidimensional_balance_collapses_identical_reclassification_root():
    raw = pd.DataFrame(
        [
            [17047, "一级", "9901", "重分类", "借", 0, 0, 0, "借", 0],
            [17047, "一级", "9901", "重分类", "贷", 0, 0, 0, "贷", 0],
            [17047, "二级", "990102", "重分类/应付账款", "借", 0, 0, 0, "借", 0],
        ],
        columns=[
            "公司编码",
            "科目层级",
            "科目",
            "科目描述",
            "方向",
            "期初余额",
            "本期借方",
            "本期贷方",
            "方向.1",
            "期末余额",
        ],
    )

    _, account_nodes, _ = adapter.normalize_trial_balance_frame(
        raw, {}, sample_spec()
    )

    assert account_nodes["account_code"].tolist() == ["9901", "990102"]
    assert account_nodes["account_nature"].tolist() == ["重分类", "重分类"]


def test_trial_balance_rejects_conflicting_duplicate_account_code():
    raw = pd.DataFrame(
        [
            {
                "行类型": "科目",
                "科目编码": "1122",
                "科目名称": "应收账款",
                "科目级次": 1,
                "上级科目编码": "",
                "科目性质": "资产",
                "期初借方余额": 0,
                "期初贷方余额": 0,
                "本期借方发生额": 0,
                "本期贷方发生额": 0,
                "期末借方余额": 100,
                "期末贷方余额": 0,
            },
            {
                "行类型": "科目",
                "科目编码": "1122",
                "科目名称": "应收账款",
                "科目级次": 1,
                "上级科目编码": "",
                "科目性质": "资产",
                "期初借方余额": 0,
                "期初贷方余额": 0,
                "本期借方发生额": 0,
                "本期贷方发生额": 0,
                "期末借方余额": 200,
                "期末贷方余额": 0,
            },
        ]
    )

    with pytest.raises(adapter.InputProtocolError, match="冲突的重复科目编码"):
        adapter.normalize_trial_balance_frame(raw, {}, sample_spec())


def test_balance_with_direction_is_converted_to_debit_credit_columns():
    raw = pd.DataFrame(
        [
            {
                "行类型": "科目",
                "科目编码": "1122",
                "科目名称": "应收账款",
                "科目级次": 1,
                "上级科目编码": "",
                "科目性质": "资产",
                "期初余额": "100.00",
                "期初方向": "借",
                "本期借方发生额": 0,
                "本期贷方发生额": "150.00",
                "期末余额": "50.00",
                "期末方向": "贷",
            }
        ]
    )

    trial_balance, _, _ = adapter.normalize_trial_balance_frame(
        raw, {}, sample_spec()
    )

    assert trial_balance.loc[0, "opening_debit_minor"] == 10000
    assert trial_balance.loc[0, "opening_credit_minor"] == 0
    assert trial_balance.loc[0, "closing_debit_minor"] == 0
    assert trial_balance.loc[0, "closing_credit_minor"] == 5000


def test_balance_with_direction_preserves_negative_source_balance():
    raw = pd.DataFrame(
        [
            {
                "行类型": "科目",
                "科目编码": "6603",
                "科目名称": "财务费用",
                "科目级次": 1,
                "上级科目编码": "",
                "科目性质": "损益",
                "期初余额": "-100.00",
                "期初方向": "借",
                "本期借方发生额": 0,
                "本期贷方发生额": 0,
                "期末余额": "-50.00",
                "期末方向": "借",
            }
        ]
    )

    trial_balance, _, _ = adapter.normalize_trial_balance_frame(
        raw, {}, sample_spec()
    )

    assert trial_balance.loc[0, "opening_debit_minor"] == -10000
    assert trial_balance.loc[0, "closing_debit_minor"] == -5000


def test_journal_codes_align_to_unique_related_balance_leaf():
    journal = pd.DataFrame(
        [
            {
                "account_code": "2211.01.01.01.01.01",
                "account_name": "应付职工薪酬_员工工资_本年工资_岗位工资",
            },
            {
                "account_code": "5401.03.05",
                "account_name": "工程施工_间接费用_业务招待费",
            },
        ]
    )
    trial_balance = pd.DataFrame(
        [
            {
                "account_code": "2211.01.01.01.01",
                "account_name": "应付职工薪酬_员工工资_本年工资",
            },
            {
                "account_code": "5401.03.05.01",
                "account_name": "工程施工_间接费用_业务招待费_商务",
            },
        ]
    )

    result = adapter.align_journal_account_codes(journal, trial_balance)

    assert result["account_code"].tolist() == [
        "2211.01.01.01.01",
        "5401.03.05.01",
    ]
    assert result["source_account_code"].tolist() == [
        "2211.01.01.01.01.01",
        "5401.03.05",
    ]


def test_read_excel_sheet_preserves_chinese_and_source_row(tmp_path):
    path = tmp_path / "中文序时账.xlsx"
    pd.DataFrame(
        [
            {
                "记账时间": datetime(2026, 1, 31),
                "业务说明": "红字冲回",
                "借方发生额": -12.30,
            }
        ]
    ).to_excel(path, sheet_name="序时账", index=False)

    result = adapter.read_excel_sheet(path, "序时账", 1)

    assert result.loc[0, "业务说明"] == "红字冲回"
    assert result.loc[0, "__source_row__"] == 2
    assert result.attrs["source_file"] == "中文序时账.xlsx"
    assert result.attrs["source_sheet"] == "序时账"
