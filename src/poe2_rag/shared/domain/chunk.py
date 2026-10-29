"""Indexable unit of game knowledge."""

from __future__ import annotations

from dataclasses import dataclass

from poe2_rag.shared.domain._text import is_blank, is_slug
from poe2_rag.shared.domain.entity_ref import EntityRef
from poe2_rag.shared.domain.errors import InvalidChunkError
from poe2_rag.shared.domain.modifier import Modifier
from poe2_rag.shared.domain.patch_version import PatchVersion
from poe2_rag.shared.domain.source_ref import SourceRef
from poe2_rag.shared.domain.validity import Validity


@dataclass(frozen=True, slots=True)
class Chunk:
    """A section of text about one entity, valid for a patch range and traceable to its source."""

    entity: EntityRef
    validity: Validity
    section: str
    text: str
    source: SourceRef
    tags: tuple[str, ...] = ()
    modifiers: tuple[Modifier, ...] = ()

    def __post_init__(self) -> None:
        self._require_type("entity", EntityRef)
        self._require_type("validity", Validity)
        self._require_type("source", SourceRef)
        if not is_slug(self.section):
            raise InvalidChunkError(f"section must match [a-z0-9_-]+, got {self.section!r}")
        if is_blank(self.text):
            raise InvalidChunkError("text must be a non-blank string")
        if not isinstance(self.tags, tuple) or any(is_blank(tag) for tag in self.tags):
            raise InvalidChunkError(f"tags must be a tuple of non-blank strings, got {self.tags!r}")
        if not isinstance(self.modifiers, tuple) or not all(isinstance(m, Modifier) for m in self.modifiers):
            raise InvalidChunkError(f"modifiers must be a tuple of Modifier, got {self.modifiers!r}")

    def _require_type(self, field: str, expected: type) -> None:
        value = getattr(self, field)
        if not isinstance(value, expected):
            raise InvalidChunkError(f"{field} must be a {expected.__name__}, got {value!r}")

    @property
    def chunk_id(self) -> str:
        """Stable id `<kind>:<slug>@<valid_from>#<section>`, unique per entity, version and section."""
        return f"{self.entity.key}@{self.validity.valid_from}#{self.section}"

    def is_valid_at(self, version: PatchVersion) -> bool:
        """Return True when this chunk applies to `version`.

        Raise `InvalidValidityError` when `version` is not a `PatchVersion`.
        """
        return self.validity.is_valid_at(version)
