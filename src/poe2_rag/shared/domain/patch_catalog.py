"""Port for the known patches and the current one."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import pairwise
from typing import Protocol, runtime_checkable

from poe2_rag.shared.domain.errors import InvalidPatchCatalogError
from poe2_rag.shared.domain.patch_version import PatchVersion


@runtime_checkable
class PatchCatalog(Protocol):
    """Single source of truth for which patches exist and which one is current."""

    def current(self) -> PatchVersion:
        """Return the patch the system treats as current."""
        ...

    def known(self) -> tuple[PatchVersion, ...]:
        """Return every known patch in ascending order, without duplicates."""
        ...

    def contains(self, version: PatchVersion) -> bool:
        """Return True when `version` is a known patch."""
        ...


class InMemoryPatchCatalog:
    """Catalog built from a fixed set of versions, used as the test fake and by other adapters."""

    __slots__ = ("_current", "_known")

    def __init__(self, versions: Iterable[PatchVersion], current: PatchVersion) -> None:
        known = _sorted_unique(versions)
        if not known:
            raise InvalidPatchCatalogError("catalog must not be empty")
        if not isinstance(current, PatchVersion):
            raise InvalidPatchCatalogError(f"current must be a PatchVersion, got {current!r}")
        if current not in known:
            raise InvalidPatchCatalogError(f"current version {current} is not one of the known versions")
        self._known = known
        self._current = current

    def current(self) -> PatchVersion:
        return self._current

    def known(self) -> tuple[PatchVersion, ...]:
        return self._known

    def contains(self, version: PatchVersion) -> bool:
        if not isinstance(version, PatchVersion):
            raise InvalidPatchCatalogError(f"version must be a PatchVersion, got {version!r}")
        return version in self._known


def _sorted_unique(versions: Iterable[PatchVersion]) -> tuple[PatchVersion, ...]:
    """Return `versions` sorted ascending, rejecting non-versions and repeated versions."""
    if not isinstance(versions, Iterable):
        raise InvalidPatchCatalogError(f"versions must be an iterable of PatchVersion, got {versions!r}")
    ordered = tuple(sorted(_require_versions(versions)))
    for previous, following in pairwise(ordered):
        if previous == following:
            raise InvalidPatchCatalogError(f"duplicate version {previous} in the catalog")
    return ordered


def _require_versions(versions: Iterable[PatchVersion]) -> tuple[PatchVersion, ...]:
    collected: list[PatchVersion] = []
    for version in versions:
        if not isinstance(version, PatchVersion):
            raise InvalidPatchCatalogError(f"versions must be PatchVersions, got {version!r}")
        collected.append(version)
    return tuple(collected)
