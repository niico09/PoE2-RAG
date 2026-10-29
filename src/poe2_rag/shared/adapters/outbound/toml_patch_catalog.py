"""Patch catalog read from a TOML configuration file."""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from typing import Any

from poe2_rag.shared.domain.errors import InvalidPatchVersionError
from poe2_rag.shared.domain.patch_catalog import InMemoryPatchCatalog
from poe2_rag.shared.domain.patch_version import PatchVersion

_ROOT_KEYS = frozenset({"current", "patches"})
_PATCH_KEYS = frozenset({"version", "released_on"})


class PatchCatalogLoadError(RuntimeError):
    """Raised when a catalog file cannot be read or decoded.

    Deliberately not a `DomainError`: a broken file is an environment failure, not a violated
    invariant. Invariants violated by a file that did decode raise `InvalidPatchCatalogError`.
    """


class TomlPatchCatalog:
    """Catalog of the patches declared in a TOML file, validated before it is returned."""

    __slots__ = ("_catalog",)

    def __init__(self, path: str | Path) -> None:
        self._catalog = _load(Path(path))

    def current(self) -> PatchVersion:
        return self._catalog.current()

    def known(self) -> tuple[PatchVersion, ...]:
        return self._catalog.known()

    def contains(self, version: PatchVersion) -> bool:
        return self._catalog.contains(version)


def _load(path: Path) -> InMemoryPatchCatalog:
    """Read and decode `path` into a validated catalog, or raise without exposing anything partial."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise PatchCatalogLoadError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PatchCatalogLoadError(f"{path} is not valid TOML: {exc}") from exc
    return _build(path, data)


def _build(path: Path, data: dict[str, Any]) -> InMemoryPatchCatalog:
    """Turn decoded TOML data into a catalog, letting the domain reject invariant violations."""
    unknown = set(data) - _ROOT_KEYS
    if unknown:
        raise PatchCatalogLoadError(f"{path}: unknown key(s) {sorted(unknown)}")
    if "current" not in data:
        raise PatchCatalogLoadError(f"{path}: missing 'current'")
    current = _parse_current(path, data["current"])
    entries = data.get("patches", [])
    if not isinstance(entries, list):
        raise PatchCatalogLoadError(f"{path}: 'patches' must be an array of tables, got {entries!r}")
    versions = tuple(_entry_version(path, index, entry) for index, entry in enumerate(entries, start=1))
    return InMemoryPatchCatalog(versions, current)


def _parse_current(path: Path, raw: object) -> PatchVersion:
    if not isinstance(raw, str):
        raise PatchCatalogLoadError(f"{path}: 'current' must be a string, got {raw!r}")
    try:
        return PatchVersion.parse(raw)
    except InvalidPatchVersionError as exc:
        raise PatchCatalogLoadError(f"{path}: invalid 'current': {exc}") from exc


def _entry_version(path: Path, position: int, entry: object) -> PatchVersion:
    """Validate one `[[patches]]` table and return the version it declares."""
    where = f"{path}: patches[{position}]"
    if not isinstance(entry, dict):
        raise PatchCatalogLoadError(f"{where} must be a table, got {entry!r}")
    unknown = set(entry) - _PATCH_KEYS
    if unknown:
        raise PatchCatalogLoadError(f"{where}: unknown key(s) {sorted(unknown)}")
    if "version" not in entry:
        raise PatchCatalogLoadError(f"{where}: missing 'version'")
    released_on = entry.get("released_on")
    if released_on is not None and not isinstance(released_on, date):
        raise PatchCatalogLoadError(f"{where}: 'released_on' must be a TOML date, got {released_on!r}")
    raw = entry["version"]
    if not isinstance(raw, str):
        raise PatchCatalogLoadError(f"{where}: 'version' must be a string, got {raw!r}")
    try:
        return PatchVersion.parse(raw)
    except InvalidPatchVersionError as exc:
        raise PatchCatalogLoadError(f"{where}: invalid version: {exc}") from exc
