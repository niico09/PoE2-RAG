"""Shared domain model: value objects and entities with no external dependencies."""

from poe2_rag.shared.domain._text import is_blank
from poe2_rag.shared.domain.chunk import Chunk
from poe2_rag.shared.domain.entity_ref import EntityRef
from poe2_rag.shared.domain.errors import (
    DomainError,
    InvalidChunkError,
    InvalidEntityRefError,
    InvalidModifierError,
    InvalidPatchCatalogError,
    InvalidPatchVersionError,
    InvalidSourceRefError,
    InvalidValidityError,
)
from poe2_rag.shared.domain.modifier import Modifier, ModifierKind
from poe2_rag.shared.domain.patch_catalog import InMemoryPatchCatalog, PatchCatalog
from poe2_rag.shared.domain.patch_version import PatchVersion
from poe2_rag.shared.domain.source_ref import SourceRef
from poe2_rag.shared.domain.validity import Validity

__all__ = [
    "Chunk",
    "is_blank",
    "DomainError",
    "EntityRef",
    "InMemoryPatchCatalog",
    "InvalidChunkError",
    "InvalidEntityRefError",
    "InvalidModifierError",
    "InvalidPatchCatalogError",
    "InvalidPatchVersionError",
    "InvalidSourceRefError",
    "InvalidValidityError",
    "Modifier",
    "ModifierKind",
    "PatchCatalog",
    "PatchVersion",
    "SourceRef",
    "Validity",
]
