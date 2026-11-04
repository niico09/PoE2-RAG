"""Orchestrate the read path end to end: question in, validated cited answer out (§5.1).

Retrieval lives in `RetrieveContext`; this use case delegates to it and adds generation plus citation
validation, so the deterministic half can be measured on its own (D4, ADR-0005).
"""

from __future__ import annotations

from dataclasses import dataclass

from poe2_rag.answering.application.ports import LanguageModel
from poe2_rag.answering.application.retrieve_context import RetrieveContext
from poe2_rag.answering.domain import Answer, RetrievedChunk
from poe2_rag.shared.domain import PatchVersion

NO_INFORMATION_TEXT = "No information available for this question in the selected patch."


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """The validated answer plus the patch it was answered for and the context that backed it."""

    answer: Answer
    target_version: PatchVersion
    delivered: tuple[RetrievedChunk, ...]


class AnswerQuestion:
    """Answer a question for one patch: retrieve the context, generate, validate citations."""

    __slots__ = ("_language_model", "_retrieve")

    def __init__(self, *, retrieve: RetrieveContext, language_model: LanguageModel) -> None:
        self._retrieve = retrieve
        self._language_model = language_model

    def execute(self, text: str, version: PatchVersion | None = None) -> AnswerResult:
        """Answer `text` for `version`, or for the catalog's current patch when it is omitted.

        Retrieval is delegated: `RetrieveContext` resolves the version, searches both indexes, fuses,
        reranks and validates every stage, so an unknown patch or a broken retrieval contract fails
        exactly as it did before this use case was split. With an empty delivered context the model
        is never called and the answer is the fixed, unsupported no-information text. `Answer.build`
        validates the citations: a citation to an undelivered chunk raises `InvalidCitationError`
        instead of being dropped (ADR-0005).
        """
        # pi-lens-ignore: python-sql-injection -- an injected use case port; this project has no SQL.
        retrieval = self._retrieve.execute(text, version)
        if not retrieval.delivered:
            return self._unsupported(retrieval.target_version)
        generated = self._language_model.generate(text, retrieval.delivered)
        answer = Answer.build(generated.text, generated.citations, retrieval.delivered)
        return AnswerResult(answer, retrieval.target_version, retrieval.delivered)

    def _unsupported(self, version: PatchVersion) -> AnswerResult:
        """Return the fixed unsupported answer for a run that delivered nothing (§5.1 step 6)."""
        return AnswerResult(Answer(NO_INFORMATION_TEXT, (), False), version, ())
