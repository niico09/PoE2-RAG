"""Port for the known patches, the current one and the one current on a given day."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from itertools import pairwise
from typing import Protocol, runtime_checkable

from poe2_rag.shared.domain.errors import (
    InvalidPatchCatalogError,
    PatchNotReleasedError,
)
from poe2_rag.shared.domain.patch_release import PatchRelease
from poe2_rag.shared.domain.patch_version import PatchVersion


@runtime_checkable
class PatchCatalog(Protocol):
    """Single source of truth for which patches exist, which one is current and when each was released."""

    def current(self) -> PatchVersion:
        """Return the patch the system treats as current, as configured."""
        ...

    def current_at(self, day: date) -> PatchVersion:
        """Return the patch current on `day`: the latest release on or before it.

        Raise `PatchNotReleasedError` when `day` precedes every release, so no patch was current
        then, and `InvalidPatchCatalogError` when `day` is not a `date`. This is a historical
        question: it is never derived from the configured current patch.
        """
        ...

    def known(self) -> tuple[PatchVersion, ...]:
        """Return every known patch in ascending order, without duplicates."""
        ...

    def contains(self, version: PatchVersion) -> bool:
        """Return True when `version` is a known patch.

        Raise `InvalidPatchCatalogError` when `version` is not a `PatchVersion`.
        """
        ...


class InMemoryPatchCatalog:
    """Catalog built from a fixed set of releases, used as the test fake and by other adapters."""

    __slots__ = ("_current", "_known", "_releases")

    def __init__(self, releases: Iterable[PatchRelease], current: PatchVersion) -> None:
        ordered = _sorted_releases(releases)
        if not isinstance(current, PatchVersion):
            raise InvalidPatchCatalogError(f"current must be a PatchVersion, got {current!r}")
        known = tuple(release.version for release in ordered)
        if current not in known:
            raise InvalidPatchCatalogError(f"current version {current} is not one of the known versions")
        self._releases = ordered
        self._known = known
        self._current = current

    def current(self) -> PatchVersion:
        return self._current

    def current_at(self, day: date) -> PatchVersion:
        if type(day) is not date:
            raise InvalidPatchCatalogError(f"day must be a date, got {day!r}")
        selected: PatchRelease | None = None
        for release in self._releases:
            if release.released_on > day:
                break
            selected = release
        if selected is None:
            first = self._releases[0]
            raise PatchNotReleasedError(
                f"no patch was released on or before {day.isoformat()}: "
                f"the first known release is {first.version} on {first.released_on.isoformat()}"
            )
        return selected.version

    def known(self) -> tuple[PatchVersion, ...]:
        return self._known

    def contains(self, version: PatchVersion) -> bool:
        if not isinstance(version, PatchVersion):
            raise InvalidPatchCatalogError(f"version must be a PatchVersion, got {version!r}")
        return version in self._known


def _sorted_releases(releases: Iterable[PatchRelease]) -> tuple[PatchRelease, ...]:
    """Return `releases` sorted by version, rejecting anything a catalog cannot hold.

    Every release is validated as a `PatchRelease` (so it carries exactly one date), no version may
    repeat, and the release dates must strictly increase with the versions: two patches released the
    same day, or a newer version released earlier, would make "the patch current on a day" ambiguous.
    """
    if not isinstance(releases, Iterable):
        raise InvalidPatchCatalogError(
            f"releases must be an iterable of PatchRelease, got {releases!r}"
        )
    if isinstance(releases, str):
        raise InvalidPatchCatalogError(
            f"releases must be an iterable of PatchRelease, got {releases!r}"
        )
    ordered = tuple(sorted(_require_releases(releases), key=lambda release: release.version))
    if not ordered:
        raise InvalidPatchCatalogError("catalog must not be empty")
    for previous, following in pairwise(ordered):
        if previous.version == following.version:
            raise InvalidPatchCatalogError(
                f"duplicate version {previous.version} in the catalog"
            )
        if previous.released_on >= following.released_on:
            raise InvalidPatchCatalogError(
                f"release dates must strictly increase with the versions, but "
                f"{previous.version} was released on {previous.released_on.isoformat()} and "
                f"{following.version} on {following.released_on.isoformat()}"
            )
    return ordered


def _require_releases(releases: Iterable[PatchRelease]) -> tuple[PatchRelease, ...]:
    collected: list[PatchRelease] = []
    for release in releases:
        if not isinstance(release, PatchRelease):
            raise InvalidPatchCatalogError(
                f"releases must be PatchRelease values, got {release!r}"
            )
        collected.append(release)
    return tuple(collected)
