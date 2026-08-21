"""Decimal arithmetic helpers for invoice validation."""

from decimal import Decimal, InvalidOperation

# Tolerance for floating-point comparison of monetary values.
TOLERANCE = Decimal("0.01")


def decimal_or_none(value: str | float | int | Decimal | None) -> Decimal | None:
    """Coerce *value* to ``Decimal``, returning ``None`` on failure."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def amounts_match(a: Decimal | None, b: Decimal | None) -> bool:
    """Return ``True`` if *a* and *b* are within tolerance, or both are ``None``."""
    if a is None or b is None:
        return a is b
    return abs(a - b) <= TOLERANCE


def check_total(subtotal: Decimal | None, tax: Decimal | None, total: Decimal | None) -> bool:
    """Verify that subtotal + tax == total within tolerance."""
    if subtotal is None or total is None:
        return True  # cannot validate without values
    expected = subtotal + (tax or Decimal("0"))
    return amounts_match(expected, total)
