"""Adapter-specific behaviour of the BM25 index: scoring, persistence, atomicity and failures."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from poe2_rag.answering.adapters.outbound.lexical.bm25_index import (
    FILE_NAME,
    SCHEMA_VERSION,
    BlankQueryError,
    Bm25Index,
    DuplicateIndexEntryError,
    EmptyRebuildError,
    IndexCorruptError,
    IndexNotFoundError,
    InvalidLimitError,
    LexicalIndexError,
    LexicalIndexLoadError,
    LexicalIndexWriteError,
    UnsupportedIndexSchemaError,
)
from poe2_rag.answering.adapters.outbound.lexical.tokenizer import TOKENIZER_VERSION
from poe2_rag.answering.application import LexicalIndex
from poe2_rag.answering.domain import RetrievedChunk
from poe2_rag.ingestion.application import LexicalIndexWriter
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
START = PatchVersion.parse("0.1.0")

# Hand-computed reference for the three-document corpus below.
# N = 3, df(spark) = 2  ->  idf = ln(1 + (3 - 2 + 0.5) / (2 + 0.5)) = ln(1.6)
IDF_SPARK = 0.47000362924573563
# tf = 1, dl = avgdl   ->  1 * (k1 + 1) / (1 + k1 * (1 - b + b * 1)) = 2.5 / 2.5 = 1.0
# tf = 2, dl = avgdl   ->  2 * (k1 + 1) / (2 + k1)                   = 5.0 / 3.5


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


def build(directory: Path, chunks: Sequence[Chunk], **options: float) -> Bm25Index:
    """Rebuild an index from `chunks` and return it, ready to search."""
    index = Bm25Index(directory, **options)
    index.rebuild(chunks)
    return index


def sections(results: Sequence[RetrievedChunk]) -> list[str]:
    """Return the section of every result, in the order the index returned them."""
    return [item.chunk.section for item in results]


def hand_computed_corpus() -> tuple[Chunk, ...]:
    """Three documents of equal length, so `dl / avgdl` is 1 and the arithmetic stays exact.

    Every document has three text tokens plus the entity name (`Patch 0.1.0`, three tokens) and a
    one-token section, so every document is seven tokens long; the equal lengths cancel the length
    normalization instead of changing the numbers.
    """
    return (
        make_chunk("alpha", "spark fires lightning"),
        make_chunk("beta", "spark spark damage"),
        make_chunk("gamma", "lightning damage only"),
    )


class TestPortSurface:
    def test_implements_both_halves_of_the_lexical_index(self, tmp_path: Path) -> None:
        index = build(tmp_path, (make_chunk(),))

        assert isinstance(index, LexicalIndex)
        assert isinstance(index, LexicalIndexWriter)


class TestHandComputedScore:
    def test_scores_a_single_term_by_hand(self, tmp_path: Path) -> None:
        index = build(tmp_path, hand_computed_corpus())

        results = index.search("spark", START, 10)

        assert sections(results) == ["beta", "alpha"]
        assert results[0].scores.lexical == pytest.approx(IDF_SPARK * 5 / 3.5)
        assert results[1].scores.lexical == pytest.approx(IDF_SPARK * 1.0)

    def test_sums_the_idf_of_every_matching_term(self, tmp_path: Path) -> None:
        """All three documents are valid, so every match is scored and order follows the sums."""
        index = build(tmp_path, hand_computed_corpus())

        results = index.search("spark lightning", START, 10)

        assert sections(results) == ["alpha", "beta", "gamma"]
        assert results[0].scores.lexical == pytest.approx(2 * IDF_SPARK)
        assert results[1].scores.lexical == pytest.approx(IDF_SPARK * 5 / 3.5)
        assert results[2].scores.lexical == pytest.approx(IDF_SPARK)

    def test_a_term_absent_from_the_corpus_adds_nothing(self, tmp_path: Path) -> None:
        index = build(tmp_path, hand_computed_corpus())

        results = index.search("spark unknownterm", START, 10)

        assert [item.scores.lexical for item in results] == pytest.approx(
            [item.scores.lexical for item in index.search("spark", START, 10)]
        )


class TestIdf:
    def test_idf_counts_the_whole_corpus_not_only_the_valid_chunks(self, tmp_path: Path) -> None:
        """Filtering may not change the statistics: the same term must score the same on any patch."""
        corpus = (
            make_chunk("stale", "spark", valid_until="0.2.0"),
            make_chunk("beta", "spark", version="0.1.0"),
            make_chunk("gamma", "unrelated", version="0.1.0"),
        )
        index = build(tmp_path, corpus)

        (result,) = index.search("spark", TARGET, 10)

        # N = 3 and df(spark) = 2, as above; filtering before the score would have used N = 2 and
        # df = 1, whose idf is ln(2) = 0.693..., not ln(1.6).
        assert result.scores.lexical == pytest.approx(IDF_SPARK)


class TestRanking:
    def test_equal_scores_break_by_ascending_chunk_id(self, tmp_path: Path) -> None:
        corpus = (make_chunk("beta", "spark"), make_chunk("alpha", "spark"))
        index = build(tmp_path, corpus)

        results = index.search("spark", START, 10)

        assert sections(results) == ["alpha", "beta"]
        assert results[0].scores.lexical == results[1].scores.lexical

    def test_a_stale_top_hit_does_not_consume_a_limit_slot(self, tmp_path: Path) -> None:
        corpus = (
            make_chunk("stale", "spark spark spark spark", valid_until="0.2.0"),
            make_chunk("beta", "spark damage", version="0.2.0"),
        )
        index = build(tmp_path, corpus)

        results = index.search("spark", TARGET, 1)

        assert sections(results) == ["beta"]

    def test_respects_the_limit_after_filtering(self, tmp_path: Path) -> None:
        corpus = tuple(
            make_chunk(f"section-{position}", "spark damage") for position in range(3)
        )
        index = build(tmp_path, corpus)

        assert len(index.search("spark", START, 2)) == 2

    def test_excludes_chunks_with_no_matching_term(self, tmp_path: Path) -> None:
        index = build(tmp_path, hand_computed_corpus())

        assert index.search("zebra", START, 10) == ()

    def test_returns_only_the_lexical_score(self, tmp_path: Path) -> None:
        index = build(tmp_path, (make_chunk(),))

        (result,) = index.search("spark", START, 1)

        assert result.scores.lexical is not None
        assert result.scores.dense is None
        assert result.scores.fused is None
        assert result.scores.rerank is None


class TestParameters:
    def test_length_normalization_follows_the_configured_b(self, tmp_path: Path) -> None:
        corpus = (make_chunk("short", "spark"), make_chunk("long", "spark " + "filler " * 7))

        normalized = build(tmp_path / "normalized", corpus).search("spark", START, 10)
        unnormalized = build(tmp_path / "b-zero", corpus, b=0.0).search("spark", START, 10)

        assert sections(normalized) == ["short", "long"]
        assert sections(unnormalized) == ["long", "short"]
        assert unnormalized[0].scores.lexical == pytest.approx(unnormalized[1].scores.lexical)

    def test_term_frequency_saturation_follows_the_configured_k1(self, tmp_path: Path) -> None:
        """With k1 = 0 the frequency factor is 1, so a term that repeats does not score higher."""
        corpus = hand_computed_corpus()

        default = build(tmp_path / "default", corpus).search("spark", START, 10)
        saturated = build(tmp_path / "k1-zero", corpus, k1=0.0).search("spark", START, 10)

        assert sections(default) == ["beta", "alpha"]
        assert sections(saturated) == ["alpha", "beta"]
        assert saturated[0].scores.lexical == pytest.approx(saturated[1].scores.lexical)
        assert saturated[0].scores.lexical == pytest.approx(IDF_SPARK)

    def test_a_reader_scores_with_the_parameters_the_index_recorded(self, tmp_path: Path) -> None:
        """One file, one ranking: the recorded parameters win over the reader's defaults."""
        corpus = (make_chunk("short", "spark"), make_chunk("long", "spark " + "filler " * 7))
        build(tmp_path, corpus, b=0.0)

        results = Bm25Index(tmp_path).search("spark", START, 10)

        assert sections(results) == ["long", "short"]
        assert results[0].scores.lexical == pytest.approx(results[1].scores.lexical)

    @pytest.mark.parametrize(("k1", "b"), [(-0.1, 0.75), (1.5, -0.1), (1.5, 1.1)])
    def test_rejects_invalid_parameters(self, tmp_path: Path, k1: float, b: float) -> None:
        with pytest.raises(LexicalIndexError, match="k1|b"):
            Bm25Index(tmp_path, k1=k1, b=b)


