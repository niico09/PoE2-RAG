"""Tests for the `ask` inbound CLI adapter.

The command is a thin formatter over `AnswerQuestionRunner.execute`: it must forward the
question and patch verbatim, print the answer and the resolved patch, and either list numbered
citations or state that none back the answer. No retrieval, validation or retry logic belongs here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from poe2_rag.answering.adapters.inbound.cli.ask import run
from poe2_rag.answering.application.answer_question import (
    NO_INFORMATION_TEXT,
    AnswerResult,
)
from poe2_rag.answering.domain import Answer, Citation, RetrievedChunk, StageScores
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    PatchVersion,
    SourceRef,
    Validity,
)

CURRENT_PATCH = PatchVersion.parse("0.1.1")
REQUESTED_PATCH = PatchVersion.parse("0.1.2")
QUESTION = "How much mana does Spark cost?"


def make_chunk(
    slug: str,
    *,
    url: str = "https://poe2db.tw/us/Spark",
    valid_from: str = "0.1.1",
) -> Chunk:
    """Build a minimal delivered chunk for CLI formatting tests."""
    return Chunk(
        entity=EntityRef("active_gem", slug, slug.title()),
        validity=Validity(PatchVersion.parse(valid_from), None),
        section="description",
        text=f"{slug.title()} description text.",
        source=SourceRef("poe2db", url, date(2026, 10, 29)),
    )


def make_result(
    *,
    answer: Answer,
    target_version: PatchVersion = CURRENT_PATCH,
    delivered: tuple[RetrievedChunk, ...] = (),
) -> AnswerResult:
    """Build a real `AnswerResult` with the domain objects the command formats."""
    return AnswerResult(
        answer=answer,
        target_version=target_version,
        delivered=delivered,
    )


@dataclass
class FakeRunner:
    """Records inputs and returns a configurable result, satisfying `AnswerQuestionRunner`."""

    result: AnswerResult
    calls: list[tuple[str, PatchVersion | None]] = field(default_factory=list)

    def execute(self, text: str, version: PatchVersion | None = None) -> AnswerResult:
        self.calls.append((text, version))
        return self.result


def test_cited_answer_prints_text_patch_and_numbered_citations(capsys) -> None:
    """A supported answer lists every citation with chunk id, quote and source URL."""
    chunk = make_chunk("spark")
    delivered = (RetrievedChunk(chunk, StageScores()),)
    answer = Answer.build(
        "Spark costs 20 mana.",
        [Citation(chunk.chunk_id, "20 mana")],
        delivered,
    )
    runner = FakeRunner(
        make_result(answer=answer, target_version=REQUESTED_PATCH, delivered=delivered)
    )

    run(question=QUESTION, patch=REQUESTED_PATCH, ask=runner)

    captured = capsys.readouterr()
    assert captured.out == (
        "Spark costs 20 mana.\n"
        "\n"
        "Patch: 0.1.2\n"
        "\n"
        "Citations:\n"
        "1. poe2db:active_gem:spark@0.1.1#description\n"
        '   Quote: "20 mana"\n'
        "   Source: https://poe2db.tw/us/Spark\n"
    )
    assert captured.err == ""


def test_two_citations_are_numbered_in_answer_order(capsys) -> None:
    """Citation numbering follows `answer.citations`, not the delivered order."""
    first = make_chunk("spark", url="https://poe2db.tw/us/Spark")
    second = make_chunk("arc", url="https://poe2db.tw/us/Arc")
    delivered = (
        RetrievedChunk(second, StageScores()),
        RetrievedChunk(first, StageScores()),
    )
    answer = Answer.build(
        "Both gems cost mana.",
        [
            Citation(first.chunk_id, "20 mana"),
            Citation(second.chunk_id, "30 mana"),
        ],
        delivered,
    )
    runner = FakeRunner(make_result(answer=answer, delivered=delivered))

    run(question=QUESTION, patch=CURRENT_PATCH, ask=runner)

    captured = capsys.readouterr()
    assert captured.out == (
        "Both gems cost mana.\n"
        "\n"
        "Patch: 0.1.1\n"
        "\n"
        "Citations:\n"
        "1. poe2db:active_gem:spark@0.1.1#description\n"
        '   Quote: "20 mana"\n'
        "   Source: https://poe2db.tw/us/Spark\n"
        "2. poe2db:active_gem:arc@0.1.1#description\n"
        '   Quote: "30 mana"\n'
        "   Source: https://poe2db.tw/us/Arc\n"
    )


def test_unsupported_answer_prints_text_and_no_citation_statement(capsys) -> None:
    """A no-information answer states it has no citations and prints no source URLs."""
    answer = Answer(NO_INFORMATION_TEXT, (), False)
    runner = FakeRunner(make_result(answer=answer, target_version=REQUESTED_PATCH))

    run(question=QUESTION, patch=REQUESTED_PATCH, ask=runner)

    captured = capsys.readouterr()
    assert captured.out == (
        "No information available for this question in the selected patch.\n"
        "\n"
        "Patch: 0.1.2\n"
        "\n"
        "This answer is not backed by any citation.\n"
    )
    assert captured.err == ""
    assert "Source:" not in captured.out
    assert "http" not in captured.out


def test_forwards_question_and_patch_verbatim(capsys) -> None:
    """`run` passes the exact question and patch to the runner, including `None`."""
    chunk = make_chunk("spark")
    delivered = (RetrievedChunk(chunk, StageScores()),)
    answer = Answer.build(
        "Spark costs 20 mana.",
        [Citation(chunk.chunk_id, "20 mana")],
        delivered,
    )
    runner = FakeRunner(make_result(answer=answer, delivered=delivered))

    run(question=QUESTION, patch=None, ask=runner)

    assert runner.calls == [(QUESTION, None)]


def test_forwards_explicit_patch_version(capsys) -> None:
    """When a patch is supplied, that exact version is forwarded and printed."""
    chunk = make_chunk("spark", valid_from="0.1.1")
    delivered = (RetrievedChunk(chunk, StageScores()),)
    answer = Answer.build(
        "Spark costs 20 mana.",
        [Citation(chunk.chunk_id, "20 mana")],
        delivered,
    )
    runner = FakeRunner(
        make_result(answer=answer, target_version=REQUESTED_PATCH, delivered=delivered)
    )

    run(question=QUESTION, patch=REQUESTED_PATCH, ask=runner)

    assert runner.calls == [(QUESTION, REQUESTED_PATCH)]
    captured = capsys.readouterr()
    assert "Patch: 0.1.2" in captured.out
    assert captured.out.splitlines()[2] == "Patch: 0.1.2"


def test_nothing_written_to_stderr_on_success(capsys) -> None:
    """Successful formatting must leave stderr empty."""
    chunk = make_chunk("spark")
    delivered = (RetrievedChunk(chunk, StageScores()),)
    answer = Answer.build(
        "Spark costs 20 mana.",
        [Citation(chunk.chunk_id, "20 mana")],
        delivered,
    )
    runner = FakeRunner(make_result(answer=answer, delivered=delivered))

    run(question=QUESTION, patch=CURRENT_PATCH, ask=runner)

    assert capsys.readouterr().err == ""
