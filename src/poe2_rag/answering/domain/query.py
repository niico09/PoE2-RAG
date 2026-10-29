"""A question plus the patch version its answer must be true for."""

from __future__ import annotations

from dataclasses import dataclass

from poe2_rag.answering.domain.errors import InvalidQueryError
from poe2_rag.shared.domain import is_blank
from poe2_rag.shared.domain.patch_catalog import PatchCatalog
from poe2_rag.shared.domain.patch_version import PatchVersion


@dataclass(frozen=True, slots=True)
class Query:
    """What the user asked and the patch it must be answered for (driver D1)."""

    text: str
    target_version: PatchVersion

    def __post_init__(self) -> None:
        if is_blank(self.text):
            raise InvalidQueryError("text must be a non-blank string")
        if not isinstance(self.target_version, PatchVersion):
            raise InvalidQueryError(f"target_version must be a PatchVersion, got {self.target_version!r}")

    @classmethod
    def for_catalog(
        cls, text: str, catalog: PatchCatalog, version: PatchVersion | None = None
    ) -> Query:
        """Build a query for `version`, or for the catalog's current patch when it is omitted.

        A version the catalog does not know is rejected: answering for a patch that does not exist
        would retrieve nothing while looking like a valid answer.
        """
        if not isinstance(catalog, PatchCatalog):
            raise InvalidQueryError(f"catalog must be a PatchCatalog, got {catalog!r}")
        if is_blank(text):
            raise InvalidQueryError("text must be a non-blank string")
        if version is not None and not isinstance(version, PatchVersion):
            raise InvalidQueryError(f"version must be a PatchVersion, got {version!r}")
        target = catalog.current() if version is None else version
        if not catalog.contains(target):
            raise InvalidQueryError(f"version {target} is not a known patch")
        return cls(text, target)
