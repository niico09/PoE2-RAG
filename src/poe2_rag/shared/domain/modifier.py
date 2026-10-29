"""Structured stat modifiers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from poe2_rag.shared.domain._text import is_blank
from poe2_rag.shared.domain.errors import InvalidModifierError


class ModifierKind(Enum):
    """How a modifier combines with others; `more`/`less` multiply, the rest sum (driver D2)."""

    INCREASED = "increased"
    REDUCED = "reduced"
    MORE = "more"
    LESS = "less"
    ADDED = "added"
    FLAT = "flat"


_MULTIPLICATIVE_KINDS = frozenset({ModifierKind.MORE, ModifierKind.LESS})


@dataclass(frozen=True, slots=True)
class Modifier:
    """A single stat change such as `20% more damage` or `+12 mana cost`."""

    stat: str
    value: float | int
    kind: ModifierKind

    def __post_init__(self) -> None:
        if is_blank(self.stat):
            raise InvalidModifierError("stat must be a non-blank string")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise InvalidModifierError(f"value must be an int or float, got {self.value!r}")
        if not math.isfinite(self.value):
            raise InvalidModifierError(f"value must be finite, got {self.value!r}")
        if not isinstance(self.kind, ModifierKind):
            raise InvalidModifierError(f"kind must be a ModifierKind, got {self.kind!r}")

    @property
    def is_multiplicative(self) -> bool:
        """True for `more`/`less`, which multiply instead of adding to the `increased` pool."""
        return self.kind in _MULTIPLICATIVE_KINDS
