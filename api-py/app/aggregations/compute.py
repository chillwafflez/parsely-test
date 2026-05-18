from decimal import Decimal
from typing import Sequence

from app.aggregations.operations import AggregationOperation


def compute(operation: AggregationOperation, values: Sequence[Decimal]) -> Decimal | None:
    if len(values) == 0:
        # Count on an empty input is 0; every other operation is None
        # (undefined). None lets the field flag as missing rather than
        # silently displaying 0 / a wrong total.
        return Decimal(0) if operation == AggregationOperation.COUNT else None

    if operation == AggregationOperation.SUM:
        return _sum(values)
    if operation == AggregationOperation.AVERAGE:
        return _sum(values) / Decimal(len(values))
    if operation == AggregationOperation.COUNT:
        return Decimal(len(values))
    if operation == AggregationOperation.MIN:
        return min(values)
    if operation == AggregationOperation.MAX:
        return max(values)

    raise ValueError(f"Unsupported aggregation operation: {operation!r}")


def format_value(operation: AggregationOperation, result: Decimal | None) -> str:
    if result is None:
        return ""
    if operation == AggregationOperation.COUNT:
        return str(int(result))
    return f"{result:.2f}"


def _sum(values: Sequence[Decimal]) -> Decimal:
    total = Decimal(0)
    for v in values:
        total += v
    return total
