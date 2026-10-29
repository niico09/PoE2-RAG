"""Shared domain model: value objects and entities with no external dependencies."""

from poe2_rag.shared.domain.errors import DomainError, InvalidPatchVersionError, InvalidValidityError
from poe2_rag.shared.domain.patch_version import PatchVersion
from poe2_rag.shared.domain.validity import Validity

__all__ = [
    "DomainError",
    "InvalidPatchVersionError",
    "InvalidValidityError",
    "PatchVersion",
    "Validity",
]
