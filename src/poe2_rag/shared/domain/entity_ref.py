"""Stable identity of the game entity a chunk describes."""

from __future__ import annotations

import re
from dataclasses import dataclass

from poe2_rag.shared.domain._text import is_blank
from poe2_rag.shared.domain.errors import InvalidEntityRefError

_SLUG = re.compile(r"[a-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class EntityRef:
    """Version-independent reference to a game entity (gem, support, mechanic, item)."""

    kind: str
    slug: str
    name: str

    def __post_init__(self) -> None:
        if is_blank(self.kind):
            raise InvalidEntityRefError("kind must be a non-blank string")
        if is_blank(self.name):
            raise InvalidEntityRefError("name must be a non-blank string")
        if not isinstance(self.slug, str) or _SLUG.fullmatch(self.slug) is None:
            raise InvalidEntityRefError(f"slug must match [a-z0-9_-]+, got {self.slug!r}")

    @property
    def key(self) -> str:
        """Stable identifier `<kind>:<slug>`, shared by every version of the entity."""
        return f"{self.kind}:{self.slug}"
