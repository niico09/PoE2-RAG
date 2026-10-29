"""Shared domain model: value objects and entities with no external dependencies."""

from poe2_rag.shared.domain.chunk import Chunk
from poe2_rag.shared.domain.entity_ref import EntityRef
from poe2_rag.shared.domain.errors import (
    DomainError,
    InvalidChunkError,
    InvalidEntityRefError,
    InvalidModifierError,
    InvalidPatchVersionError,
    InvalidSourceRefError,
    InvalidValidityError,
)
from poe2_rag.shared.domain.modifier import Modifier, ModifierKind
from poe2_rag.shared.domain.patch_version import PatchVersion
from poe2_rag.shared.domain.source_ref import SourceRef
from poe2_rag.shared.domain.validity import Validity

__all__ = [
    "Chunk",
    "DomainError",
    "EntityRef",
    "InvalidChunkError",
    "InvalidEntityRefError",
    "InvalidModifierError",
    "InvalidPatchVersionError",
    "InvalidSourceRefError",
    "InvalidValidityError",
    "Modifier",
    "ModifierKind",
    "PatchVersion",
    "SourceRef",
    "Validity",
]
