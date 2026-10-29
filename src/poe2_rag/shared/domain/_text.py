"""Internal text helpers shared by domain value objects."""

import re

_SLUG = re.compile(r"[a-z0-9_-]+")


def is_blank(value: object) -> bool:
    """Return True when `value` is not a string or contains only whitespace."""
    return not isinstance(value, str) or not value.strip()


def is_slug(value: object) -> bool:
    """Return True when `value` is a string made only of `[a-z0-9_-]`, safe as an id part."""
    return isinstance(value, str) and _SLUG.fullmatch(value) is not None
