from importlib import import_module


def test_run_status_covers_the_complete_pipeline():
    contracts = import_module("cashflow_indirect.contracts")

    assert hasattr(contracts, "RunStatus")
    assert [status.value for status in contracts.RunStatus] == [
        "created",
        "input_snapshotted",
        "normalized",
        "prepared",
        "awaiting_ai",
        "ai_received",
        "ready_to_finalize",
        "calculated",
        "reconciled",
        "exported",
        "blocked",
        "failed",
    ]


def test_rule_book_contains_all_sixteen_items_with_evidence(rule_book):
    assert [item.item_no for item in rule_book.items] == list(range(1, 17))
    assert rule_book.items[0].name == "资产减值准备"
    assert rule_book.items[-1].name == "其他明确调整"
    assert "CAS8" in rule_book.items[0].official_source_ids
    assert all(
        item.family and item.rule_ids and item.official_source_ids
        for item in rule_book.items
    )
    assert all(
        source_id in rule_book.official_sources
        for item in rule_book.items
        for source_id in item.official_source_ids
    )
    assert rule_book.paired_offsets == ((3, 13), (4, 13), (5, 13))
