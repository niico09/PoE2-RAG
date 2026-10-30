"""Chunk repository stored as one JSON file inside a directory.

The corpus is a repository artifact, not package data: `data/processed/` stays out of version
control because the repository is public and the scraped content is not redistributed (ADR-0002).
`JsonChunkRepository` receives the directory; the composition root owns how it is resolved, exactly
like `TomlPatchCatalog` receives the catalog path.

The file is replaced atomically (temporary file in the same directory plus `os.replace`), so a failed
write never leaves a truncated corpus: either the previous file survives or the new one is complete.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any

from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    Modifier,
    ModifierKind,
    PatchVersion,
    SourceRef,
    Validity,
)

SCHEMA_VERSION = 1
FILE_NAME = "chunks.json"
DEFAULT_DIRECTORY = Path("data/processed")

_ROOT_KEYS = frozenset({"schema_version", "chunks"})
_CHUNK_KEYS = frozenset({"entity", "validity", "section", "text", "tags", "modifiers", "source"})
_ENTITY_KEYS = frozenset({"kind", "slug", "name"})
_VALIDITY_KEYS = frozenset({"valid_from", "valid_until"})
_SOURCE_KEYS = frozenset({"type", "url", "captured_at"})
_MODIFIER_KEYS = frozenset({"stat", "value", "kind"})


class ChunkRepositoryLoadError(RuntimeError):
    """Raised when the corpus file cannot be read or decoded.

    Deliberately not a `DomainError`: a broken file is an environment failure, not a violated
    invariant. Invariants violated by a file that did decode still surface through the domain types
    as this error, because a corpus that cannot be rebuilt is not readable.
    """


class ChunkRepositoryWriteError(RuntimeError):
    """Raised when the corpus file cannot be replaced; the previous file is left untouched."""


class JsonChunkRepository:
    """Store the processed corpus in one JSON file under `directory`."""

    __slots__ = ("_path",)

    def __init__(self, directory: str | Path = DEFAULT_DIRECTORY) -> None:
        self._path = Path(directory) / FILE_NAME

    @property
    def path(self) -> Path:
        """The file this repository reads and writes."""
        return self._path

    def save(self, chunks: Sequence[Chunk]) -> None:
        """Upsert `chunks` into the corpus, preserving the position of an existing `chunk_id`.

        An empty sequence is a no-op: it neither reads nor rewrites the corpus. A read failure stops
        the save, because merging into a corpus that could not be decoded would drop stored chunks.
        """
        if not chunks:
            return
        stored = {chunk.chunk_id: chunk for chunk in self.all()}
        for chunk in chunks:
            stored[chunk.chunk_id] = chunk
        payload = {
            "schema_version": SCHEMA_VERSION,
            "chunks": [_dump_chunk(chunk) for chunk in stored.values()],
        }
        self._write(payload)

    def all(self) -> tuple[Chunk, ...]:
        """Return every chunk stored in the file, or nothing when the file does not exist yet."""
        if not self._path.exists():
            return ()
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ChunkRepositoryLoadError(f"cannot read {self._path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise ChunkRepositoryLoadError(f"{self._path} is not valid UTF-8: {exc}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ChunkRepositoryLoadError(f"{self._path} is not valid JSON: {exc}") from exc
        return _build_chunks(self._path, data)

    def _write(self, payload: dict[str, Any]) -> None:
        """Serialize `payload` to a temporary file and replace the corpus with it."""
        directory = self._path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ChunkRepositoryWriteError(f"cannot create {directory}: {exc}") from exc
        temporary: str | None = None
        try:
            handle, temporary = tempfile.mkstemp(
                dir=directory, prefix=f".{FILE_NAME}.", suffix=".tmp"
            )
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            temporary = None
        except OSError as exc:
            raise ChunkRepositoryWriteError(f"cannot write {self._path}: {exc}") from exc
        finally:
            if temporary is not None:
                with suppress(OSError):
                    os.unlink(temporary)


def _dump_chunk(chunk: Chunk) -> dict[str, Any]:
    """Return the JSON object that holds every field of `chunk`; `chunk_id` is derived, not stored."""
    return {
        "entity": {
            "kind": chunk.entity.kind,
            "slug": chunk.entity.slug,
            "name": chunk.entity.name,
        },
        "validity": {
            "valid_from": str(chunk.validity.valid_from),
            "valid_until": (
                None if chunk.validity.valid_until is None else str(chunk.validity.valid_until)
            ),
        },
        "section": chunk.section,
        "text": chunk.text,
        "tags": list(chunk.tags),
        "modifiers": [
            {"stat": modifier.stat, "value": modifier.value, "kind": modifier.kind.value}
            for modifier in chunk.modifiers
        ],
        "source": {
            "type": chunk.source.type,
            "url": chunk.source.url,
            "captured_at": chunk.source.captured_at.isoformat(),
        },
    }


def _build_chunks(path: Path, data: object) -> tuple[Chunk, ...]:
    """Decode the whole file or raise, so a caller never receives a partial corpus."""
    if not isinstance(data, dict):
        raise ChunkRepositoryLoadError(f"{path} must contain a JSON object")
    _require_keys(path, data, _ROOT_KEYS, "the file")
    version = data["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ChunkRepositoryLoadError(f"{path}: unsupported schema_version {version!r}")
    entries = data["chunks"]
    if not isinstance(entries, list):
        raise ChunkRepositoryLoadError(f"{path}: 'chunks' must be an array, got {entries!r}")
    chunks = tuple(_build_chunk(path, index, entry) for index, entry in enumerate(entries))
    _require_unique_ids(path, chunks)
    return chunks


def _require_unique_ids(path: Path, chunks: tuple[Chunk, ...]) -> None:
    """Reject a file that stores one `chunk_id` twice, so the corpus stays keyed by identity."""
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.chunk_id in seen:
            raise ChunkRepositoryLoadError(f"{path}: duplicate chunk_id {chunk.chunk_id!r}")
        seen.add(chunk.chunk_id)


def _build_chunk(path: Path, index: int, entry: object) -> Chunk:
    """Decode one stored chunk, reporting its position when the data is malformed."""
    where = f"{path}: chunks[{index}]"
    if not isinstance(entry, dict):
        raise ChunkRepositoryLoadError(f"{where} must be an object, got {entry!r}")
    _require_keys(path, entry, _CHUNK_KEYS, f"chunks[{index}]")
    try:
        entity = _require_object(path, entry["entity"], f"chunks[{index}].entity")
        _require_keys(path, entity, _ENTITY_KEYS, f"chunks[{index}].entity")
        validity = _require_object(path, entry["validity"], f"chunks[{index}].validity")
        _require_keys(path, validity, _VALIDITY_KEYS, f"chunks[{index}].validity")
        source = _require_object(path, entry["source"], f"chunks[{index}].source")
        _require_keys(path, source, _SOURCE_KEYS, f"chunks[{index}].source")
        tags = entry["tags"]
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError(f"'tags' must be an array of strings, got {tags!r}")
        modifiers = entry["modifiers"]
        if not isinstance(modifiers, list):
            raise ValueError(f"'modifiers' must be an array, got {modifiers!r}")
        valid_until = validity["valid_until"]
        return Chunk(
            entity=EntityRef(entity["kind"], entity["slug"], entity["name"]),
            validity=Validity(
                PatchVersion.parse(validity["valid_from"]),
                None if valid_until is None else PatchVersion.parse(valid_until),
            ),
            section=entry["section"],
            text=entry["text"],
            tags=tuple(tags),
            modifiers=tuple(
                _build_modifier(path, index, position, modifier)
                for position, modifier in enumerate(modifiers)
            ),
            source=SourceRef(
                source["type"],
                source["url"],
                date.fromisoformat(source["captured_at"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ChunkRepositoryLoadError(f"{where}: {exc}") from exc


def _build_modifier(path: Path, index: int, position: int, entry: object) -> Modifier:
    """Decode one stored modifier, keeping the field checks inside the domain type."""
    where = f"chunks[{index}].modifiers[{position}]"
    modifier = _require_object(path, entry, where)
    _require_keys(path, modifier, _MODIFIER_KEYS, where)
    return Modifier(modifier["stat"], modifier["value"], ModifierKind(modifier["kind"]))


def _require_object(path: Path, value: object, where: str) -> dict[str, Any]:
    """Return `value` as an object or raise a load error that names `where`."""
    if not isinstance(value, dict):
        raise ChunkRepositoryLoadError(f"{path}: {where} must be an object, got {value!r}")
    return value


def _require_keys(path: Path, data: dict[str, Any], expected: frozenset[str], where: str) -> None:
    """Require exactly `expected` keys in `data`, so a misspelled field fails closed."""
    unknown = set(data) - expected
    if unknown:
        raise ChunkRepositoryLoadError(f"{path}: {where} has unknown key(s) {sorted(unknown)}")
    missing = expected - set(data)
    if missing:
        raise ChunkRepositoryLoadError(f"{path}: {where} is missing key(s) {sorted(missing)}")
