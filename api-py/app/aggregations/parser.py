"""Extracts numeric values from Azure DI layout words. Tolerates currency
symbols, accounting-style negatives ("(123.45)"), thousands separators,
leading signs, and trailing percent signs. EU-style decimals (comma-as-
decimal) are intentionally not supported in v1."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.domain import WordData


_CURRENCY_SYMBOLS = ("$", "€", "£", "¥")


@dataclass(frozen=True)
class ParsedToken:
    # Paired with the source word so callers can later associate the value
    # with its source bbox for preview-list display and per-token highlighting.
    source: WordData
    value: Decimal


def try_parse(content: str | None) -> Decimal | None:
    if content is None or not content.strip():
        return None

    s = content.strip()
    negative = False

    # Accounting-style negative — "(123.45)" → -123.45.
    if len(s) >= 2 and s[0] == "(" and s[-1] == ")":
        negative = True
        s = s[1:-1].strip()

    # Trailing percent — keep the displayed magnitude (12.5%, not 0.125).
    # Aggregations on percent columns sum the displayed values; converting
    # to fractions would surprise users reading the source PDF.
    if s.endswith("%"):
        s = s[:-1].strip()

    # Strip leading sign and/or currency in either order, at most one strip
    # per kind — so "-$50" parses the same as "$-50".
    sign_stripped = False
    currency_stripped = False
    for _ in range(2):
        if not s:
            break
        c = s[0]
        if not sign_stripped and (c == "-" or c == "+"):
            if c == "-":
                negative = not negative
            s = s[1:].lstrip()
            sign_stripped = True
            continue
        if not currency_stripped and c in _CURRENCY_SYMBOLS:
            s = s[1:].lstrip()
            currency_stripped = True
            continue
        break

    # Strip thousands separators — invariant-culture US formatting.
    s = s.replace(",", "")

    try:
        parsed = Decimal(s)
    except (InvalidOperation, ValueError):
        return None

    return -parsed if negative else parsed


def parse_words(words: Iterable[WordData]) -> Iterator[ParsedToken]:
    for word in words:
        value = try_parse(word.content)
        if value is not None:
            yield ParsedToken(source=word, value=value)
