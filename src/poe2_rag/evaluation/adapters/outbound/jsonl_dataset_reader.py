"""JSONL dataset reader for evaluation cases.

Each line is a self-describing object with a required schema version, so a typo or an incompatible
file is caught at read time instead of producing a misleading metric. Blank lines are ignored.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from poe2_rag.evaluation.application.errors import InvalidDatasetError
from poe2_rag.evaluation.application.report import EvalDataset
from poe2_rag.evaluation.domain import EvalCase, InvalidEvalCaseError
from poe2_rag.shared.domain import InvalidPatchVersionError, PatchVersion

DATASET_SCHEMA_VERSION = 1

_REQUIRED_KEYS = frozenset({"schema_version", "id", "question", "expected_chunk_ids"})
_OPTIONAL_KEYS = frozenset({"patch", "expected_answer", "tags"})
_ACCEPTED_KEYS = _REQUIRED_KEYS | _OPTIONAL_KEYS


class JsonlDatasetReader:
    """Read an evaluation dataset from a UTF-8 JSONL file."""

    def read(self, path: Path) -> EvalDataset:
        """Return the dataset at `path`, or raise `InvalidDatasetError`.

        The returned value carries the SHA-256 of the raw file bytes, so a report can be tied to the
        exact dataset that produced it without the use case touching the filesystem.
        """
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise InvalidDatasetError(f"cannot read {path}: {exc}") from exc

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidDatasetError(f"{path} is not valid UTF-8: {exc}") from exc

        cases: list[EvalCase] = []
        seen: dict[str, int] = {}

        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            case = _read_line(line_number, line)
            if case.case_id in seen:
                first_line = seen[case.case_id]
                raise InvalidDatasetError(
                    f"repeated case id {case.case_id!r} at line {line_number} "
                    f"(first seen at line {first_line})"
                )
            seen[case.case_id] = line_number
            cases.append(case)

        return EvalDataset(
            path=path,
            sha256=hashlib.sha256(raw).hexdigest(),
            cases=tuple(cases),
        )


def _read_line(line_number: int, line: str) -> EvalCase:
    """Parse one JSONL line into an `EvalCase`, translating every problem into `InvalidDatasetError`."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as exc:
        raise InvalidDatasetError(f"line {line_number}: invalid JSON: {exc}") from exc

    if not isinstance(obj, dict):
        raise InvalidDatasetError(
            f"line {line_number}: line must be a JSON object, got {type(obj).__name__}"
        )

    unknown = set(obj) - _ACCEPTED_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise InvalidDatasetError(f"line {line_number}: unknown field {names}")

    missing = _REQUIRED_KEYS - set(obj)
    if missing:
        names = ", ".join(sorted(missing))
        raise InvalidDatasetError(f"line {line_number}: missing required field {names}")

    version = obj["schema_version"]
    if type(version) is not int or version != DATASET_SCHEMA_VERSION:
        raise InvalidDatasetError(
            f"line {line_number}: unsupported schema_version {version!r}"
        )

    _require_string(line_number, obj, "id")
    _require_string(line_number, obj, "question")
    _require_string_list(line_number, obj, "expected_chunk_ids")
    if "patch" in obj:
        _require_string(line_number, obj, "patch")
    if "expected_answer" in obj:
        _require_string(line_number, obj, "expected_answer")
    if "tags" in obj:
        _require_string_list(line_number, obj, "tags")

    try:
        return EvalCase(
            case_id=obj["id"],
            question=obj["question"],
            expected_chunk_ids=tuple(obj["expected_chunk_ids"]),
            patch=PatchVersion.parse(obj["patch"]) if "patch" in obj else None,
            expected_answer=obj.get("expected_answer", ""),
            tags=tuple(obj.get("tags", [])),
        )
    except (InvalidEvalCaseError, InvalidPatchVersionError) as exc:
        raise InvalidDatasetError(f"line {line_number}: {exc}") from exc


def _require_string(line_number: int, obj: dict[str, Any], name: str) -> None:
    """Require `obj[name]` to be a string."""
    value = obj[name]
    if not isinstance(value, str):
        raise InvalidDatasetError(
            f"line {line_number}: {name!r} must be a string, got {type(value).__name__}"
        )


def _require_string_list(line_number: int, obj: dict[str, Any], name: str) -> None:
    """Require `obj[name]` to be a list of strings."""
    value = obj[name]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidDatasetError(
            f"line {line_number}: {name!r} must be a list of strings"
        )
