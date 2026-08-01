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
    PROVISIONAL_REVIEW = "provisional_review"
    BLOCKED = "blocked"
    FAILED = "failed"


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

    valid_items = set(range(1, 17))
    exclusive_groups = tuple(
        tuple(map(int, group)) for group in raw["exclusive_groups"]
    )
    if any(
        len(group) < 2
        or len(set(group)) != len(group)
        or not set(group) <= valid_items
        for group in exclusive_groups
    ):
        raise ValueError("互斥项目组必须包含至少两个不同的第1至16项")
    paired_offsets = tuple(
        tuple(map(int, pair)) for pair in raw["paired_offsets"]
    )
    if any(
        len(pair) != 2
        or pair[0] == pair[1]
        or not set(pair) <= valid_items
        for pair in paired_offsets
    ):
        raise ValueError("配套抵销必须由两个不同的第1至16项组成")
    account_groups = {
        str(name): tuple(map(str, values))
        for name, values in raw["account_groups"].items()
    }
    required_groups = {
        "资产减值准备",
        "信用减值准备",
        "递延所得税资产",
        "递延所得税负债",
        "存货",
        "经营性应收",
        "经营性应付",
    }
    if (
        not required_groups <= set(account_groups)
        or any(not name or not values for name, values in account_groups.items())
    ):
        raise ValueError("科目组必须包含全部必需分组且每组至少有一个科目")

    return RuleBook(
        schema_version=str(raw["schema_version"]),
        rule_version=str(raw["rule_version"]),
        items=items,
        exclusive_groups=exclusive_groups,
        paired_offsets=paired_offsets,
        account_groups=account_groups,
        official_sources=official_sources,
    )
