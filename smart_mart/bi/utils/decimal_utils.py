from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

MONEY_QUANT = Decimal("0.01")
UNIT_COST_QUANT = Decimal("0.0001")


def as_decimal(value: object, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value: object) -> Decimal:
    return as_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def quantize_unit_cost(value: object) -> Decimal:
    return as_decimal(value).quantize(UNIT_COST_QUANT, rounding=ROUND_HALF_UP)


def decimal_to_float(value: object) -> float:
    return float(as_decimal(value))
