"""Chroma-backed dense vector index, validity-aware on read (ADR-0003).

The adapter is both a writer (`VectorIndexWriter`) and a reader (`VectorIndex`). Rebuilding creates
a fresh Chroma collection and swaps a pointer file atomically; searching trusts only the pointer,
so a failed rebuild leaves the previous collection answering.

The chunk codec is local to this module. The answering capability must not import an ingestion
adapter, so the JSON layout is mirrored here rather than reused from the chunk repository.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from collections.abc import Sequence
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import chromadb
from chromadb.api import ClientAPI
from chromadb.config import Settings
from chromadb.errors import ChromaError, NotFoundError

from poe2_rag.answering.adapters.outbound.dense.version_codec import (
    encode_validity,
    encode_version,
)
from poe2_rag.answering.domain import RetrievedChunk, StageScores
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    Modifier,
    ModifierKind,
    PatchVersion,
    SourceRef,
    Validity,
)

DEFAULT_DIRECTORY = Path("data/index/chroma")
DEFAULT_COLLECTION_NAME = "poe2_chunks"
SCHEMA_VERSION = 1


class VectorIndexError(ValueError):
    """Base class for caller-provided invalid arguments to the vector index."""


class InvalidVectorError(VectorIndexError):
    """Raised when a vector is not a finite float sequence of the configured dimension."""


class InvalidLimitError(VectorIndexError):
    """Raised when a search limit is not a positive integer."""


class EmptyRebuildError(VectorIndexError):
    """Raised when rebuild receives an empty corpus."""


class RebuildSizeMismatchError(VectorIndexError):
    """Raised when `chunks` and `vectors` have different lengths."""


class DuplicateIndexEntryError(VectorIndexError):
    """Raised when rebuild receives two chunks with the same `chunk_id`.

    The index is keyed by identity: storing one chunk twice would make `search` return it twice and
    the query flow reject the whole result (`DuplicateChunkError`). The corpus is rejected instead,
    before anything is written.
    """


class VectorIndexReadError(RuntimeError):
    """Base class for environment failures when reading the persisted index."""


class CollectionNotFoundError(VectorIndexReadError):
    """Raised when the pointer is missing or the named collection does not exist."""


class UnsupportedIndexSchemaError(VectorIndexReadError):
    """Raised when the index schema version is not supported."""


class EmbeddingModelMismatchError(VectorIndexReadError):
    """Raised when the configured embedder does not match the index metadata."""


class IndexCorruptError(VectorIndexReadError):
    """Raised when a stored record cannot be decoded or contradicts its id."""


class VectorIndexWriteError(RuntimeError):
    """Raised when the atomic rebuild fails; the previous index is left untouched."""


@runtime_checkable
class EmbedderIdentity(Protocol):
    """The pairing information the index stores and later checks against the reader's embedder."""

    @property
    def model_id(self) -> str:
        """Identifies the embedding model that produced the stored vectors."""
        ...

    @property
    def dimension(self) -> int:
        """The fixed vector dimension of that model."""
        ...


