"""Internal text helpers shared by domain value objects."""


def is_blank(value: object) -> bool:
    """Return True when `value` is not a string or contains only whitespace."""
    return not isinstance(value, str) or not value.strip()
