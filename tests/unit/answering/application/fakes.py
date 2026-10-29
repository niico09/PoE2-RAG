"""Recording fakes for the answering application ports.

Each fake appends to a shared `CallRecorder`, so a test can assert the arguments a port received and
the order of the whole pipeline without touching a real adapter (§8). The fakes honor `limit` and the
score each port promises, so a test that sees a different shape is testing the use case, not the fake.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from poe2_rag.answering.application import GeneratedAnswer
from poe2_rag.answering.domain import RetrievedChunk
from poe2_rag.shared.domain import PatchVersion


@dataclass
class CallRecorder:
    """Ordered journal of every port call one `execute` made.

    `Any` is deliberate: one journal holds calls with unrelated signatures, and a test reads the
    fields it knows for the port it asked about.
    """

    calls: list[tuple[Any, ...]] = field(default_factory=list)

    def record(self, port: str, *arguments: object) -> None:
        self.calls.append((port, *arguments))

    def kinds(self) -> list[str]:
        """Return the ports that were called, in order."""
        return [str(call[0]) for call in self.calls]

    def arguments(self, port: str) -> list[tuple[Any, ...]]:
        """Return the arguments of every call to `port`, in order."""
        return [call[1:] for call in self.calls if call[0] == port]


class FakeEmbedder:
    """Returns one fixed vector and records every text it was asked to embed."""

    def __init__(self, recorder: CallRecorder, vector: tuple[float, ...] = (0.25, 0.5)) -> None:
        self._recorder = recorder
        self._vector = vector

    def embed(self, text: str) -> tuple[float, ...]:
        self._recorder.record("embed", text)
        return self._vector

    @property
    def vector(self) -> tuple[float, ...]:
        """The vector every `embed` call returns."""
        return self._vector


class FakeLexicalIndex:
    """Returns scripted chunks in order, honoring `limit`, and records every search."""

    def __init__(self, recorder: CallRecorder, results: Sequence[RetrievedChunk] = ()) -> None:
        self._recorder = recorder
        self._results = tuple(results)

    def search(self, text: str, version: PatchVersion, limit: int) -> tuple[RetrievedChunk, ...]:
        self._recorder.record("lexical", text, version, limit)
        return self._results[:limit]


class FakeVectorIndex:
    """Returns scripted chunks in order, honoring `limit`, and records every search."""

    def __init__(self, recorder: CallRecorder, results: Sequence[RetrievedChunk] = ()) -> None:
        self._recorder = recorder
        self._results = tuple(results)

    def search(
        self, vector: Sequence[float], version: PatchVersion, limit: int
    ) -> tuple[RetrievedChunk, ...]:
        self._recorder.record("dense", vector, version, limit)
        return self._results[:limit]


class FakeReranker:
    """Keeps or reorders the candidates by chunk id, sets `scores.rerank`, and records the call.

    `returned` replaces the result entirely, to simulate an adapter that returns more chunks than
    its `limit` or that injects one; `order` reorders the candidates by chunk id.
    """

    def __init__(
        self,
        recorder: CallRecorder,
        order: Sequence[str] | None = None,
        returned: Sequence[RetrievedChunk] | None = None,
    ) -> None:
        self._recorder = recorder
        self._order = None if order is None else tuple(order)
        self._returned = None if returned is None else tuple(returned)

    def rerank(
        self, text: str, candidates: Sequence[RetrievedChunk], limit: int
    ) -> tuple[RetrievedChunk, ...]:
        self._recorder.record("rerank", text, tuple(candidates), limit)
        if self._returned is not None:
            return self._returned
        ordered = self._ordered(candidates)
        ranked = tuple(
            RetrievedChunk(item.chunk, replace(item.scores, rerank=1.0 / rank))
            for rank, item in enumerate(ordered, start=1)
        )
        return ranked[:limit]

    def _ordered(self, candidates: Sequence[RetrievedChunk]) -> tuple[RetrievedChunk, ...]:
        if self._order is None:
            return tuple(candidates)
        by_id = {item.chunk.chunk_id: item for item in candidates}
        return tuple(by_id[chunk_id] for chunk_id in self._order)


class FakeLanguageModel:
    """Returns one scripted raw answer and records the question and the delivered chunk ids."""

    def __init__(self, recorder: CallRecorder, answer: GeneratedAnswer | None = None) -> None:
        self._recorder = recorder
        self._answer = answer or GeneratedAnswer("No information available.", ())

    def generate(self, question: str, context: Sequence[RetrievedChunk]) -> GeneratedAnswer:
        self._recorder.record(
            "generate", question, tuple(item.chunk.chunk_id for item in context)
        )
        return self._answer