class ChromaVectorIndex:
    """Chroma persistent collection with atomic pointer-based swap and patch-validity filtering.

    Construction is lazy: no client is created and no file is touched until `rebuild` or `search` is
    called. The index stores the embedding model and dimension in the collection metadata so a
    reader configured with a different model fails loud instead of comparing incompatible vectors.
    """

    __slots__ = ("_directory", "_collection_name", "_embedder", "_batch_size", "_client")

    def __init__(
        self,
        directory: str | Path = DEFAULT_DIRECTORY,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        *,
        embedder: EmbedderIdentity,
        batch_size: int = 256,
    ) -> None:
        if batch_size <= 0:
            raise VectorIndexError(f"batch_size must be positive, got {batch_size!r}")
        self._directory = Path(directory)
        self._collection_name = collection_name
        self._embedder = embedder
        self._batch_size = batch_size
        self._client: ClientAPI | None = None

    def rebuild(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        """Replace the persisted index with `chunks` and their `vectors`, atomically.

        Inputs are materialized and validated before any collection is created: an empty corpus, a
        length mismatch, a repeated `chunk_id` or an invalid vector all stop the run with no disk
        side effects. Validity is encoded before collection creation too, so a version component
        above `999` leaves the store untouched.

        A new collection is built with a UUID-suffixed name, populated in batches, and then promoted
        by an atomic pointer write. Any failure before the pointer write deletes the half-built
        collection (best effort); only after the pointer is durable are stale collections removed.
        """
        materialized_chunks = list(chunks)
        materialized_vectors = list(vectors)
        if len(materialized_chunks) == 0:
            raise EmptyRebuildError("cannot rebuild the vector index from an empty corpus")
        if len(materialized_chunks) != len(materialized_vectors):
            raise RebuildSizeMismatchError(
                f"chunks ({len(materialized_chunks)}) and vectors ({len(materialized_vectors)}) "
                "must have the same length"
            )

        seen: set[str] = set()
        for chunk in materialized_chunks:
            if chunk.chunk_id in seen:
                raise DuplicateIndexEntryError(
                    f"the corpus holds chunk_id {chunk.chunk_id!r} more than once"
                )
            seen.add(chunk.chunk_id)

        # Encode metadata before touching Chroma so a version-component overflow leaves nothing
        # behind, and validate every vector while we are still safe to abort cleanly.
        records: list[tuple[str, list[float], dict[str, Any]]] = []
        for chunk, vector in zip(materialized_chunks, materialized_vectors, strict=True):
            self._validate_vector(vector)
            valid_from_code, valid_until_code = encode_validity(chunk.validity)
            records.append(
                (
                    chunk.chunk_id,
                    list(vector),
                    {
                        "chunk": json.dumps(_dump_chunk(chunk), ensure_ascii=False, sort_keys=True),
                        "valid_from_code": valid_from_code,
                        "valid_until_code": valid_until_code,
                    },
                )
            )

        client = self._client_or_create()
        new_collection_name = f"{self._collection_name}__{uuid.uuid4().hex}"
        try:
            collection = client.create_collection(
                name=new_collection_name,
                configuration={"hnsw": {"space": "cosine"}},
                metadata={
                    "index_schema_version": SCHEMA_VERSION,
                    "embedding_model": self._embedder.model_id,
                    "dimension": self._embedder.dimension,
                    "chunk_count": len(materialized_chunks),
                },
                embedding_function=None,
            )
        except ChromaError as exc:
            raise VectorIndexWriteError(
                f"cannot create collection {new_collection_name}: {exc}"
            ) from exc

        try:
            for start in range(0, len(records), self._batch_size):
                batch = records[start : start + self._batch_size]
                collection.add(
                    ids=[record[0] for record in batch],
                    embeddings=[record[1] for record in batch],
                    metadatas=[record[2] for record in batch],
                )
        except ChromaError as exc:
            with suppress(ChromaError):
                client.delete_collection(new_collection_name)
            raise VectorIndexWriteError(
                f"cannot add records to collection {new_collection_name}: {exc}"
            ) from exc

        pointer = {"schema_version": SCHEMA_VERSION, "collection": new_collection_name}
        try:
            self._write_pointer(pointer)
        except VectorIndexWriteError:
            # The new collection was built but never promoted: drop it, so only the collection the
            # pointer still names survives and a later rebuild does not inherit orphans.
            with suppress(ChromaError):
                client.delete_collection(new_collection_name)
            raise

        self._delete_stale_collections(client, new_collection_name)

    def search(
        self, vector: Sequence[float], version: PatchVersion, limit: int
    ) -> tuple[RetrievedChunk, ...]:
        """Return up to `limit` chunks nearest to `vector`, best first, with only `scores.dense` set.

        The query vector and limit are validated before the store is touched. The pointer names the
        current collection; if the pointer or collection is missing, or if the stored metadata does
        not match this reader's embedder, the call fails loud. Validity filtering runs inside the
        Chroma query so the limit is applied after stale records are removed.
        """
        self._validate_vector(vector)
        if type(limit) is not int or limit <= 0:
            raise InvalidLimitError(f"limit must be a positive int, got {limit!r}")
        version_code = encode_version(version)

        client = self._client_or_create()
        pointer = self._read_pointer()
        collection_name = pointer["collection"]
        try:
            collection = client.get_collection(name=collection_name, embedding_function=None)
        except NotFoundError as exc:
            raise CollectionNotFoundError(
                f"collection {collection_name!r} not found"
            ) from exc
        except ChromaError as exc:
            raise VectorIndexReadError(
                f"cannot read collection {collection_name!r}: {exc}"
            ) from exc

        metadata = collection.metadata or {}
        self._check_schema(metadata)
        self._check_embedder(metadata)

        try:
            raw = collection.query(
                query_embeddings=[list(vector)],
                n_results=limit,
                where={
                    "$and": [
                        {"valid_from_code": {"$lte": version_code}},
                        {"valid_until_code": {"$gt": version_code}},
                    ]
                },
                include=["metadatas", "distances"],
            )
        except ChromaError as exc:
            raise VectorIndexReadError(f"query failed: {exc}") from exc

        raw_ids = raw["ids"]
        raw_metadatas = raw["metadatas"]
        raw_distances = raw["distances"]
        if raw_ids is None or raw_metadatas is None or raw_distances is None:
            raise IndexCorruptError("query response is missing requested fields")
        ids = raw_ids[0]
        metadatas = raw_metadatas[0]
        distances = raw_distances[0]
        if metadatas is None or distances is None:
            raise IndexCorruptError("query response is missing requested metadata or distances")

        scored: list[tuple[RetrievedChunk, float, str]] = []
        for chunk_id, record_metadata, distance in zip(ids, metadatas, distances, strict=True):
            if not isinstance(record_metadata, dict):
                raise IndexCorruptError(f"record {chunk_id!r} has no metadata object")
            chunk_json = record_metadata.get("chunk")
            if not isinstance(chunk_json, str):
                raise IndexCorruptError(
                    f"stored chunk metadata must be a JSON string, got {type(chunk_json).__name__}"
                )
            try:
                chunk_data = json.loads(chunk_json)
            except json.JSONDecodeError as exc:
                raise IndexCorruptError(f"stored chunk metadata is not valid JSON: {exc}") from exc
            chunk = _build_chunk(chunk_data)
            if chunk.chunk_id != chunk_id:
                raise IndexCorruptError(
                    f"stored chunk_id {chunk.chunk_id!r} does not match record id {chunk_id!r}"
                )
            score = 1.0 - distance
            scored.append((RetrievedChunk(chunk, StageScores(dense=score)), score, chunk_id))

        scored.sort(key=lambda item: (-item[1], item[2]))
        return tuple(item[0] for item in scored)

    def _validate_vector(self, vector: Sequence[float]) -> None:
        """Reject a vector that is not a finite float sequence of exactly `embedder.dimension`."""
        if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence):
            raise InvalidVectorError(
                f"vector must be a sequence of numbers, got {type(vector).__name__}"
            )
        if len(vector) != self._embedder.dimension:
            raise InvalidVectorError(
                f"vector must have dimension {self._embedder.dimension}, got {len(vector)}"
            )
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidVectorError(f"vector values must be finite numbers, got {value!r}")
            if not math.isfinite(value):
                raise InvalidVectorError(f"vector values must be finite, got {value!r}")

    def _client_or_create(self) -> ClientAPI:
        """Return the cached client, creating a persistent one on first use."""
        if self._client is None:
            self._directory.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(self._directory),
                settings=Settings(anonymized_telemetry=False),
            )
        return self._client

    def _pointer_path(self) -> Path:
        """The JSON pointer that names the current collection."""
        return self._directory / f"{self._collection_name}.current.json"

    def _read_pointer(self) -> dict[str, Any]:
        """Read and validate the pointer file, or raise `CollectionNotFoundError`."""
        path = self._pointer_path()
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise CollectionNotFoundError(f"vector index not found: {path}") from exc
        except OSError as exc:
            raise CollectionNotFoundError(f"cannot read pointer {path}: {exc}") from exc

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CollectionNotFoundError(f"pointer {path} is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise CollectionNotFoundError(f"pointer {path} must contain a JSON object")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise UnsupportedIndexSchemaError(
                f"pointer {path}: unsupported schema_version {data.get('schema_version')!r}"
            )
        collection = data.get("collection")
        if not isinstance(collection, str):
            raise CollectionNotFoundError(
                f"pointer {path} must name a collection string"
            )
        return data

    def _write_pointer(self, pointer: dict[str, Any]) -> None:
        """Serialize `pointer` to a temporary file and replace the current one atomically."""
        self._directory.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            handle, temporary = tempfile.mkstemp(
                dir=self._directory,
                prefix=f".{self._collection_name}.current.json.",
                suffix=".tmp",
            )
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(pointer, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._pointer_path())
            temporary = None
        except OSError as exc:
            raise VectorIndexWriteError(f"cannot write pointer: {exc}") from exc
        finally:
            if temporary is not None:
                with suppress(OSError):
                    os.unlink(temporary)

    def _check_schema(self, metadata: dict[str, Any]) -> None:
        """Refuse a collection written by an incompatible index schema version."""
        stored_version = metadata.get("index_schema_version")
        if stored_version != SCHEMA_VERSION:
            raise UnsupportedIndexSchemaError(
                f"collection schema version {stored_version!r} is not supported"
            )

    def _check_embedder(self, metadata: dict[str, Any]) -> None:
        """Refuse a collection built by a different embedding model or dimension."""
        expected_model = self._embedder.model_id
        expected_dimension = self._embedder.dimension
        actual_model = metadata.get("embedding_model")
        actual_dimension = metadata.get("dimension")
        if actual_model != expected_model or actual_dimension != expected_dimension:
            raise EmbeddingModelMismatchError(
                f"index was built with model {actual_model!r} and dimension {actual_dimension!r}, "
                f"but this reader expects model {expected_model!r} and dimension {expected_dimension!r}"
            )

    def _delete_stale_collections(self, client: ClientAPI, keep: str) -> None:
        """Remove older collections with the same base name, ignoring individual failures."""
        prefix = f"{self._collection_name}__"
        for collection in client.list_collections():
            name = collection.name
            if name.startswith(prefix) and name != keep:
                with suppress(ChromaError):
                    client.delete_collection(name)


