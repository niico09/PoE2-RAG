"""BM25 Okapi keyword index, persisted as one JSON file and validity-aware on read (architecture §6).

The index is both a writer (`LexicalIndexWriter`) and a reader (`LexicalIndex`). Rebuilding
replaces the whole file atomically; searching loads it once and caches the decoded corpus in
memory.

Validity filtering runs before scoring and before the limit is applied. Filtering late would leave
holes in the top-K: a stale hit could consume a slot that a valid chunk should have filled
(architecture §5.1 step 2, ADR-0003).

IDF is computed over the whole persisted corpus, never over the subset that survives the validity
filter. The same term must score the same on every patch; otherwise a query would rank differently
just because some chunks are stale.

The write is atomic (temporary file in the same directory plus `os.replace`) so a failed rebuild
never leaves a truncated index: the previous file survives or no file is written.

The file records the rules it was built with: the tokenizer version and the BM25 parameters. One file
therefore ranks identically for every reader, and a file built by other tokenization rules is refused
instead of answering with terms that no longer match the query.

The chunk encoder is local to this module. The answering capability must not import an ingestion
adapter (architecture §7), so the JSON layout is mirrored here rather than reused from the chunk
repository.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from collections.abc import Sequence
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any

from poe2_rag.answering.adapters.outbound.lexical.tokenizer import (
    TOKENIZER_VERSION,
    tokenize,
)
from poe2_rag.answering.domain import RetrievedChunk, StageScores
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    Modifier,
    ModifierKind,
    PatchVersion,
    SourceRef,
    Validity,
    is_blank,
)

SCHEMA_VERSION = 1
FILE_NAME = "bm25.json"
DEFAULT_DIRECTORY = Path("data/index")
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75

_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "tokenizer_version",
        "k1",
        "b",
        "corpus_fingerprint",
        "chunk_count",
        "average_length",
        "document_frequencies",
        "documents",
    }
)
_DOCUMENT_KEYS = frozenset({"chunk", "length", "term_frequencies"})
_CHUNK_KEYS = frozenset({"entity", "validity", "section", "text", "tags", "modifiers", "source"})
_ENTITY_KEYS = frozenset({"kind", "slug", "name"})
_VALIDITY_KEYS = frozenset({"valid_from", "valid_until"})
_SOURCE_KEYS = frozenset({"type", "url", "captured_at"})
_MODIFIER_KEYS = frozenset({"stat", "value", "kind"})


class LexicalIndexError(ValueError):
    """Base class for caller-provided invalid arguments to the lexical index."""


class BlankQueryError(LexicalIndexError):
    """Raised when a search query is blank or not a string."""


class InvalidLimitError(LexicalIndexError):
    """Raised when a search limit is not a positive integer."""


class EmptyRebuildError(LexicalIndexError):
    """Raised when rebuild receives an empty sequence."""


class DuplicateIndexEntryError(LexicalIndexError):
    """Raised when rebuild receives two chunks with the same `chunk_id`.

    The index is keyed by identity: storing one chunk twice would make `search` return it twice and
    the query flow reject the whole result (`DuplicateChunkError`). The corpus is rejected instead,
    before anything is written.
    """


class LexicalIndexLoadError(RuntimeError):
    """Base class for environment failures when reading the persisted index."""


class IndexNotFoundError(LexicalIndexLoadError):
    """Raised when the index file does not exist."""


class UnsupportedIndexSchemaError(LexicalIndexLoadError):
    """Raised when the index file's schema version is not supported."""


class IndexCorruptError(LexicalIndexLoadError):
    """Raised when the index file cannot be decoded."""


class LexicalIndexWriteError(RuntimeError):
    """Raised when the atomic write fails; the previous index is left untouched."""


