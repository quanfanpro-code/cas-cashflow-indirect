from decimal import Decimal
from importlib import import_module

import pytest


money = import_module("cashflow_indirect.money")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.56", 123456),
        ("1，234.56", 123456),
        ("(12.30)", -1230),
        (-5, -500),
        ("", 0),
        (None, 0),
    ],
)
def test_parse_minor_preserves_exact_cents(raw, expected):
    assert money.parse_minor(raw, 2) == expected


def test_parse_minor_rejects_text_amount():
    with pytest.raises(money.MoneyParseError):
        money.parse_minor("一百元", 2)


def test_parse_minor_accepts_excel_binary_float_tail():
    assert money.parse_minor(52944.02999999996, 2) == 5294403


@pytest.mark.parametrize("raw", ["1.001", 1.001])
def test_parse_minor_rejects_hidden_fraction_below_scale(raw):
    with pytest.raises(money.MoneyPrecisionError):
        money.parse_minor(raw, 2)


def test_parse_minor_accepts_full_width_minus():
    assert money.parse_minor("－1,234.56", 2) == -123456


@pytest.mark.parametrize(
    ("display_unit", "expected"),
    [
        ("元", Decimal("1234.56")),
        ("千元", Decimal("1.23456")),
        ("万元", Decimal("0.123456")),
    ],
)
def test_format_minor_changes_only_the_display_unit(display_unit, expected):
    assert money.format_minor(123456, 2, display_unit) == expected
