"""One known game patch and the day it was released."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from poe2_rag.shared.domain.errors import InvalidPatchCatalogError
from poe2_rag.shared.domain.patch_version import PatchVersion


@dataclass(frozen=True, slots=True)
class PatchRelease:
    """A known patch together with its release day.

    The catalog is built from these values, so a known patch without a date is not representable and
    "every known patch has exactly one date" holds by construction. `released_on` must be a `date`,
    not a `datetime`: a `datetime` is a `date` subclass, but comparing the two raises `TypeError`, so
    accepting one here would only move the failure to `current_at`.
    """

    version: PatchVersion
    released_on: date

    def __post_init__(self) -> None:
        if not isinstance(self.version, PatchVersion):
            raise InvalidPatchCatalogError(
                f"version must be a PatchVersion, got {self.version!r}"
            )
        if type(self.released_on) is not date:
            raise InvalidPatchCatalogError(
                f"released_on must be a date, got {self.released_on!r}"
            )
