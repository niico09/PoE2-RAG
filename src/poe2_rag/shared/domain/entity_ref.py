"""Stable identity of the game entity a chunk describes."""

from __future__ import annotations

from dataclasses import dataclass

from poe2_rag.shared.domain._text import is_blank, is_slug
from poe2_rag.shared.domain.errors import InvalidEntityRefError


@dataclass(frozen=True, slots=True)
class EntityRef:
    """Version-independent reference to a game entity (gem, support, mechanic, item)."""

    kind: str
    slug: str
    name: str

    def __post_init__(self) -> None:
        if not is_slug(self.kind):
            raise InvalidEntityRefError(f"kind must match [a-z0-9_-]+, got {self.kind!r}")
        if is_blank(self.name):
            raise InvalidEntityRefError("name must be a non-blank string")
        if not is_slug(self.slug):
            raise InvalidEntityRefError(f"slug must match [a-z0-9_-]+, got {self.slug!r}")

    @property
    def key(self) -> str:
        """Stable identifier `<kind>:<slug>`, shared by every version of the entity."""
        return f"{self.kind}:{self.slug}"
