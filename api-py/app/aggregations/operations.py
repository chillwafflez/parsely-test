from enum import Enum


class AggregationOperation(str, Enum):
    # Persisted as the operation's name string (case-insensitive) so schema
    # changes stay additive when new operations land.
    SUM = "Sum"
    AVERAGE = "Average"
    COUNT = "Count"
    MIN = "Min"
    MAX = "Max"

    @classmethod
    def try_parse(cls, name: str | None) -> "AggregationOperation | None":
        if not name:
            return None
        normalized = name.strip().lower()
        for op in cls:
            if op.value.lower() == normalized:
                return op
        return None
