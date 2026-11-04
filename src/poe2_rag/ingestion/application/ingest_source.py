"""Orchestrate the write path end to end: one locator in, validated persisted chunks out (§5)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from poe2_rag.ingestion.application.errors import (
    ChunkIdentityConflictError,
    DuplicateChunkIdError,
    MixedSnapshotVersionError,
    OutOfOrderSnapshotError,
    UnknownPatchError,
)
from poe2_rag.ingestion.application.ports import (
    ChunkRepository,
    DocumentParser,
    SourceFetcher,
)
from poe2_rag.shared.domain import Chunk, PatchCatalog, PatchVersion, Validity

SnapshotIdentity = tuple[str, str]
"""What one snapshot is about: the source type plus the entity key.

`Chunk.chunk_id` is `<source.type>:<entity.key>@<valid_from>#<section>`, so everything before the `@`
is exactly this pair and every chunk already carries it: no new identifier is needed to know which
stored chunks belong to the same snapshot identity.
"""


@dataclass(frozen=True, slots=True)
class IngestReport:
    """What one `IngestSource.execute` run did with a document.

    `chunk_ids` are every id the document produced, in document order, whether new, updated or
    skipped. `skipped` counts the chunks already stored with an identical value, so a second run over
    an unchanged snapshot reports `skipped == len(chunk_ids)` and writes nothing. `closed` counts the
    stored chunks whose open range this run closed at the snapshot's version, and `removed` counts
    the stored chunks at that same version the snapshot no longer contains; both are zero for a
    first snapshot or a no-op re-run.
    """

    locator: str
    chunk_ids: tuple[str, ...]
    skipped: int
    closed: int = 0
    removed: int = 0


class IngestSource:
    """Fetch one source, parse it into chunks, validate them and persist them."""

    __slots__ = ("_catalog", "_fetcher", "_parser", "_repository")

    def __init__(
        self,
        fetcher: SourceFetcher,
        parser: DocumentParser,
        repository: ChunkRepository,
        catalog: PatchCatalog,
    ) -> None:
        self._fetcher = fetcher
        self._parser = parser
        self._repository = repository
        self._catalog = catalog

    def execute(self, locator: str) -> IngestReport:
        """Ingest the document at `locator`, superseding the previous snapshot of its entities.

        The order is fixed: fetch, parse, reject chunks whose `valid_from` is unknown to the catalog,
        reject duplicate `chunk_id` values, reject a document that describes one entity at two
        versions, then compare against the corpus. The comparison closes the stored open range of
        every incoming identity at the incoming version and removes the stored chunks of that
        identity at the same version the document no longer has; a stored version newer than the
        document is an out-of-order snapshot and stops the run. Every check runs before the first
        write, so a rejected document leaves the corpus byte-for-byte untouched.

        A `chunk_id` already stored from a different source URL is rejected as well, because
        upserting it would move the first document's citation to the second thread. A document whose
        chunks are all already stored and whose entity has nothing left to close is a no-op that
        reports them as skipped.
        """
        raw = self._fetcher.fetch(locator)
        chunks = tuple(self._parser.parse(raw))
        _require_known_patches(chunks, self._catalog)
        _require_distinct_ids(chunks)
        if not chunks:
            return IngestReport(locator, (), 0)
        snapshot = _snapshot_versions(chunks)
        stored = {chunk.chunk_id: chunk for chunk in self._repository.all()}
        _require_same_source(chunks, stored)
        closed = _close_superseded(stored, snapshot)
        removed = _removed_at_the_same_version(stored, snapshot, {chunk.chunk_id for chunk in chunks})
        changed = tuple(chunk for chunk in chunks if stored.get(chunk.chunk_id) != chunk)
        writes = changed + closed
        if writes:
            self._repository.save(writes)
        if removed:
            self._repository.remove(removed)
        return IngestReport(
            locator=locator,
            chunk_ids=tuple(chunk.chunk_id for chunk in chunks),
            skipped=len(chunks) - len(changed),
            closed=len(closed),
            removed=len(removed),
        )


def _identity(chunk: Chunk) -> SnapshotIdentity:
    """Return the snapshot identity `chunk` belongs to, as carried by its `chunk_id`."""
    return (chunk.source.type, chunk.entity.key)


def _snapshot_versions(chunks: Sequence[Chunk]) -> dict[SnapshotIdentity, PatchVersion]:
    """Map every identity in the document to its version, rejecting a document with two per identity."""
    versions: dict[SnapshotIdentity, PatchVersion] = {}
    for chunk in chunks:
        identity = _identity(chunk)
        version = chunk.validity.valid_from
        existing = versions.get(identity)
        if existing is not None and existing != version:
            raise MixedSnapshotVersionError(
                f"the document describes {identity[0]}:{identity[1]} at both {existing} and "
                f"{version}; one snapshot cannot hold two versions of one entity"
            )
        versions[identity] = version
    return versions


def _close_superseded(
    stored: dict[str, Chunk], snapshot: dict[SnapshotIdentity, PatchVersion]
) -> tuple[Chunk, ...]:
    """Return the stored chunks whose open range this snapshot closes, rejecting an older document.

    A stored chunk of an identity in the document must not reach past the snapshot version: a later
    `valid_from` or a `valid_until` beyond it both prove a newer snapshot is already stored, and
    closing anything would then overlap it. Open ranges that start earlier are closed at the snapshot
    version; a range that is already closed is history and stays as it is.
    """
    closed: list[Chunk] = []
    for chunk in stored.values():
        version = snapshot.get(_identity(chunk))
        if version is None:
            continue
        _require_not_newer(chunk, version)
        if chunk.validity.valid_from >= version:
            continue
        if chunk.validity.valid_until is None:
            closed.append(replace(chunk, validity=Validity(chunk.validity.valid_from, version)))
    return tuple(closed)


def _require_not_newer(chunk: Chunk, version: PatchVersion) -> None:
    """Raise `OutOfOrderSnapshotError` when `chunk` proves a version newer than `version` is stored."""
    reaches = chunk.validity.valid_from
    if chunk.validity.valid_until is not None and chunk.validity.valid_until > reaches:
        reaches = chunk.validity.valid_until
    if reaches > version:
        raise OutOfOrderSnapshotError(
            f"the document snapshots {chunk.source.type}:{chunk.entity.key} at {version}, but the "
            f"stored {chunk.chunk_id!r} already reaches {reaches}: a newer snapshot is stored, so "
            f"this one cannot be ingested"
        )


def _removed_at_the_same_version(
    stored: dict[str, Chunk],
    snapshot: dict[SnapshotIdentity, PatchVersion],
    incoming_ids: set[str],
) -> tuple[str, ...]:
    """Return the stored ids at the snapshot version that the document no longer contains.

    A section dropped at the same patch must not survive as current: the document is the whole
    snapshot of its entities, so an absent section was removed from the source. Ids at another
    version are history and are never dropped here.
    """
    removed: list[str] = []
    for chunk_id, chunk in stored.items():
        version = snapshot.get(_identity(chunk))
        if version is None or chunk.validity.valid_from != version:
            continue
        if chunk_id not in incoming_ids:
            removed.append(chunk_id)
    return tuple(removed)


def _require_known_patches(chunks: Sequence[Chunk], catalog: PatchCatalog) -> None:
    """Raise `UnknownPatchError` when a chunk starts at a patch the catalog does not contain."""
    for chunk in chunks:
        if not catalog.contains(chunk.validity.valid_from):
            raise UnknownPatchError(
                f"chunk {chunk.chunk_id!r} starts at {chunk.validity.valid_from}, "
                f"which is not a known patch"
            )


def _require_distinct_ids(chunks: Sequence[Chunk]) -> None:
    """Raise `DuplicateChunkIdError` when one document resolves two chunks to the same identity."""
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.chunk_id in seen:
            raise DuplicateChunkIdError(
                f"the document produced {chunk.chunk_id!r} more than once"
            )
        seen.add(chunk.chunk_id)


def _require_same_source(chunks: Sequence[Chunk], stored: dict[str, Chunk]) -> None:
    """Raise when a stored `chunk_id` belongs to another document URL.

    A re-capture of the same thread is an update, so only the source identity is compared: the
    capture date may change without the identity moving.
    """
    for chunk in chunks:
        existing = stored.get(chunk.chunk_id)
        if existing is None:
            continue
        if existing.source.type != chunk.source.type or existing.source.url != chunk.source.url:
            raise ChunkIdentityConflictError(
                f"chunk {chunk.chunk_id!r} is already stored from {existing.source.url!r} "
                f"and cannot be claimed by {chunk.source.url!r}"
            )
