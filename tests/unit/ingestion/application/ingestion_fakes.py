"""Recording fakes for the ingestion application ports.

Each fake appends to a shared `CallRecorder`, so a test can assert the arguments a port received and
the order of the whole run without touching a real adapter (§8). The repository fake applies the same
upsert semantics the port promises, so a test that sees a different shape is testing the use case.

The module carries the capability name because `tests/` is not a package: pytest puts each test
directory on `sys.path`, and a second capability's `fakes.py` would shadow this one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from poe2_rag.ingestion.application import RawDocument
from poe2_rag.shared.domain import Chunk, EntityRef, PatchVersion, SourceRef, Validity

THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
FETCHED_AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


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


def make_raw(**overrides: object) -> RawDocument:
    """Build a raw document for the fake fetcher, with the ingestion fixture defaults."""
    defaults: dict[str, object] = {
        "locator": "data/raw/patch-notes-0.1.0.html",
        "content": "<html><body><h1>0.1.0 Patch Notes</h1></body></html>",
        "fetched_at": FETCHED_AT,
        "source_type": "patch_notes",
    }
    return RawDocument(**{**defaults, **overrides})  # type: ignore[arg-type]


def make_chunk(
    section: str = "general-changes",
    version: str = "0.1.0",
    *,
    text: str | None = None,
    url: str = THREAD_URL,
) -> Chunk:
    """Build one patch-notes chunk, the modelling decision of `odd/tasks/ingest-patch-notes.md` S8."""
    parsed = PatchVersion.parse(version)
    slug = f"{parsed.major}-{parsed.minor}-{parsed.patch}"
    return Chunk(
        entity=EntityRef("patch_notes", slug, f"Patch {parsed}"),
        validity=Validity(parsed),
        section=section,
        text=text if text is not None else f"Notes for {section} in {parsed}.",
        source=SourceRef("patch_notes", url, FETCHED_AT.date()),
    )


class FakeSourceFetcher:
    """Returns one scripted document and records every locator it was asked to fetch."""

    def __init__(self, recorder: CallRecorder, error: Exception | None = None) -> None:
        self._recorder = recorder
        self._error = error

    def fetch(self, locator: str) -> RawDocument:
        self._recorder.record("fetch", locator)
        if self._error is not None:
            raise self._error
        return make_raw(locator=locator)


class FakeDocumentParser:
    """Returns scripted chunks and records the exact document it was given."""

    def __init__(self, recorder: CallRecorder, chunks: Sequence[Chunk] = ()) -> None:
        self._recorder = recorder
        self._chunks = tuple(chunks)

    def parse(self, raw: RawDocument) -> tuple[Chunk, ...]:
        self._recorder.record("parse", raw)
        return self._chunks


class FakeChunkRepository:
    """Applies the port's upsert semantics in memory and records every access."""

    def __init__(self, recorder: CallRecorder, stored: Sequence[Chunk] = ()) -> None:
        self._recorder = recorder
        self._by_id = {chunk.chunk_id: chunk for chunk in stored}

    def save(self, chunks: Sequence[Chunk]) -> None:
        self._recorder.record("save", tuple(chunks))
        for chunk in chunks:
            self._by_id[chunk.chunk_id] = chunk

    def all(self) -> tuple[Chunk, ...]:
        self._recorder.record("all")
        return tuple(self._by_id.values())


class FakeLexicalIndexWriter:
    """Records every rebuild with the exact chunks it received and keeps the last snapshot."""

    def __init__(self, recorder: CallRecorder, error: Exception | None = None) -> None:
        self._recorder = recorder
        self._error = error
        self._chunks: tuple[Chunk, ...] = ()

    def rebuild(self, chunks: Sequence[Chunk]) -> None:
        self._recorder.record("rebuild", tuple(chunks))
        if self._error is not None:
            raise self._error
        self._chunks = tuple(chunks)

    @property
    def chunks(self) -> tuple[Chunk, ...]:
        """The chunks the last successful rebuild received."""
        return self._chunks
