"""Money is always an int in minor units plus an ISO currency code."""
from __future__ import annotations

_DECIMALS = {"USD": 2, "EUR": 2, "GBP": 2, "SEK": 2, "NGN": 2, "JPY": 0}
_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "SEK": "kr", "NGN": "₦", "JPY": "¥"}


def fmt(amount_minor: int, currency: str) -> str:
    d = _DECIMALS.get(currency.upper(), 2)
    major = amount_minor / (10**d) if d else amount_minor
    sym = _SYMBOLS.get(currency.upper(), currency.upper() + " ")
    return f"{sym}{major:,.{d}f}" if d else f"{sym}{int(major):,}"


def percent_of(amount_minor: int, percent: float) -> int:
    """Floor to whole minor units so we never over-pay by rounding."""
    return int(amount_minor * percent // 100)


def pct(part_minor: int, whole_minor: int) -> float:
    return (part_minor * 100.0 / whole_minor) if whole_minor else 0.0
