from importlib import import_module
import json
from dataclasses import replace

import pandas as pd
import pytest

from cashflow_indirect.contracts import RunSpec


storage = import_module("cashflow_indirect.storage")


def sample_spec():
    return RunSpec(
        "甲公司",
        "2026-01-01",
        "2026-12-31",
        "CNY",
        2,
        "元",
        10000,
        5000,
    )


def test_run_store_never_overwrites_same_input(tmp_path):
    first = storage.RunStore.create(
        tmp_path, sample_spec(), {"journal": "a"}, "r1", "c1"
    )
    second = storage.RunStore.create(
        tmp_path, sample_spec(), {"journal": "a"}, "r1", "c1"
    )

    assert first.run_id != second.run_id
    assert first.input_fingerprint == second.input_fingerprint
    assert first.root != second.root
    manifest = json.loads(
        (first.root / "run_manifest.json").read_text(encoding="utf-8-sig")
    )
    assert manifest["run_id"] == first.run_id
    assert manifest["input_hashes"] == {"journal": "a"}


def test_net_profit_confirmation_source_changes_input_fingerprint(tmp_path):
    confirmed = replace(
        sample_spec(),
        journal_preprocessing_confirmed=True,
        net_profit_user_confirmed=True,
        net_profit_source="user_provided",
        net_profit_source_note="客户在当前任务直接提供",
    )
    changed_source = replace(
        confirmed,
        net_profit_source="user_confirmed_statement",
        net_profit_source_note="客户确认采用利润表金额",
    )

    first = storage.RunStore.create(
        tmp_path, confirmed, {"journal": "a"}, "r1", "c1"
    )
    second = storage.RunStore.create(
        tmp_path, changed_source, {"journal": "a"}, "r1", "c1"
    )

    assert first.input_fingerprint != second.input_fingerprint


def test_checkpoint_is_invalid_when_input_hash_changes(tmp_path):
    store = storage.RunStore.create(
        tmp_path, sample_spec(), {"journal": "a"}, "r1", "c1"
    )
    output = store.write_parquet(
        "01_normalized",
        "journal_lines.parquet",
        pd.DataFrame({"x": [1]}),
    )
    store.complete_stage(
        "normalized",
        {"journal": "a"},
        {"journal_lines": storage.sha256_file(output)},
    )

    assert store.can_resume("normalized", {"journal": "a"}, "r1", "c1")
    assert not store.can_resume("normalized", {"journal": "b"}, "r1", "c1")


def test_json_serialization_failure_does_not_create_target(tmp_path):
    store = storage.RunStore.create(
        tmp_path, sample_spec(), {"journal": "a"}, "r1", "c1"
    )

    with pytest.raises(TypeError):
        store.write_json("00_input", "bad.json", {"bad": object()})

    assert not (store.root / "00_input" / "bad.json").exists()


def test_events_must_continue_from_the_previous_sequence(tmp_path):
    store = storage.RunStore.create(
        tmp_path, sample_spec(), {"journal": "a"}, "r1", "c1"
    )
    store.append_events([{"sequence_no": 1, "event_type": "created"}])

    with pytest.raises(ValueError, match="事件序号"):
        store.append_events([{"sequence_no": 3, "event_type": "skipped"}])


def test_second_writer_is_rejected_while_run_is_locked(tmp_path):
    store = storage.RunStore.create(
        tmp_path, sample_spec(), {"journal": "a"}, "r1", "c1"
    )

    with store.writer_lock():
        with pytest.raises(storage.RunLockedError, match="正在写入"):
            store.write_json("01_normalized", "blocked.json", {"x": 1})


def test_writer_lock_retries_transient_permission_error(tmp_path, monkeypatch):
    store = storage.RunStore.create(
        tmp_path, sample_spec(), {"journal": "a"}, "r1", "c1"
    )
    original_open = storage.Path.open
    attempts = 0

    def transiently_blocked(path, *args, **kwargs):
        nonlocal attempts
        if path.name == ".writer.lock" and args[0] == "a+b":
            attempts += 1
            if attempts < 25:
                raise PermissionError("写入锁被短暂占用")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(storage.Path, "open", transiently_blocked)
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)

    with store.writer_lock():
        pass

    assert attempts == 25


def test_writer_lock_keeps_persistent_permission_error(tmp_path, monkeypatch):
    store = storage.RunStore.create(
        tmp_path, sample_spec(), {"journal": "a"}, "r1", "c1"
    )
    original_open = storage.Path.open
    attempts = 0

    def persistently_blocked(path, *args, **kwargs):
        nonlocal attempts
        if path.name == ".writer.lock" and args[0] == "a+b":
            attempts += 1
            raise PermissionError("写入锁持续无权限")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(storage.Path, "open", persistently_blocked)
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)

    with pytest.raises(PermissionError, match="持续无权限"):
        with store.writer_lock():
            pass

    assert attempts == 100


def test_checkpoint_is_invalid_when_output_hash_changes(tmp_path):
    store = storage.RunStore.create(
        tmp_path, sample_spec(), {"journal": "a"}, "r1", "c1"
    )
    output = store.write_parquet(
        "01_normalized",
        "journal_lines.parquet",
        pd.DataFrame({"x": [1]}),
    )
    store.complete_stage(
        "normalized",
        {"journal": "a"},
        {"journal_lines": storage.sha256_file(output)},
    )
    output.write_bytes(b"changed")

    assert not store.can_resume("normalized", {"journal": "a"}, "r1", "c1")
