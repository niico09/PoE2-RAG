"""Contract every `ChunkRepository` implementation must honour (architecture §8).

The in-memory implementation in this module is the contract's executable specification: the JSON
adapter is accepted only when it passes the same scenarios. Both are built through one factory, so a
test that passes for one and fails for the other points at the adapter, not at the test.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

import pytest

from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)
from poe2_rag.ingestion.application import ChunkRepository
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    Modifier,
    ModifierKind,
    PatchVersion,
    SourceRef,
    Validity,
)

BuildRepository = Callable[[], ChunkRepository]

THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
CAPTURED_AT = date(2026, 10, 29)


class InMemoryChunkRepository:
    """Reference implementation of the port: a chunk_id-keyed store with insertion order."""

    def __init__(self) -> None:
        self._by_id: dict[str, Chunk] = {}

    def save(self, chunks: Sequence[Chunk]) -> None:
        for chunk in chunks:
            self._by_id[chunk.chunk_id] = chunk

    def all(self) -> tuple[Chunk, ...]:
        return tuple(self._by_id.values())


def make_chunk(
    section: str = "general-changes",
    version: str = "0.1.0",
    *,
    text: str = "Notes line one.\n- bullet line",
    valid_until: str | None = None,
    tags: tuple[str, ...] = (),
    modifiers: tuple[Modifier, ...] = (),
) -> Chunk:
    """Build a chunk that exercises every stored field, including the optional ones."""
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


@pytest.fixture(params=["in-memory", "json"])
def build_repository(request: pytest.FixtureRequest, tmp_path: Path) -> BuildRepository:
    """Build either implementation fresh for one test, so one suite proves both."""
    if request.param == "in-memory":
        return InMemoryChunkRepository
    directory = tmp_path / "data" / "processed"
    return lambda: JsonChunkRepository(directory)


class TestPortSurface:
    def test_is_a_chunk_repository(self, build_repository: BuildRepository) -> None:
        assert isinstance(build_repository(), ChunkRepository)


class TestEmptyRepository:
    def test_a_fresh_repository_has_no_chunks(self, build_repository: BuildRepository) -> None:
        assert build_repository().all() == ()


class TestRoundTrip:
    def test_round_trips_every_chunk_field(self, build_repository: BuildRepository) -> None:
        chunk = make_chunk(
            text="Changed values:\n- Spark — 20% more damage\n- \"quoted\" text",
            valid_until="0.2.0",
            tags=("Spell", "Lightning"),
            modifiers=(
                Modifier("mana_cost", 12, ModifierKind.FLAT),
                Modifier("damage", 20.5, ModifierKind.MORE),
            ),
        )
        repository = build_repository()

        repository.save((chunk,))

        assert repository.all() == (chunk,)

    def test_round_trips_an_open_validity_range(self, build_repository: BuildRepository) -> None:
        chunk = make_chunk("skill-changes", "0.1.1")

        repository = build_repository()
        repository.save((chunk,))

        assert repository.all()[0].validity.valid_until is None

    def test_round_trips_multiple_chunks(self, build_repository: BuildRepository) -> None:
        chunks = (
            make_chunk("general-changes"),
            make_chunk("skill-changes"),
            make_chunk("bug-fixes", "0.1.1"),
        )
        repository = build_repository()

        repository.save(chunks)

        assert repository.all() == chunks


class TestUpsert:
    def test_replaces_a_chunk_with_the_same_id(self, build_repository: BuildRepository) -> None:
        original = make_chunk(text="Old text.")
        updated = make_chunk(text="New text.")
        assert original.chunk_id == updated.chunk_id
        repository = build_repository()
        repository.save((original,))

        repository.save((updated,))

        assert repository.all() == (updated,)

    def test_adds_a_new_chunk_and_keeps_the_stored_one(
        self, build_repository: BuildRepository
    ) -> None:
        first = make_chunk("general-changes")
        second = make_chunk("skill-changes")
        repository = build_repository()
        repository.save((first,))

        repository.save((second,))

        assert repository.all() == (first, second)

    def test_re_saving_the_same_chunk_keeps_one_copy(
        self, build_repository: BuildRepository
    ) -> None:
        chunk = make_chunk()
        repository = build_repository()

        repository.save((chunk,))
        repository.save((chunk,))

        assert repository.all() == (chunk,)

    def test_saving_nothing_changes_nothing(self, build_repository: BuildRepository) -> None:
        chunk = make_chunk()
        repository = build_repository()
        repository.save((chunk,))

        repository.save(())

        assert repository.all() == (chunk,)

    def test_updating_a_chunk_keeps_its_position(self, build_repository: BuildRepository) -> None:
        first = make_chunk("general-changes", text="Old text.")
        second = make_chunk("skill-changes")
        updated_first = make_chunk("general-changes", text="New text.")
        repository = build_repository()
        repository.save((first, second))

        repository.save((updated_first,))

        assert repository.all() == (updated_first, second)


class TestAll:
    def test_returns_every_chunk_once(self, build_repository: BuildRepository) -> None:
        chunks = (make_chunk("general-changes"), make_chunk("skill-changes"))
        repository = build_repository()

        repository.save(chunks)
        stored = repository.all()

        assert sorted(chunk.chunk_id for chunk in stored) == sorted(
            chunk.chunk_id for chunk in chunks
        )
        assert len(stored) == len(chunks)

    def test_returns_an_immutable_sequence(self, build_repository: BuildRepository) -> None:
        repository = build_repository()
        repository.save((make_chunk(),))

        assert isinstance(repository.all(), tuple)
