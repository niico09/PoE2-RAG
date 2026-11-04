"""Adapter-specific behaviour of the JSONL evaluation dataset reader."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from poe2_rag.evaluation.adapters.outbound import (
    DATASET_SCHEMA_VERSION,
    JsonlDatasetReader,
)
from poe2_rag.evaluation.application.errors import InvalidDatasetError
from poe2_rag.evaluation.domain import EvalCase
from poe2_rag.shared.domain import PatchVersion


def _write_jsonl(path: Path, lines: list[dict[str, Any]]) -> bytes:
    """Write `lines` as JSONL and return the raw bytes that were persisted."""
    text = "".join(f"{json.dumps(line)}\n" for line in lines)
    path.write_text(text, encoding="utf-8")
    return path.read_bytes()


def _case(line: dict[str, Any]) -> EvalCase:
    """Build the `EvalCase` that corresponds to a well-formed dataset line."""
    return EvalCase(
        case_id=line["id"],
        question=line["question"],
        expected_chunk_ids=tuple(line.get("expected_chunk_ids", [])),
        patch=PatchVersion.parse(line["patch"]) if "patch" in line else None,
        expected_answer=line.get("expected_answer", ""),
        tags=tuple(line.get("tags", [])),
    )


class TestReadValid:
    def test_reads_multi_line_file_with_all_fields(self, tmp_path: Path) -> None:
        lines = [
            {
                "schema_version": DATASET_SCHEMA_VERSION,
                "id": "spark-mana-cost",
                "question": "What is the mana cost of Spark?",
                "expected_chunk_ids": ["chunks:gem:spark@0.1.0"],
                "patch": "0.1.0",
                "expected_answer": "Spark costs 10 mana.",
                "tags": ["gem", "mana"],
            },
            {
                "schema_version": DATASET_SCHEMA_VERSION,
                "id": "fireball-damage",
                "question": "How much damage does Fireball deal?",
                "expected_chunk_ids": [],
            },
        ]
        path = tmp_path / "dataset.jsonl"
        raw = _write_jsonl(path, lines)

        dataset = JsonlDatasetReader().read(path)

        assert dataset.path == path
        assert dataset.sha256 == hashlib.sha256(raw).hexdigest()
        assert dataset.cases == (_case(lines[0]), _case(lines[1]))

    def test_optional_fields_default_to_empty(self, tmp_path: Path) -> None:
        lines = [
            {
                "schema_version": DATASET_SCHEMA_VERSION,
                "id": "minimal-case",
                "question": "A minimal question?",
                "expected_chunk_ids": ["chunk-1"],
            }
        ]
        path = tmp_path / "dataset.jsonl"
        _write_jsonl(path, lines)

        dataset = JsonlDatasetReader().read(path)

        assert dataset.cases == (
            EvalCase(
                case_id="minimal-case",
                question="A minimal question?",
                expected_chunk_ids=("chunk-1",),
                patch=None,
                expected_answer="",
                tags=(),
            ),
        )

    def test_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text(
            "\n"
            '{"schema_version": 1, "id": "a", "question": "Q?", "expected_chunk_ids": []}\n'
            "   \n"
            "\n",
            encoding="utf-8",
        )
        raw = path.read_bytes()

        dataset = JsonlDatasetReader().read(path)

        assert dataset.sha256 == hashlib.sha256(raw).hexdigest()
        assert dataset.cases == (
            EvalCase(case_id="a", question="Q?", expected_chunk_ids=()),
        )

    def test_empty_file_returns_no_cases(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        raw = path.read_bytes()

        dataset = JsonlDatasetReader().read(path)

        assert dataset.sha256 == hashlib.sha256(raw).hexdigest()
        assert dataset.cases == ()

    def test_whitespace_only_file_returns_no_cases(self, tmp_path: Path) -> None:
        path = tmp_path / "blank.jsonl"
        path.write_text("\n  \n\t\n", encoding="utf-8")
        raw = path.read_bytes()

        dataset = JsonlDatasetReader().read(path)

        assert dataset.sha256 == hashlib.sha256(raw).hexdigest()
        assert dataset.cases == ()


class TestReadErrors:
    def _expect_line_error(self, path: Path, expected_fragment: str) -> None:
        with pytest.raises(InvalidDatasetError) as exc_info:
            JsonlDatasetReader().read(path)
        message = str(exc_info.value)
        assert expected_fragment in message
        assert "line " in message

    def test_missing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.jsonl"

        with pytest.raises(InvalidDatasetError) as exc_info:
            JsonlDatasetReader().read(path)

        assert str(path) in str(exc_info.value)

    def test_wrong_schema_version(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text(
            '{"schema_version": 99, "id": "a", "question": "Q?", "expected_chunk_ids": []}\n',
            encoding="utf-8",
        )

        self._expect_line_error(path, "schema_version")

    def test_malformed_json(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text(
            '{"schema_version": 1, "id": "a", "question": "Q?"\n',
            encoding="utf-8",
        )

        self._expect_line_error(path, "line 1")

    def test_non_object_line(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text('["not", "an", "object"]\n', encoding="utf-8")

        self._expect_line_error(path, "line 1")

    def test_missing_required_field(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text(
            '{"schema_version": 1, "id": "a", "question": "Q?"}\n',
            encoding="utf-8",
        )

        self._expect_line_error(path, "expected_chunk_ids")

    def test_unknown_field(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text(
            '{"schema_version": 1, "id": "a", "question": "Q?", "expected_chunk_ids": [], '
            '"typo": true}\n',
            encoding="utf-8",
        )

        self._expect_line_error(path, "unknown field")

    def test_expected_chunk_ids_wrong_type(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text(
            '{"schema_version": 1, "id": "a", "question": "Q?", "expected_chunk_ids": "no"}\n',
            encoding="utf-8",
        )

        self._expect_line_error(path, "expected_chunk_ids")

    def test_expected_chunk_ids_element_wrong_type(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text(
            '{"schema_version": 1, "id": "a", "question": "Q?", "expected_chunk_ids": [1]}\n',
            encoding="utf-8",
        )

        self._expect_line_error(path, "line 1")

    def test_tags_wrong_type(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text(
            '{"schema_version": 1, "id": "a", "question": "Q?", "expected_chunk_ids": [], '
            '"tags": "tag"}\n',
            encoding="utf-8",
        )

        self._expect_line_error(path, "tags")

    def test_invalid_patch_string(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text(
            '{"schema_version": 1, "id": "a", "question": "Q?", "expected_chunk_ids": [], '
            '"patch": "not-a-version"}\n',
            encoding="utf-8",
        )

        self._expect_line_error(path, "patch")

    def test_duplicate_case_id(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text(
            '{"schema_version": 1, "id": "a", "question": "One?", "expected_chunk_ids": []}\n'
            '{"schema_version": 1, "id": "a", "question": "Two?", "expected_chunk_ids": []}\n',
            encoding="utf-8",
        )

        with pytest.raises(InvalidDatasetError) as exc_info:
            JsonlDatasetReader().read(path)

        message = str(exc_info.value)
        assert "a" in message
        assert "line 2" in message

    def test_domain_validation_is_wrapped(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset.jsonl"
        path.write_text(
            '{"schema_version": 1, "id": "BAD ID", "question": "Q?", "expected_chunk_ids": []}\n',
            encoding="utf-8",
        )

        self._expect_line_error(path, "line 1")
