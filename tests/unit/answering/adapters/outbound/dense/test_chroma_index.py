"""Adapter-specific behaviour of the Chroma dense index: storage, atomicity and failures."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import chromadb
import pytest
from chromadb.config import Settings
from chromadb.errors import InternalError

from poe2_rag.answering.adapters.outbound.dense.chroma_index import (
    ChromaVectorIndex,
    CollectionNotFoundError,
    DuplicateIndexEntryError,
    EmbeddingModelMismatchError,
    EmptyRebuildError,
    IndexCorruptError,
    InvalidLimitError,
    InvalidVectorError,
    RebuildSizeMismatchError,
    UnsupportedIndexSchemaError,
    VectorIndexError,
    VectorIndexReadError,
    VectorIndexWriteError,
)
from poe2_rag.answering.application import VectorIndex
from poe2_rag.answering.domain import RetrievedChunk
from poe2_rag.ingestion.application import VectorIndexWriter
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    Modifier,
    ModifierKind,
    PatchVersion,
    SourceRef,
    Validity,
)

THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
CAPTURED_AT = date(2026, 10, 29)
TARGET = PatchVersion.parse("0.2.0")


class _HashingEmbedder:
    """Local deterministic fake embedder, mirroring `tests/contract/hashing_embedder.py`."""

    model_id: str = "fake-hashed-bow"
    dimension: int = 64

    def embed(self, text: str) -> tuple[float, ...]:
        return self._embed(text)

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._embed(text) for text in texts)

    def overflowing_indices(self, texts: Sequence[str]) -> tuple[int, ...]:
        return ()

    def _embed(self, text: str) -> tuple[float, ...]:
        import hashlib
        import math
        import re

        vector = [0.0] * self.dimension
        words = re.findall(r"[a-z0-9]+", text.lower())
        for word in words:
            first = hashlib.sha256(word.encode("utf-8")).digest()
            second = hashlib.sha256(first).digest()
            for index, byte in enumerate(first + second):
                vector[index] += byte / 255.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return tuple(vector)
        return tuple(value / norm for value in vector)


HashingEmbedder = _HashingEmbedder


def make_chunk(
    section: str = "skill-changes",
    text: str = "Spark damage.",
    version: str = "0.1.0",
    *,
    valid_until: str | None = None,
    tags: tuple[str, ...] = (),
    modifiers: tuple[Modifier, ...] = (),
) -> Chunk:
    """Build one chunk; the entity slug follows the patch so distinct ranges stay distinct."""
    parsed = PatchVersion.parse(version)
    slug = f"{parsed.major}-{parsed.minor}-{parsed.patch}"
    return Chunk(
        entity=EntityRef("patch_notes", slug, f"Patch {parsed}"),
        validity=Validity(
            parsed, None if valid_until is None else PatchVersion.parse(valid_until)
        ),
        section=section,
        text=text,
        source=SourceRef("patch_notes", THREAD_URL, CAPTURED_AT),
        tags=tags,
        modifiers=modifiers,
    )


def build(directory: Path, chunks: tuple[Chunk, ...], *, batch_size: int = 256) -> ChromaVectorIndex:
    """Rebuild a Chroma index from `chunks` and return it, ready to search."""
    embedder = HashingEmbedder()
    index = ChromaVectorIndex(directory, embedder=embedder, batch_size=batch_size)
    vectors = embedder.embed_documents([chunk.text for chunk in chunks])
    index.rebuild(chunks, vectors)
    return index


def chunk_ids(results: Sequence[RetrievedChunk]) -> list[str]:
    """Return the ids of one result, in the order the index returned them."""
    return [item.chunk.chunk_id for item in results]


class TestPortSurface:
    def test_implements_both_halves_of_the_vector_index(self, tmp_path: Path) -> None:
        index = build(tmp_path, (make_chunk(),))

        assert isinstance(index, VectorIndex)
        assert isinstance(index, VectorIndexWriter)

    def test_construction_is_lazy_and_does_not_touch_disk(self, tmp_path: Path) -> None:
        index = ChromaVectorIndex(tmp_path, embedder=HashingEmbedder())

        with pytest.raises(CollectionNotFoundError):
            index.search(HashingEmbedder().embed("spark"), TARGET, 10)


class TestSearch:
    def test_rebuild_then_search_returns_the_chunk_with_only_dense_score(
        self, tmp_path: Path
    ) -> None:
        chunk = make_chunk()
        index = build(tmp_path, (chunk,))
        query_vector = HashingEmbedder().embed(chunk.text)

        (result,) = index.search(query_vector, TARGET, 10)

        assert result.chunk == chunk
        assert result.scores.dense is not None
        assert result.scores.dense > 0
        assert result.scores.lexical is None
        assert result.scores.fused is None
        assert result.scores.rerank is None

    def test_equal_scores_break_by_ascending_chunk_id(self, tmp_path: Path) -> None:
        alpha = make_chunk("alpha", text="Spark damage.")
        beta = make_chunk("beta", text="Spark damage.")
        index = build(tmp_path, (alpha, beta))
        query_vector = HashingEmbedder().embed("Spark damage.")

        results = index.search(query_vector, TARGET, 10)

        assert chunk_ids(results) == [alpha.chunk_id, beta.chunk_id]
        assert results[0].scores.dense == pytest.approx(results[1].scores.dense)

    def test_a_chunk_round_trips_every_field(self, tmp_path: Path) -> None:
        chunk = make_chunk(
            "skill-changes",
            text="Changed values:\n- Spark — 20% more damage\n- \"quoted\" text",
            valid_until="0.3.0",
            tags=("Spell", "Lightning"),
            modifiers=(Modifier("damage", 20.5, ModifierKind.MORE),),
        )
        index = build(tmp_path, (chunk,))
        query_vector = HashingEmbedder().embed(chunk.text)

        (result,) = index.search(query_vector, TARGET, 1)

        assert result.chunk == chunk

    def test_a_second_rebuild_replaces_the_index(self, tmp_path: Path) -> None:
        old_chunk = make_chunk("old", text="Spark damage.")
        new_chunk = make_chunk("new", text="Lightning damage.")
        embedder = HashingEmbedder()
        index = build(tmp_path, (old_chunk,))

        index.rebuild((new_chunk,), (embedder.embed(new_chunk.text),))

        assert old_chunk.chunk_id not in chunk_ids(
            index.search(embedder.embed(old_chunk.text), TARGET, 10)
        )
        assert chunk_ids(index.search(embedder.embed(new_chunk.text), TARGET, 10)) == [
            new_chunk.chunk_id
        ]

    def test_batching_adds_all_chunks(self, tmp_path: Path) -> None:
        chunks = tuple(make_chunk(f"section-{index}", text=f"Spark damage {index}.") for index in range(5))
        index = build(tmp_path, chunks, batch_size=2)
        query_vector = HashingEmbedder().embed("Spark damage")

        results = index.search(query_vector, TARGET, 10)

        assert len(results) == 5


class TestPersistence:
    def test_persistence_round_trip_with_a_new_instance(self, tmp_path: Path) -> None:
        chunk = make_chunk()
        build(tmp_path, (chunk,))
        query_vector = HashingEmbedder().embed(chunk.text)

        second_index = ChromaVectorIndex(tmp_path, embedder=HashingEmbedder())
        (result,) = second_index.search(query_vector, TARGET, 1)

        assert result.chunk == chunk

    def test_a_never_rebuilt_directory_raises_collection_not_found(
        self, tmp_path: Path
    ) -> None:
        index = ChromaVectorIndex(tmp_path, embedder=HashingEmbedder())

        with pytest.raises(CollectionNotFoundError):
            index.search(HashingEmbedder().embed("spark"), TARGET, 10)

    def test_a_deleted_collection_behind_a_valid_pointer_raises(
        self, tmp_path: Path
    ) -> None:
        index = build(tmp_path, (make_chunk(),))
        pointer_path = tmp_path / "poe2_chunks.current.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        client = chromadb.PersistentClient(
            path=str(tmp_path), settings=Settings(anonymized_telemetry=False)
        )
        client.delete_collection(pointer["collection"])

        with pytest.raises(CollectionNotFoundError):
            index.search(HashingEmbedder().embed("Spark damage."), TARGET, 10)


class TestEmbedderValidation:
    def test_model_mismatch_raises(self, tmp_path: Path) -> None:
        build(tmp_path, (make_chunk(),))

        wrong_embedder = _FakeEmbedder("other-model", 64)
        index = ChromaVectorIndex(tmp_path, embedder=wrong_embedder)

        with pytest.raises(EmbeddingModelMismatchError, match="other-model|fake-hashed-bow"):
            index.search([0.0] * 64, TARGET, 10)

    def test_dimension_mismatch_raises(self, tmp_path: Path) -> None:
        build(tmp_path, (make_chunk(),))

        wrong_embedder = _FakeEmbedder("fake-hashed-bow", 32)
        index = ChromaVectorIndex(tmp_path, embedder=wrong_embedder)

        with pytest.raises(EmbeddingModelMismatchError, match="32|64"):
            index.search([0.0] * 32, TARGET, 10)


class TestQueryValidation:
    def test_query_vector_of_wrong_length_raises(self, tmp_path: Path) -> None:
        index = build(tmp_path, (make_chunk(),))

        with pytest.raises(InvalidVectorError, match="dimension"):
            index.search([0.0] * 32, TARGET, 10)

    def test_query_vector_with_nan_raises(self, tmp_path: Path) -> None:
        index = build(tmp_path, (make_chunk(),))
        vector = [0.0] * 64
        vector[0] = float("nan")

        with pytest.raises(InvalidVectorError, match="finite"):
            index.search(vector, TARGET, 10)

    def test_limit_must_be_positive(self, tmp_path: Path) -> None:
        index = build(tmp_path, (make_chunk(),))

        with pytest.raises(InvalidLimitError):
            index.search(HashingEmbedder().embed("spark"), TARGET, 0)

        with pytest.raises(InvalidLimitError):
            index.search(HashingEmbedder().embed("spark"), TARGET, -1)


class TestRebuildInput:
    def test_rejects_an_empty_corpus(self, tmp_path: Path) -> None:
        index = ChromaVectorIndex(tmp_path, embedder=HashingEmbedder())

        with pytest.raises(EmptyRebuildError):
            index.rebuild((), ())

        assert not (tmp_path / "poe2_chunks.current.json").exists()

    def test_rejects_a_length_mismatch(self, tmp_path: Path) -> None:
        index = ChromaVectorIndex(tmp_path, embedder=HashingEmbedder())
        chunk = make_chunk()

        with pytest.raises(RebuildSizeMismatchError, match="chunks|vectors"):
            index.rebuild((chunk,), ())

        assert not (tmp_path / "poe2_chunks.current.json").exists()

    def test_rejects_a_repeated_chunk_id(self, tmp_path: Path) -> None:
        index = ChromaVectorIndex(tmp_path, embedder=HashingEmbedder())
        chunk = make_chunk()

        with pytest.raises(DuplicateIndexEntryError, match="chunk_id"):
            index.rebuild((chunk, chunk), ([0.0] * 64, [0.0] * 64))

        assert not (tmp_path / "poe2_chunks.current.json").exists()

    def test_rejects_a_vector_with_wrong_dimension(self, tmp_path: Path) -> None:
        index = ChromaVectorIndex(tmp_path, embedder=HashingEmbedder())
        chunk = make_chunk()

        with pytest.raises(InvalidVectorError, match="dimension"):
            index.rebuild((chunk,), ([0.0] * 32,))

        assert not (tmp_path / "poe2_chunks.current.json").exists()


class TestErrorTaxonomy:
    def test_base_caller_error_is_value_error(self) -> None:
        assert issubclass(VectorIndexError, ValueError)

    def test_caller_errors_are_value_errors(self) -> None:
        assert issubclass(InvalidVectorError, ValueError)
        assert issubclass(InvalidLimitError, ValueError)
        assert issubclass(EmptyRebuildError, ValueError)
        assert issubclass(RebuildSizeMismatchError, ValueError)
        assert issubclass(DuplicateIndexEntryError, ValueError)

    def test_read_errors_are_runtime_errors(self) -> None:
        assert issubclass(CollectionNotFoundError, RuntimeError)
        assert issubclass(UnsupportedIndexSchemaError, RuntimeError)
        assert issubclass(EmbeddingModelMismatchError, RuntimeError)
        assert issubclass(IndexCorruptError, RuntimeError)
        assert issubclass(VectorIndexReadError, RuntimeError)

    def test_write_error_is_runtime_error(self) -> None:
        assert issubclass(VectorIndexWriteError, RuntimeError)


class TestAtomicity:
    def test_a_version_component_above_999_leaves_previous_index_intact(
        self, tmp_path: Path
    ) -> None:
        embedder = HashingEmbedder()
        index = ChromaVectorIndex(tmp_path, embedder=embedder)
        good_chunk = make_chunk()
        index.rebuild((good_chunk,), (embedder.embed(good_chunk.text),))

        bad_chunk = make_chunk("overflow", version="1000.0.0")
        with pytest.raises(ValueError, match="999"):
            index.rebuild((bad_chunk,), (embedder.embed(bad_chunk.text),))

        results = index.search(embedder.embed(good_chunk.text), TARGET, 1)
        assert chunk_ids(results) == [good_chunk.chunk_id]

    def test_a_failed_add_leaves_the_previous_index_answering(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        embedder = HashingEmbedder()
        index = ChromaVectorIndex(tmp_path, embedder=embedder)
        old_chunk = make_chunk("old", text="Spark damage.")
        index.rebuild((old_chunk,), (embedder.embed(old_chunk.text),))

        def failing_add(*args: object, **kwargs: object) -> None:
            raise InternalError("injected add failure")

        monkeypatch.setattr(
            "chromadb.api.models.Collection.Collection.add", failing_add
        )

        new_chunk = make_chunk("new", text="Lightning damage.")
        with pytest.raises(VectorIndexWriteError):
            index.rebuild((new_chunk,), (embedder.embed(new_chunk.text),))

        results = index.search(embedder.embed("Spark damage."), TARGET, 1)
        assert chunk_ids(results) == [old_chunk.chunk_id]

    def test_a_failed_pointer_write_leaves_the_previous_index_answering_and_no_orphan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The new collection is only promoted once the pointer is durable; a failure drops it."""
        embedder = HashingEmbedder()
        index = ChromaVectorIndex(tmp_path, embedder=embedder)
        old_chunk = make_chunk("old", text="Spark damage.")
        index.rebuild((old_chunk,), (embedder.embed(old_chunk.text),))

        def failing_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
            raise OSError("injected pointer failure")

        monkeypatch.setattr(
            "poe2_rag.answering.adapters.outbound.dense.chroma_index.tempfile.mkstemp",
            failing_mkstemp,
        )

        new_chunk = make_chunk("new", text="Lightning damage.")
        with pytest.raises(VectorIndexWriteError, match="pointer"):
            index.rebuild((new_chunk,), (embedder.embed(new_chunk.text),))

        results = index.search(embedder.embed("Spark damage."), TARGET, 1)
        assert chunk_ids(results) == [old_chunk.chunk_id]
        client = chromadb.PersistentClient(
            path=str(tmp_path), settings=Settings(anonymized_telemetry=False)
        )
        assert len(client.list_collections()) == 1


@dataclass(frozen=True)
class _FakeEmbedder:
    """Stand-in embedder to exercise model/dimension mismatch checks."""

    model_id: str
    dimension: int

    def embed(self, text: str) -> tuple[float, ...]:
        return tuple([0.0] * self.dimension)

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(tuple([0.0] * self.dimension) for _ in texts)

    def overflowing_indices(self, texts: Sequence[str]) -> tuple[int, ...]:
        return ()