def _dump_chunk(chunk: Chunk) -> dict[str, Any]:
    """Return the JSON object that holds every field of `chunk`; `chunk_id` is derived, not stored.

    This mirrors `JsonChunkRepository` so a round-tripped chunk equals the original, while keeping
    the answering capability independent of the ingestion adapter.
    """
    return {
        "entity": {
            "kind": chunk.entity.kind,
            "slug": chunk.entity.slug,
            "name": chunk.entity.name,
        },
        "validity": {
            "valid_from": str(chunk.validity.valid_from),
            "valid_until": (
                None
                if chunk.validity.valid_until is None
                else str(chunk.validity.valid_until)
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


def _build_chunk(data: object) -> Chunk:
    """Decode one stored chunk, raising `IndexCorruptError` when the record is malformed."""
    if not isinstance(data, dict):
        raise IndexCorruptError(f"chunk metadata must be an object, got {data!r}")
    try:
        entity = data["entity"]
        validity = data["validity"]
        source = data["source"]
        tags = data["tags"]
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError(f"'tags' must be an array of strings, got {tags!r}")
        modifiers = data["modifiers"]
        if not isinstance(modifiers, list):
            raise ValueError(f"'modifiers' must be an array, got {modifiers!r}")
        valid_until = validity["valid_until"]
        return Chunk(
            entity=EntityRef(entity["kind"], entity["slug"], entity["name"]),
            validity=Validity(
                PatchVersion.parse(validity["valid_from"]),
                None if valid_until is None else PatchVersion.parse(valid_until),
            ),
            section=data["section"],
            text=data["text"],
            tags=tuple(tags),
            modifiers=tuple(
                Modifier(modifier["stat"], modifier["value"], ModifierKind(modifier["kind"]))
                for modifier in modifiers
            ),
            source=SourceRef(
                source["type"],
                source["url"],
                date.fromisoformat(source["captured_at"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IndexCorruptError(f"cannot decode chunk: {exc}") from exc
