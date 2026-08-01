"""运行目录和阶段文件存储。"""

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import tempfile
import time
from typing import Iterator
from uuid import uuid4

import pandas as pd

from .contracts import RunSpec


class RunLockedError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def event_hash(event: dict) -> str:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def validate_event_chain(path: Path) -> None:
    path = Path(path)
    if not path.exists():
        return
    previous_hash = "0" * 64
    for expected_sequence, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"事件留痕第{expected_sequence}行不是合法JSON") from exc
        if int(event.get("sequence_no", 0)) != expected_sequence:
            raise ValueError("事件留痕序号不连续")
        if event.get("previous_event_hash") != previous_hash:
            raise ValueError("事件留痕前序哈希不一致")
        actual_hash = event_hash(event)
        if event.get("event_hash") != actual_hash:
            raise ValueError("事件留痕哈希校验失败")
        previous_hash = actual_hash


class RunStore:
    def __init__(
        self,
        root: Path,
        run_id: str,
        input_fingerprint: str,
        rule_version: str,
        code_version: str,
    ) -> None:
        self.root = root
        self.run_id = run_id
        self.input_fingerprint = input_fingerprint
        self.rule_version = rule_version
        self.code_version = code_version
        self._written_files: dict[str, Path] = {}

    @classmethod
    def create(
        cls,
        root: Path,
        spec: RunSpec,
        input_hashes: dict[str, str],
        rule_version: str,
        code_version: str,
    ) -> "RunStore":
        fingerprint_payload = {
            "spec": asdict(spec),
            "input_hashes": input_hashes,
            "rule_version": rule_version,
        }
        input_fingerprint = hashlib.sha256(
            _canonical_json(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        run_id = uuid4().hex
        run_root = Path(root) / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        store = cls(run_root, run_id, input_fingerprint, rule_version, code_version)
        store.write_json(
            "",
            "run_manifest.json",
            {
                "run_id": run_id,
                "input_fingerprint": input_fingerprint,
                "input_hashes": input_hashes,
                "rule_version": rule_version,
                "code_version": code_version,
                "spec": asdict(spec),
            },
        )
        return store

    @contextmanager
    def writer_lock(self) -> Iterator[None]:
        lock_path = self.root / ".writer.lock"
        # ponytail: 只容忍锁文件的短暂占用；五秒后仍失败就保留原始权限错误。
        for attempt in range(100):
            try:
                handle = lock_path.open("a+b")
                break
            except PermissionError:
                if attempt == 99:
                    raise
                time.sleep(0.05)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            handle.close()
            raise RunLockedError(f"运行目录正在写入：{self.root}") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            handle.close()

    def _target(self, stage: str, filename: str) -> Path:
        directory = self.root / stage if stage else self.root
        directory.mkdir(parents=True, exist_ok=True)
        return directory / filename

    def _write_text(self, target: Path, text: str, encoding: str) -> Path:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="\n",
            delete=False,
            dir=target.parent,
            prefix=f".{target.name}.",
        ) as temporary:
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)
        return target

    def write_json(self, stage: str, filename: str, payload: object) -> Path:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        target = self._target(stage, filename)
        with self.writer_lock():
            result = self._write_text(target, text + "\n", "utf-8-sig")
        self._written_files[Path(filename).stem] = result.relative_to(self.root)
        return result

    def write_parquet(
        self,
        stage: str,
        filename: str,
        frame: pd.DataFrame,
    ) -> Path:
        target = self._target(stage, filename)
        with self.writer_lock():
            with tempfile.NamedTemporaryFile(
                delete=False,
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".parquet",
            ) as temporary:
                temporary_path = Path(temporary.name)
            frame.to_parquet(temporary_path, index=False)
            os.replace(temporary_path, target)
        self._written_files[Path(filename).stem] = target.relative_to(self.root)
        return target

    def append_events(self, events: list[dict]) -> Path:
        target = self._target("07_audit", "events.jsonl")
        serialized = "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            for event in events
        )
        with self.writer_lock():
            validate_event_chain(target)
            last_sequence = 0
            previous_hash = "0" * 64
            if target.exists():
                lines = [
                    line
                    for line in target.read_text(encoding="utf-8-sig").splitlines()
                    if line.strip()
                ]
                if lines:
                    last_event = json.loads(lines[-1])
                    last_sequence = int(last_event["sequence_no"])
                    previous_hash = str(last_event["event_hash"])
            expected = list(
                range(last_sequence + 1, last_sequence + len(events) + 1)
            )
            actual = [int(event["sequence_no"]) for event in events]
            if actual != expected:
                raise ValueError(f"事件序号必须连续，期望{expected}，实际{actual}")
            if events and events[0].get("previous_event_hash") != previous_hash:
                raise ValueError("事件留痕前序哈希不一致")
            for event in events:
                if event.get("event_hash") != event_hash(event):
                    raise ValueError("事件留痕哈希校验失败")
            with target.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
        return target

    def complete_stage(
        self,
        stage: str,
        input_hashes: dict[str, str],
        output_hashes: dict[str, str],
    ) -> Path:
        outputs: dict[str, dict[str, str]] = {}
        for name, expected_hash in output_hashes.items():
            relative_path = self._written_files[name]
            output_path = self.root / relative_path
            actual_hash = sha256_file(output_path)
            if actual_hash != expected_hash:
                raise ValueError(f"阶段输出哈希不一致：{name}")
            outputs[name] = {
                "path": relative_path.as_posix(),
                "sha256": actual_hash,
            }
        return self.write_json(
            "",
            "checkpoint.json",
            {
                "stage": stage,
                "input_hashes": input_hashes,
                "rule_version": self.rule_version,
                "code_version": self.code_version,
                "outputs": outputs,
            },
        )

    def can_resume(
        self,
        stage: str,
        input_hashes: dict[str, str],
        rule_version: str,
        code_version: str,
    ) -> bool:
        checkpoint_path = self.root / "checkpoint.json"
        if not checkpoint_path.exists():
            return False
        try:
            checkpoint = json.loads(
                checkpoint_path.read_text(encoding="utf-8-sig")
            )
            if (
                checkpoint["stage"] != stage
                or checkpoint["input_hashes"] != input_hashes
                or checkpoint["rule_version"] != rule_version
                or checkpoint["code_version"] != code_version
            ):
                return False
            return all(
                sha256_file(self.root / output["path"]) == output["sha256"]
                for output in checkpoint["outputs"].values()
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
