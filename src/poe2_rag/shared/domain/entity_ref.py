"""Stable identity of the game entity a chunk describes."""

from __future__ import annotations

from dataclasses import dataclass

from poe2_rag.shared.domain._text import is_blank, is_slug
from poe2_rag.shared.domain.errors import InvalidEntityRefError

ENTITY_KINDS = frozenset({"active_gem", "support_gem", "mechanic", "item", "patch_notes"})


@dataclass(frozen=True, slots=True)
class EntityRef:
    """Version-independent reference to a game entity (gem, support, mechanic, item)."""

    kind: str
    slug: str
    name: str

    def __post_init__(self) -> None:
        if self.kind not in ENTITY_KINDS:
            raise InvalidEntityRefError(f"kind must be one of {sorted(ENTITY_KINDS)}, got {self.kind!r}")
        if is_blank(self.name):
            raise InvalidEntityRefError("name must be a non-blank string")
        if not is_slug(self.slug):
            raise InvalidEntityRefError(f"slug must match [a-z0-9_-]+, got {self.slug!r}")

    @property
    def key(self) -> str:
        """Stable identifier `<kind>:<slug>`, shared by every version of the entity."""
        return f"{self.kind}:{self.slug}"