class TestRebuildInput:
    def test_rejects_a_corpus_with_a_repeated_chunk_id(self, tmp_path: Path) -> None:
        """One identity, one document: two copies would surface as a duplicate in the query flow."""
        chunk = make_chunk()
        index = Bm25Index(tmp_path)

        with pytest.raises(DuplicateIndexEntryError, match="chunk_id"):
            index.rebuild((chunk, chunk))

        assert not (tmp_path / FILE_NAME).exists()

    def test_accepts_any_iterable_of_chunks(self, tmp_path: Path) -> None:
        index = Bm25Index(tmp_path)

        index.rebuild(chunk for chunk in (make_chunk("alpha", "spark damage"),))  # type: ignore[arg-type]

        assert sections(index.search("spark", START, 10)) == ["alpha"]

    def test_rejects_an_empty_iterable(self, tmp_path: Path) -> None:
        """An iterable has no truth value, so the guard has to look at what it produced."""
        index = Bm25Index(tmp_path)

        with pytest.raises(EmptyRebuildError):
            index.rebuild(chunk for chunk in ())  # type: ignore[arg-type]

        assert not (tmp_path / FILE_NAME).exists()


class TestPersistence:
    def test_writes_one_json_file_with_the_schema_and_the_fingerprint(self, tmp_path: Path) -> None:
        index = build(tmp_path, hand_computed_corpus())

        assert index.path == tmp_path / FILE_NAME
        payload = json.loads(index.path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["chunk_count"] == 3
        assert payload["corpus_fingerprint"].startswith("sha256:")
        assert payload["average_length"] == pytest.approx(7.0)
        terms = set(payload["document_frequencies"])
        # The corpus text is indexed...
        assert {"spark", "fires", "lightning", "damage", "only"} <= terms
        # ...and so are the entity name and the section, which is what lets a query for either meet.
        assert {"patch", "0.1", "0", "alpha", "beta", "gamma"} <= terms
        assert len(payload["documents"]) == 3

    def test_round_trips_every_chunk_field(self, tmp_path: Path) -> None:
        chunk = make_chunk(
            "skill-changes",
            text="Changed values:\n- Spark — 20% more damage\n- \"quoted\" text",
            valid_until="0.3.0",
            tags=("Spell", "Lightning"),
            modifiers=(Modifier("damage", 20.5, ModifierKind.MORE),),
        )
        build(tmp_path, (chunk,))

        (result,) = Bm25Index(tmp_path).search("spark", START, 1)

        assert result.chunk == chunk

    def test_records_the_tokenizer_version_and_the_scoring_parameters(self, tmp_path: Path) -> None:
        build(tmp_path, hand_computed_corpus(), k1=2.0, b=0.25)

        payload = payload_of(tmp_path)

        assert payload["tokenizer_version"] == TOKENIZER_VERSION
        assert payload["k1"] == 2.0
        assert payload["b"] == 0.25

    def test_two_rebuilds_of_one_corpus_produce_the_same_bytes(self, tmp_path: Path) -> None:
        first = build(tmp_path / "first", hand_computed_corpus())
        second = build(
            tmp_path / "second", tuple(reversed(hand_computed_corpus()))
        )

        assert first.path.read_bytes() == second.path.read_bytes()

    def test_a_rebuild_through_the_same_instance_is_visible_immediately(
        self, tmp_path: Path
    ) -> None:
        index = build(tmp_path, (make_chunk("alpha", "spark damage"),))

        index.rebuild((make_chunk("beta", "zebra"),))

        assert index.search("spark", START, 10) == ()
        assert sections(index.search("zebra", START, 10)) == ["beta"]

    def test_loading_is_lazy_so_construction_does_not_touch_the_disk(self, tmp_path: Path) -> None:
        index = Bm25Index(tmp_path)

        with pytest.raises(IndexNotFoundError):
            index.search("spark", START, 10)

    @pytest.mark.parametrize("schema_version", [0, 2, "1", None])
    def test_rejects_an_unknown_schema_version(
        self, tmp_path: Path, schema_version: object
    ) -> None:
        write_payload(tmp_path, {"schema_version": schema_version})

        with pytest.raises(UnsupportedIndexSchemaError):
            Bm25Index(tmp_path).search("spark", START, 10)


class TestLoadErrors:
    def test_a_missing_index_names_the_file(self, tmp_path: Path) -> None:
        with pytest.raises(IndexNotFoundError, match=FILE_NAME):
            Bm25Index(tmp_path).search("spark", START, 10)

    def test_a_non_json_index_is_corrupt(self, tmp_path: Path) -> None:
        (tmp_path / FILE_NAME).write_text("not json", encoding="utf-8")

        with pytest.raises(IndexCorruptError):
            Bm25Index(tmp_path).search("spark", START, 10)

    @pytest.mark.parametrize(
        "payload",
        [
            {"schema_version": SCHEMA_VERSION},
            {"schema_version": SCHEMA_VERSION, "documents": ["not an object"]},
            {"schema_version": SCHEMA_VERSION, "documents": [], "unexpected": True},
        ],
    )
    def test_a_structurally_wrong_index_is_corrupt(
        self, tmp_path: Path, payload: dict[str, Any]
    ) -> None:
        write_payload(tmp_path, payload)

        with pytest.raises(IndexCorruptError):
            Bm25Index(tmp_path).search("spark", START, 10)

    def test_a_negative_average_length_is_corrupt(self, tmp_path: Path) -> None:
        build(tmp_path, (make_chunk(),))
        payload = payload_of(tmp_path)
        payload["average_length"] = -3.0
        write_payload(tmp_path, payload)

        with pytest.raises(IndexCorruptError, match="average_length"):
            Bm25Index(tmp_path).search("spark", START, 10)

    def test_a_zero_average_length_is_corrupt(self, tmp_path: Path) -> None:
        build(tmp_path, (make_chunk(),))
        payload = payload_of(tmp_path)
        payload["average_length"] = 0.0
        write_payload(tmp_path, payload)

        with pytest.raises(IndexCorruptError, match="average_length"):
            Bm25Index(tmp_path).search("spark", START, 10)

    def test_a_document_frequency_above_the_corpus_size_is_corrupt(
        self, tmp_path: Path
    ) -> None:
        """A df above N makes idf negative and would drop a real match into an empty result."""
        build(tmp_path, (make_chunk(),))
        payload = payload_of(tmp_path)
        payload["document_frequencies"]["spark"] = 99
        write_payload(tmp_path, payload)

        with pytest.raises(IndexCorruptError, match="document_frequencies"):
            Bm25Index(tmp_path).search("spark", START, 10)

    def test_a_document_length_that_disagrees_with_its_frequencies_is_corrupt(
        self, tmp_path: Path
    ) -> None:
        build(tmp_path, (make_chunk(),))
        payload = payload_of(tmp_path)
        payload["documents"][0]["length"] += 1
        write_payload(tmp_path, payload)

        with pytest.raises(IndexCorruptError, match="length"):
            Bm25Index(tmp_path).search("spark", START, 10)

    def test_a_repeated_chunk_id_in_the_file_is_corrupt(self, tmp_path: Path) -> None:
        build(tmp_path, (make_chunk("alpha", "spark damage"),))
        payload = payload_of(tmp_path)
        payload["documents"].append(payload["documents"][0])
        payload["chunk_count"] = 2
        write_payload(tmp_path, payload)

        with pytest.raises(IndexCorruptError, match="chunk_id"):
            Bm25Index(tmp_path).search("spark", START, 10)

    def test_rejects_an_index_built_with_another_tokenizer_version(self, tmp_path: Path) -> None:
        """Terms stored by other tokenization rules must not answer queries tokenized by these."""
        build(tmp_path, (make_chunk(),))
        payload = payload_of(tmp_path)
        payload["tokenizer_version"] = TOKENIZER_VERSION + 1
        write_payload(tmp_path, payload)

        with pytest.raises(UnsupportedIndexSchemaError, match="tokenizer"):
            Bm25Index(tmp_path).search("spark", START, 10)

    def test_load_errors_are_environment_failures(self) -> None:
        assert issubclass(LexicalIndexLoadError, RuntimeError)
        for error in (IndexNotFoundError, UnsupportedIndexSchemaError, IndexCorruptError):
            assert issubclass(error, LexicalIndexLoadError)


class TestInputErrors:
    @pytest.mark.parametrize("text", ["", "   ", "\n\t", None])
    def test_rejects_blank_text_before_it_loads_the_index(
        self, tmp_path: Path, text: object
    ) -> None:
        with pytest.raises(BlankQueryError):
            Bm25Index(tmp_path).search(text, START, 10)  # type: ignore[arg-type]

    @pytest.mark.parametrize("limit", [0, -1])
    def test_rejects_a_non_positive_limit_before_it_loads_the_index(
        self, tmp_path: Path, limit: int
    ) -> None:
        with pytest.raises(InvalidLimitError):
            Bm25Index(tmp_path).search("spark", START, limit)

    def test_rejects_an_empty_rebuild_without_writing(self, tmp_path: Path) -> None:
        index = Bm25Index(tmp_path)

        with pytest.raises(EmptyRebuildError):
            index.rebuild(())

        assert not (tmp_path / FILE_NAME).exists()

    def test_input_errors_are_value_errors(self) -> None:
        assert issubclass(LexicalIndexError, ValueError)
        for error in (BlankQueryError, InvalidLimitError, EmptyRebuildError):
            assert issubclass(error, LexicalIndexError)


class TestCache:
    def test_another_instance_rebuild_is_visible_to_this_reader(self, tmp_path: Path) -> None:
        """A reindex from another instance must not leave this reader answering with the old index."""
        build(tmp_path, (make_chunk("alpha", "spark damage"),))
        reader = Bm25Index(tmp_path)
        assert sections(reader.search("spark", START, 10)) == ["alpha"]

        build(tmp_path, (make_chunk("beta", "zebra"),))

        assert reader.search("spark", START, 10) == ()
        assert sections(reader.search("zebra", START, 10)) == ["beta"]


class TestAtomicWrite:
    def test_a_failed_replace_keeps_the_previous_index(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index = build(tmp_path, (make_chunk("alpha", "spark damage"),))
        before = index.path.read_bytes()

        def fail(source: object, destination: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", fail)
        with pytest.raises(LexicalIndexWriteError, match=FILE_NAME):
            index.rebuild((make_chunk("beta", "zebra"),))

        assert index.path.read_bytes() == before
        assert [path.name for path in tmp_path.iterdir()] == [FILE_NAME]
        assert sections(index.search("spark", START, 10)) == ["alpha"]

    def test_a_failed_first_rebuild_leaves_no_index_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(source: object, destination: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", fail)
        with pytest.raises(LexicalIndexWriteError):
            Bm25Index(tmp_path).rebuild((make_chunk(),))

        assert list(tmp_path.iterdir()) == []


def payload_of(directory: Path) -> dict[str, Any]:
    """Return the payload an index wrote, so a test can corrupt exactly one detail of it."""
    return json.loads((directory / FILE_NAME).read_text(encoding="utf-8"))


def write_payload(directory: Path, payload: dict[str, Any]) -> None:
    """Write a payload as the index file, so a test controls exactly what the loader reads."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / FILE_NAME).write_text(json.dumps(payload), encoding="utf-8")
