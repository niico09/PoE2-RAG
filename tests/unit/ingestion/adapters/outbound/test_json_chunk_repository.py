"""Adapter-specific behaviour of the JSON chunk repository: file shape, persistence and atomicity."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    ChunkRepositoryLoadError,
    ChunkRepositoryWriteError,
    JsonChunkRepository,
)
from poe2_rag.shared.domain import (
    Chunk,
    DomainError,
    EntityRef,
    PatchVersion,
    SourceRef,
    Validity,
)

THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"


def make_chunk(section: str = "general-changes", text: str = "Notes.") -> Chunk:
    version = PatchVersion.parse("0.1.0")
    return Chunk(
        entity=EntityRef("patch_notes", "0-1-0", "Patch 0.1.0"),
        validity=Validity(version),
        section=section,
        text=text,
        source=SourceRef("patch_notes", THREAD_URL, date(2026, 10, 29)),
    )


def read_file(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class TestFileShape:
    def test_uses_one_json_file_in_the_given_directory(self, tmp_path: Path) -> None:
        repository = JsonChunkRepository(tmp_path)

        repository.save((make_chunk(),))

        assert repository.path == tmp_path / "chunks.json"
        assert [path.name for path in tmp_path.iterdir()] == ["chunks.json"]

    def test_declares_the_schema_version(self, tmp_path: Path) -> None:
        repository = JsonChunkRepository(tmp_path)

        repository.save((make_chunk(),))

        document = read_file(repository.path)
        assert document["schema_version"] == 1
        assert isinstance(document["chunks"], list)

    def test_creates_the_directory_on_save(self, tmp_path: Path) -> None:
        repository = JsonChunkRepository(tmp_path / "nested" / "processed")

        repository.save((make_chunk(),))

        assert repository.path.is_file()

    def test_defaults_to_the_production_directory(self) -> None:
        assert JsonChunkRepository().path == Path("data/processed/chunks.json")


class TestPersistence:
    def test_reads_back_after_a_new_instance(self, tmp_path: Path) -> None:
        chunk = make_chunk()
        JsonChunkRepository(tmp_path).save((chunk,))

        assert JsonChunkRepository(tmp_path).all() == (chunk,)

    def test_a_missing_file_reads_as_an_empty_repository(self, tmp_path: Path) -> None:
        assert JsonChunkRepository(tmp_path / "missing").all() == ()

    def test_rejects_a_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "chunks.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(ChunkRepositoryLoadError) as exc_info:
            JsonChunkRepository(tmp_path).all()

        assert str(path) in str(exc_info.value)

    def test_rejects_an_unknown_schema_version(self, tmp_path: Path) -> None:
        path = tmp_path / "chunks.json"
        path.write_text(json.dumps({"schema_version": 99, "chunks": []}), encoding="utf-8")

        with pytest.raises(ChunkRepositoryLoadError) as exc_info:
            JsonChunkRepository(tmp_path).all()

        assert "schema_version" in str(exc_info.value)

    def test_rejects_a_chunk_with_a_missing_field(self, tmp_path: Path) -> None:
        path = tmp_path / "chunks.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "chunks": [{"section": "general-changes", "text": "Notes."}],
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ChunkRepositoryLoadError) as exc_info:
            JsonChunkRepository(tmp_path).all()

        assert "chunks[0]" in str(exc_info.value)


class TestAtomicWrite:
    def test_a_failed_replace_keeps_the_previous_corpus(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = JsonChunkRepository(tmp_path)
        repository.save((make_chunk("general-changes", "Original."),))
        before = repository.path.read_bytes()

        def fail_replace(source: object, target: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", fail_replace)

        with pytest.raises(ChunkRepositoryWriteError):
            repository.save((make_chunk("skill-changes", "New."),))

        assert repository.path.read_bytes() == before
        assert sorted(path.name for path in tmp_path.iterdir()) == ["chunks.json"]

    def test_a_failed_first_write_leaves_no_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = JsonChunkRepository(tmp_path)

        def fail_replace(source: object, target: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", fail_replace)

        with pytest.raises(ChunkRepositoryWriteError):
            repository.save((make_chunk(),))

        assert list(tmp_path.iterdir()) == []

    def test_a_read_failure_stops_a_save(self, tmp_path: Path) -> None:
        path = tmp_path / "chunks.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(ChunkRepositoryLoadError):
            JsonChunkRepository(tmp_path).save((make_chunk(),))


class TestErrors:
    def test_load_and_write_errors_are_environment_failures(self) -> None:
        assert issubclass(ChunkRepositoryLoadError, RuntimeError)
        assert issubclass(ChunkRepositoryWriteError, RuntimeError)

    def test_errors_are_not_domain_errors(self) -> None:
        assert not issubclass(ChunkRepositoryLoadError, DomainError)
        assert not issubclass(ChunkRepositoryWriteError, DomainError)
