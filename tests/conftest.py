from pathlib import Path

import pytest

from cashflow_indirect.contracts import load_rule_book


@pytest.fixture
def rule_book():
    return load_rule_book(Path("rules/cas_general_v1.json"))
