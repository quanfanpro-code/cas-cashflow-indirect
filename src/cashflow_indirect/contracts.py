"""稳定的数据合同。"""

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path


class RunStatus(str, Enum):
    CREATED = "created"
    INPUT_SNAPSHOTTED = "input_snapshotted"
    NORMALIZED = "normalized"
    PREPARED = "prepared"
    AWAITING_AI = "awaiting_ai"
    AI_RECEIVED = "ai_received"
    READY_TO_FINALIZE = "ready_to_finalize"
    CALCULATED = "calculated"
    RECONCILED = "reconciled"
    EXPORTED = "exported"
    BLOCKED = "blocked"
    FAILED = "failed"


class DecisionStatus(str, Enum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    PAIRED_OFFSET = "paired_offset"
    AI_CLASSIFIED = "ai_classified"
    REVIEW_REQUIRED = "review_required"


@dataclass(frozen=True)
class RunSpec:
    entity_name: str
    period_start: str
    period_end: str
    functional_currency: str
    currency_scale: int
    display_unit: str
    net_profit_minor: int
    performance_materiality_minor: int
    financial_entity_flag: bool = False
    direct_cfo_minor: int | None = None
    journal_preprocessing_confirmed: bool = False
    net_profit_user_confirmed: bool = False
    net_profit_source: str = ""
    net_profit_source_note: str = ""


@dataclass(frozen=True)
class ItemRule:
    item_no: int
    name: str
    family: str
    rule_ids: tuple[str, ...]
    official_source_ids: tuple[str, ...]


@dataclass(frozen=True)
class RuleBook:
    schema_version: str
    rule_version: str
    items: tuple[ItemRule, ...]
    exclusive_groups: tuple[tuple[int, ...], ...]
    paired_offsets: tuple[tuple[int, int], ...]
    account_groups: dict[str, tuple[str, ...]]
    official_sources: dict[str, str]


def load_rule_book(path: Path) -> RuleBook:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    items = tuple(
        ItemRule(
            item_no=int(item["item_no"]),
            name=str(item["name"]),
            family=str(item["family"]),
            rule_ids=tuple(map(str, item["rule_ids"])),
            official_source_ids=tuple(map(str, item["official_source_ids"])),
        )
        for item in raw["items"]
    )
    if [item.item_no for item in items] != list(range(1, 17)):
        raise ValueError("规则清单必须按1至16连续排列")
    if any(
        not item.name
        or not item.family
        or not item.rule_ids
        or not item.official_source_ids
        for item in items
    ):
        raise ValueError("每个项目都必须绑定名称、项目族、规则编号和财政部依据编号")

    official_sources = {
        str(source_id): str(url)
        for source_id, url in raw["official_sources"].items()
    }
    unknown_sources = {
        source_id
        for item in items
        for source_id in item.official_source_ids
        if source_id not in official_sources
    }
    if unknown_sources:
        raise ValueError(f"规则引用了未知财政部依据：{sorted(unknown_sources)}")

    return RuleBook(
        schema_version=str(raw["schema_version"]),
        rule_version=str(raw["rule_version"]),
        items=items,
        exclusive_groups=tuple(
            tuple(map(int, group)) for group in raw["exclusive_groups"]
        ),
        paired_offsets=tuple(
            tuple(map(int, pair)) for pair in raw["paired_offsets"]
        ),
        account_groups={
            str(name): tuple(map(str, values))
            for name, values in raw["account_groups"].items()
        },
        official_sources=official_sources,
    )