class Bm25Index:
    """Persisted BM25 keyword index with patch-validity filtering inside the adapter.

    `k1` and `b` configure the BM25 formula that `rebuild` records in the file; `search` scores with
    the recorded values, so every reader of one file ranks identically and a reader's constructor
    arguments can never silently re-rank an index somebody else built.
    """

    __slots__ = ("_path", "_k1", "_b", "_cache", "_cache_stamp")

    def __init__(
        self,
        directory: str | Path = DEFAULT_DIRECTORY,
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ) -> None:
        if k1 < 0:
            raise LexicalIndexError(f"k1 must be non-negative, got {k1!r}")
        if not (0 <= b <= 1):
            raise LexicalIndexError(f"b must be between 0 and 1, got {b!r}")
        self._path = Path(directory) / FILE_NAME
        self._k1 = float(k1)
        self._b = float(b)
        self._cache: dict[str, Any] | None = None
        self._cache_stamp: tuple[int, int, int] | None = None

    @property
    def path(self) -> Path:
        """The file this index reads and writes."""
        return self._path

    def rebuild(self, chunks: Sequence[Chunk]) -> None:
        """Replace the persisted index with `chunks`, atomically.

        The corpus is sorted by `chunk_id` before serialization so two rebuilds of the same corpus
        in different orders produce byte-identical files. The input is materialized first, so an
        empty iterable is rejected the same way an empty sequence is, and a repeated `chunk_id`
        stops the run before anything is written: the index is keyed by identity.
        """
        documents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for chunk in sorted(chunks, key=lambda chunk: chunk.chunk_id):
            if chunk.chunk_id in seen:
                raise DuplicateIndexEntryError(
                    f"the corpus holds chunk_id {chunk.chunk_id!r} more than once"
                )
            seen.add(chunk.chunk_id)
            documents.append(_make_document(chunk))
        if not documents:
            raise EmptyRebuildError("cannot rebuild the lexical index from an empty corpus")
        fingerprint = _fingerprint_documents(tuple(documents))
        # A chunk always indexes at least its section, so the average length is positive.
        average_length = sum(document["length"] for document in documents) / len(documents)
        document_frequencies = Counter[str]()
        for document in documents:
            document_frequencies.update(document["term_frequencies"].keys())
        payload = {
            "schema_version": SCHEMA_VERSION,
            "tokenizer_version": TOKENIZER_VERSION,
            "k1": self._k1,
            "b": self._b,
            "corpus_fingerprint": fingerprint,
            "chunk_count": len(documents),
            "average_length": average_length,
            "document_frequencies": dict(sorted(document_frequencies.items())),
            "documents": documents,
        }
        self._write(payload)
        self._cache = None
        self._cache_stamp = None

    def search(self, text: str, version: PatchVersion, limit: int) -> tuple[RetrievedChunk, ...]:
        """Return up to `limit` chunks matching `text`, best first, with only `scores.lexical` set.

        Input is validated before the index is loaded so a missing file never masks a bad query.
        Candidates are filtered by patch validity before scoring and limiting, because a stale hit
        must not consume a slot a valid chunk could use (architecture §5.1 step 2).
        """
        if is_blank(text):
            raise BlankQueryError("query text must be a non-blank string")
        if type(limit) is not int or limit <= 0:
            raise InvalidLimitError(f"limit must be a positive int, got {limit!r}")
        data = self._load()
        query_terms = sorted(set(tokenize(text)))
        if not query_terms:
            return ()
        documents = data["documents"]
        document_frequencies = data["document_frequencies"]
        average_length = data["average_length"]
        # The file's parameters, not this instance's: one index, one ranking (see the class docstring).
        k1 = data["k1"]
        b = data["b"]
        corpus_size = len(documents)
        scored: list[tuple[RetrievedChunk, float]] = []
        for document in documents:
            chunk = document["chunk"]
            if not chunk.is_valid_at(version):
                continue
            score = _score_document(
                query_terms,
                document,
                document_frequencies,
                corpus_size,
                average_length,
                k1,
                b,
            )
            if score > 0:
                scored.append((RetrievedChunk(chunk, StageScores(lexical=score)), score))
        scored.sort(key=lambda item: (-item[1], item[0].chunk.chunk_id))
        return tuple(item[0] for item in scored[:limit])

    def _load(self) -> dict[str, Any]:
        """Read and decode the persisted index, reloading it when another writer replaced it.

        The decoded corpus is cached, but the file's identity is re-checked on every search: the
        composition root may wire the writer and the reader as two instances, and a reindex from
        another process must never leave this reader answering with the previous index. Only a
        `stat` runs while the cache is warm; the file is read again only when it changed.
        """
        stamp = self._stamp()
        if self._cache is not None and self._cache_stamp == stamp:
            return self._cache
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise IndexCorruptError(f"cannot read {self._path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise IndexCorruptError(f"{self._path} is not valid UTF-8: {exc}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise IndexCorruptError(f"{self._path} is not valid JSON: {exc}") from exc
        decoded = _build_index(self._path, data)
        self._cache = decoded
        self._cache_stamp = stamp
        return decoded

    def _stamp(self) -> tuple[int, int, int]:
        """Identify the current file version, or raise when the index does not exist."""
        try:
            details = self._path.stat()
        except FileNotFoundError as exc:
            raise IndexNotFoundError(f"lexical index not found: {self._path}") from exc
        except OSError as exc:
            raise IndexCorruptError(f"cannot read {self._path}: {exc}") from exc
        # A replaced file gets a new inode and modification time, so this changes on every rebuild.
        return (details.st_mtime_ns, details.st_size, details.st_ino)

    def _write(self, payload: dict[str, Any]) -> None:
        """Serialize `payload` to a temporary file and replace the index with it atomically."""
        directory = self._path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LexicalIndexWriteError(f"cannot create {directory}: {exc}") from exc
        temporary: str | None = None
        try:
            handle, temporary = tempfile.mkstemp(
                dir=directory, prefix=f".{FILE_NAME}.", suffix=".tmp"
            )
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            temporary = None
        except OSError as exc:
            raise LexicalIndexWriteError(f"cannot write {self._path}: {exc}") from exc
        finally:
            if temporary is not None:
                with suppress(OSError):
                    os.unlink(temporary)


def _make_document(chunk: Chunk) -> dict[str, Any]:
    """Build one persisted document: the chunk, its token length, and its term frequencies.

    The document is indexed on the chunk's text, entity name and section, so a query for the
    entity or section finds the chunk even when the body text does not contain those words.
    """
    tokens = tokenize(f"{chunk.text} {chunk.entity.name} {chunk.section}")
    term_frequencies = Counter(tokens)
    return {
        "chunk": _dump_chunk(chunk),
        "length": len(tokens),
        "term_frequencies": dict(sorted(term_frequencies.items())),
    }


def _fingerprint_documents(documents: tuple[dict[str, Any], ...]) -> str:
    """Return a stable SHA-256 fingerprint of the corpus content (chunk fields only)."""
    canonical = json.dumps(
        [document["chunk"] for document in documents],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _score_document(
    query_terms: list[str],
    document: dict[str, Any],
    document_frequencies: dict[str, int],
    corpus_size: int,
    average_length: float,
    k1: float,
    b: float,
) -> float:
    """Compute the BM25 Okapi score of one document for the distinct query terms."""
    term_frequencies = document["term_frequencies"]
    # `average_length` is validated positive by the loader, so the normalization always divides.
    normalization = k1 * (1 - b + b * (document["length"] / average_length))
    score = 0.0
    for term in query_terms:
        tf = term_frequencies.get(term)
        if not tf:
            continue
        df = document_frequencies.get(term, 0)
        idf = math.log(1 + (corpus_size - df + 0.5) / (df + 0.5))
        score += idf * (tf * (k1 + 1)) / (tf + normalization)
    return score


def _dump_chunk(chunk: Chunk) -> dict[str, Any]:
    """Return the JSON object that holds every field of `chunk`; `chunk_id` is derived, not stored.

    This mirrors `JsonChunkRepository` so a round-tripped chunk equals the original, while keeping
    the answering capability independent of the ingestion adapter (architecture §7).
    """
    return {
        "entity": {
            "kind": chunk.entity.kind,
            "slug": chunk.entity.slug,
            "name": chunk.entity.name,
        },
        "validity": {
            "valid_from": str(chunk.validity.valid_from),
            "valid_until": (
                None if chunk.validity.valid_until is None else str(chunk.validity.valid_until)
            ),
        },
        "section": chunk.section,
        "text": chunk.text,
        "tags": list(chunk.tags),
        "modifiers": [
            {"stat": modifier.stat, "value": modifier.value, "kind": modifier.kind.value}
            for modifier in chunk.modifiers
        ],
        "source": {
            "type": chunk.source.type,
            "url": chunk.source.url,
            "captured_at": chunk.source.captured_at.isoformat(),
        },
    }


def _build_index(path: Path, data: object) -> dict[str, Any]:
    """Decode the whole index file or raise; a caller never receives a partial index."""
    if not isinstance(data, dict):
        raise IndexCorruptError(f"{path} must contain a JSON object")
    if "schema_version" not in data:
        raise IndexCorruptError(f"{path}: missing key 'schema_version'")
    version = data["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise UnsupportedIndexSchemaError(f"{path}: unsupported schema_version {version!r}")
    _require_keys(path, data, _ROOT_KEYS, "the file")
    tokenizer_version = data["tokenizer_version"]
    if type(tokenizer_version) is not int or tokenizer_version != TOKENIZER_VERSION:
        raise UnsupportedIndexSchemaError(
            f"{path}: index built with tokenizer version {tokenizer_version!r}, this reader "
            f"tokenizes with version {TOKENIZER_VERSION}; rebuild the index"
        )
    try:
        chunk_count = data["chunk_count"]
        if type(chunk_count) is not int:
            raise IndexCorruptError(f"{path}: 'chunk_count' must be an int, got {chunk_count!r}")
        k1 = _require_number(path, data["k1"], "k1")
        if k1 < 0:
            raise IndexCorruptError(f"{path}: 'k1' must be non-negative, got {k1!r}")
        b = _require_number(path, data["b"], "b")
        if not 0 <= b <= 1:
            raise IndexCorruptError(f"{path}: 'b' must be between 0 and 1, got {b!r}")
        average_length = _require_number(path, data["average_length"], "average_length")
        if average_length <= 0:
            raise IndexCorruptError(
                f"{path}: 'average_length' must be positive, got {average_length!r}"
            )
        corpus_fingerprint = data["corpus_fingerprint"]
        if not isinstance(corpus_fingerprint, str):
            raise IndexCorruptError(
                f"{path}: 'corpus_fingerprint' must be a string, got {corpus_fingerprint!r}"
            )
        document_frequencies = _require_int_string_map(
            path, data["document_frequencies"], "document_frequencies"
        )
        for term, frequency in document_frequencies.items():
            if not 0 <= frequency <= chunk_count:
                raise IndexCorruptError(
                    f"{path}: 'document_frequencies[{term!r}]' must be between 0 and "
                    f"{chunk_count}, got {frequency}"
                )
        documents = _require_list(path, data["documents"], "documents")
        if len(documents) != chunk_count:
            raise IndexCorruptError(
                f"{path}: 'chunk_count' ({chunk_count}) does not match the number of documents "
                f"({len(documents)})"
            )
        decoded_documents = tuple(
            _build_document(path, index, document) for index, document in enumerate(documents)
        )
        _require_distinct_chunks(path, decoded_documents)
    except (KeyError, TypeError, ValueError) as exc:
        raise IndexCorruptError(f"{path}: {exc}") from exc
    return {
        "schema_version": version,
        "tokenizer_version": tokenizer_version,
        "k1": k1,
        "b": b,
        "corpus_fingerprint": corpus_fingerprint,
        "chunk_count": chunk_count,
        "average_length": average_length,
        "document_frequencies": document_frequencies,
        "documents": decoded_documents,
    }


def _build_document(path: Path, index: int, data: object) -> dict[str, Any]:
    """Decode one persisted document, reporting its position when the data is malformed."""
    where = f"{path}: documents[{index}]"
    if not isinstance(data, dict):
        raise IndexCorruptError(f"{where} must be an object, got {data!r}")
    _require_keys(path, data, _DOCUMENT_KEYS, f"documents[{index}]")
    try:
        length = data["length"]
        if type(length) is not int:
            raise IndexCorruptError(f"{where}: 'length' must be an int, got {length!r}")
        if length < 0:
            raise IndexCorruptError(f"{where}: 'length' must be non-negative, got {length}")
        term_frequencies = _require_int_string_map(
            path, data["term_frequencies"], f"documents[{index}].term_frequencies"
        )
        for term, count in term_frequencies.items():
            if count < 1:
                raise IndexCorruptError(
                    f"{where}: term_frequencies[{term!r}] must be positive, got {count}"
                )
        counted = sum(term_frequencies.values())
        if counted != length:
            raise IndexCorruptError(
                f"{where}: 'length' ({length}) does not match its term frequencies ({counted})"
            )
        chunk = _build_chunk(path, index, data["chunk"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IndexCorruptError(f"{where}: {exc}") from exc
    return {
        "chunk": chunk,
        "length": length,
        "term_frequencies": term_frequencies,
    }


def _build_chunk(path: Path, index: int, data: object) -> Chunk:
    """Decode one stored chunk, reporting its position when the data is malformed."""
    where = f"{path}: documents[{index}].chunk"
    if not isinstance(data, dict):
        raise IndexCorruptError(f"{where} must be an object, got {data!r}")
    _require_keys(path, data, _CHUNK_KEYS, f"documents[{index}].chunk")
    try:
        entity = _require_object(path, data["entity"], f"documents[{index}].entity")
        _require_keys(path, entity, _ENTITY_KEYS, f"documents[{index}].entity")
        validity = _require_object(path, data["validity"], f"documents[{index}].validity")
        _require_keys(path, validity, _VALIDITY_KEYS, f"documents[{index}].validity")
        source = _require_object(path, data["source"], f"documents[{index}].source")
        _require_keys(path, source, _SOURCE_KEYS, f"documents[{index}].source")
        tags = data["tags"]
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError(f"'tags' must be an array of strings, got {tags!r}")
        modifiers = data["modifiers"]
        if not isinstance(modifiers, list):
            raise ValueError(f"'modifiers' must be an array, got {modifiers!r}")
        valid_until = validity["valid_until"]
        return Chunk(
            entity=EntityRef(entity["kind"], entity["slug"], entity["name"]),
            validity=Validity(
                PatchVersion.parse(validity["valid_from"]),
                None if valid_until is None else PatchVersion.parse(valid_until),
            ),
            section=data["section"],
            text=data["text"],
            tags=tuple(tags),
            modifiers=tuple(
                _build_modifier(path, index, position, modifier)
                for position, modifier in enumerate(modifiers)
            ),
            source=SourceRef(
                source["type"],
                source["url"],
                date.fromisoformat(source["captured_at"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IndexCorruptError(f"{where}: {exc}") from exc


def _build_modifier(path: Path, index: int, position: int, data: object) -> Modifier:
    """Decode one stored modifier, keeping the field checks inside the domain type."""
    where = f"documents[{index}].chunk.modifiers[{position}]"
    modifier = _require_object(path, data, where)
    _require_keys(path, modifier, _MODIFIER_KEYS, where)
    return Modifier(modifier["stat"], modifier["value"], ModifierKind(modifier["kind"]))


def _require_object(path: Path, value: object, where: str) -> dict[str, Any]:
    """Return `value` as an object or raise a load error that names `where`."""
    if not isinstance(value, dict):
        raise IndexCorruptError(f"{path}: {where} must be an object, got {value!r}")
    return value


def _require_keys(path: Path, data: dict[str, Any], expected: frozenset[str], where: str) -> None:
    """Require exactly `expected` keys in `data`, so a misspelled field fails closed."""
    unknown = set(data) - expected
    if unknown:
        raise IndexCorruptError(f"{path}: {where} has unknown key(s) {sorted(unknown)}")
    missing = expected - set(data)
    if missing:
        raise IndexCorruptError(f"{path}: {where} is missing key(s) {sorted(missing)}")


def _require_list(path: Path, value: object, where: str) -> list[Any]:
    """Return `value` as a list or raise a load error that names `where`."""
    if not isinstance(value, list):
        raise IndexCorruptError(f"{path}: '{where}' must be an array, got {value!r}")
    return value


def _require_int_string_map(path: Path, value: object, where: str) -> dict[str, int]:
    """Return `value` as a string-to-int map or raise a load error that names `where`."""
    if not isinstance(value, dict):
        raise IndexCorruptError(f"{path}: '{where}' must be an object, got {value!r}")
    result: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(key, str):
            raise IndexCorruptError(f"{path}: '{where}' keys must be strings, got {key!r}")
        if type(count) is not int:
            raise IndexCorruptError(f"{path}: '{where}' values must be ints, got {count!r}")
        result[key] = count
    return result


def _require_number(path: Path, value: object, where: str) -> float:
    """Return `value` as a finite number or raise a load error that names `where`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IndexCorruptError(f"{path}: '{where}' must be a number, got {value!r}")
    if not math.isfinite(value):
        raise IndexCorruptError(f"{path}: '{where}' must be finite, got {value!r}")
    return float(value)


def _require_distinct_chunks(path: Path, documents: tuple[dict[str, Any], ...]) -> None:
    """Reject a file that stores one `chunk_id` twice, so the index stays keyed by identity."""
    seen: set[str] = set()
    for document in documents:
        chunk_id = document["chunk"].chunk_id
        if chunk_id in seen:
            raise IndexCorruptError(f"{path}: duplicate chunk_id {chunk_id!r}")
        seen.add(chunk_id)
