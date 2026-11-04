"""One question of the fixed evaluation dataset and what a correct retrieval must deliver (§8, D4)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from poe2_rag.evaluation.domain.errors import InvalidEvalCaseError
from poe2_rag.shared.domain import PatchVersion, is_blank

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
"""An id is a slug: lowercase words joined by single dashes, as a report and a shell can carry it."""


@dataclass(frozen=True, slots=True)
class EvalCase:
    """Id, question and the chunks a correct retrieval should deliver, plus the case's annotations.

    `expected_chunk_ids` empty means "the corpus does not answer this question": the case is scored
    as neither a hit nor a miss, it is counted on its own (S4). `patch` absent means "use the default
    patch policy", which is the catalog's current patch, resolved by `RetrieveContext` itself.
    `expected_answer` is free text kept for the generation stage and is not used yet.
    """

    case_id: str
    question: str
    expected_chunk_ids: tuple[str, ...] = ()
    patch: PatchVersion | None = None
    expected_answer: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self._require_case_id()
        if not isinstance(self.question, str) or is_blank(self.question):
            raise InvalidEvalCaseError(f"question must be a non-blank string, got {self.question!r}")
        self._require_expected_chunk_ids()
        if self.patch is not None and not isinstance(self.patch, PatchVersion):
            raise InvalidEvalCaseError(f"patch must be a PatchVersion or None, got {self.patch!r}")
        if not isinstance(self.expected_answer, str):
            raise InvalidEvalCaseError(
                f"expected_answer must be a string, got {self.expected_answer!r}"
            )
        _require_unique_texts("tags", self.tags)

    def _require_case_id(self) -> None:
        """Reject anything that is not a slug: the id names the case in every report and error."""
        if not isinstance(self.case_id, str) or not _SLUG.fullmatch(self.case_id):
            raise InvalidEvalCaseError(
                f"case_id must be a lowercase slug such as 'spark-mana-cost', got {self.case_id!r}"
            )

    def _require_expected_chunk_ids(self) -> None:
        """At least one id would be answerable, and a repeated id cannot change a rank."""
        _require_unique_texts("expected_chunk_ids", self.expected_chunk_ids)

    @property
    def is_answerable(self) -> bool:
        """True when the dataset states which chunks a correct retrieval must deliver."""
        return bool(self.expected_chunk_ids)


def _require_unique_texts(name: str, values: object) -> None:
    """Require a tuple of non-blank, distinct strings under `name`."""
    if not isinstance(values, tuple):
        raise InvalidEvalCaseError(f"{name} must be a tuple, got {values!r}")
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or is_blank(value):
            raise InvalidEvalCaseError(f"{name} must hold non-blank strings, got {value!r}")
        if value in seen:
            raise InvalidEvalCaseError(f"{name} repeats {value!r}")
        seen.add(value)
