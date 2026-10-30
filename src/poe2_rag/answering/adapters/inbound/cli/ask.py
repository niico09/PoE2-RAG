"""The `poe2-rag ask` command: format the answer produced by `AnswerQuestionRunner`.

This module deliberately contains no business logic. It forwards the question and optional
patch to the runner and prints the returned `AnswerResult`. Citation formatting trusts the
application-layer invariant that every `Citation.chunk_id` names a chunk present in
`result.delivered`.
"""

from __future__ import annotations

from typing import Protocol

from poe2_rag.answering.application.answer_question import AnswerResult
from poe2_rag.shared.domain.patch_version import PatchVersion


class AnswerQuestionRunner(Protocol):
    """Port used by the CLI to run a question through the answering pipeline."""

    def execute(self, text: str, version: PatchVersion | None = None) -> AnswerResult:
        """Answer `text` for `version`, or for the catalog's current patch when omitted."""
        ...


def run(*, question: str, patch: PatchVersion | None, ask: AnswerQuestionRunner) -> None:
    """Print the answer, patch and citations for `question`.

    The optional `patch` is passed straight through to `ask.execute`; when it is `None` the
    application use case supplies its own default. The command never filters, validates or
    retries the result.
    """
    # pi-lens-ignore: python-sql-injection -- an injected runner port; this project has no SQL.
    result = ask.execute(question, patch)
    print(result.answer.text)
    print()
    print(f"Patch: {result.target_version}")

    if result.answer.supported:
        print()
        print("Citations:")
        # `Answer.build` already verified that every citation names a delivered chunk.
        url_by_chunk_id = {
            item.chunk.chunk_id: item.chunk.source.url for item in result.delivered
        }
        for index, citation in enumerate(result.answer.citations, start=1):
            print(f"{index}. {citation.chunk_id}")
            print(f'   Quote: "{citation.quoted_text}"')
            print(f"   Source: {url_by_chunk_id[citation.chunk_id]}")
    else:
        print()
        print("This answer is not backed by any citation.")
