"""金额的精确换算。"""

from decimal import Decimal, InvalidOperation


class MoneyParseError(ValueError):
    pass


class MoneyPrecisionError(ValueError):
    pass


def parse_minor(value: object, scale: int = 2) -> int:
    if value is None or str(value).strip() == "":
        return 0
    text = str(value).strip().replace(",", "").replace("，", "").replace("－", "-")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise MoneyParseError(f"无法识别金额：{value!r}") from exc
    if not amount.is_finite():
        raise MoneyParseError(f"金额必须是有限数字：{value!r}")
    if negative:
        amount = -amount
    minor = amount.scaleb(scale)
    integral_minor = minor.to_integral_value()
    if (
        isinstance(value, float)
        and abs(minor - integral_minor) <= Decimal("1e-7")
    ):
        minor = integral_minor
    if minor != integral_minor:
        raise MoneyPrecisionError(f"金额超过允许小数位数：{value!r}")
    return int(minor)


def format_minor(value_minor: int, scale: int, display_unit: str) -> Decimal:
    divisors = {"元": Decimal(1), "千元": Decimal(1000), "万元": Decimal(10000)}
    try:
        divisor = divisors[display_unit]
    except KeyError as exc:
        raise ValueError(f"不支持的展示单位：{display_unit}") from exc
    return Decimal(value_minor).scaleb(-scale) / divisor
