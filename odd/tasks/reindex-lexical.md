# Feature: reindex-lexical

## Objective
Rebuild a persisted lexical index from the processed corpus (`ReindexCorpus`) and answer
validity-filtered keyword searches from it (`Bm25Index`), test-first, so retrieval runs on real
ingested chunks for the first time.

## Specs
- **S1** — `ingestion/application/ports.py` adds the write port as a `typing.Protocol`:
  `LexicalIndexWriter.rebuild(chunks: Sequence[Chunk]) -> None`. The read port stays `LexicalIndex`
  in `answering/application/ports.py`, unchanged.
- **S2** — `ingestion/application/reindex_corpus.py`: `ReindexCorpus(repository, lexical_writer)`;
  `execute() -> ReindexReport(chunk_count, rebuilt_at)`. An empty corpus raises a specific
  `IngestionError` subclass, because "an empty index would silently answer 'no information'".
  A clear seam for the vector writer (next task) stays without implementing it.
- **S3** — `answering/adapters/outbound/lexical/tokenizer.py`: pure, deterministic tokenizer.
  Lowercase; split on whitespace and punctuation; KEEP numbers, decimals and signed or percent
  values as single tokens (`"+10%"`, `"1.5"`, `"-20%"`); keep hyphenated/underscored game terms
  (`"area-of-effect"` → one token plus its parts). No stemming, no stopword removal in v1, because
  `"more"`/`"less"`/`"increased"` are domain-significant (D2).
- **S4** — `answering/adapters/outbound/lexical/bm25_index.py`: `Bm25Index(directory: Path, ...)`
  implements `LexicalIndexWriter` and `LexicalIndex`. Own BM25 Okapi in pure Python (k1=1.5, b=0.75,
  constructor-configurable), no third-party BM25 package.
- **S5** — `rebuild` tokenizes every chunk text (plus entity name and section), computes the corpus
  statistics and persists ONE JSON file atomically (temp file + replace) with a schema version, the
  chunk data needed to return `RetrievedChunk`, and a corpus fingerprint.
- **S6** — `search` loads lazily; filters candidates with `chunk.is_valid_at(version)` BEFORE
  ranking and before taking `limit`; IDF is computed over the whole corpus; zero-score chunks are
  excluded; ties break by ascending `chunk_id`; the result carries only `scores.lexical` set.
  A missing index file or an unknown schema version raises a specific error, never an empty result.
  `limit` <= 0 and blank text raise specific errors.
- **S7** — `data/index/` joins `.gitignore`, derived from the CC BY-NC-SA corpus (ADR-0002).
- **S8** — Domain and application layers stay stdlib-only; no new dependency at all in this task:
  no Chroma, no embeddings, no `rank_bm25`.
- **S9** — TDD: tests first, observe RED, implement GREEN, refactor while green. Required unit tests:
  the tokenizer cases of S3; a hand-computed BM25 score on a 3-document corpus; deterministic
  tie-break; `limit` respected after filtering (a stale top hit does not consume a slot); persistence
  round-trip; atomic write leaves no partial file; missing/unknown-schema index errors;
  `ReindexCorpus` with fakes (empty corpus, report counts, writer called once with all chunks).
- **S10** — Contract tests for `LexicalIndex` live in `tests/contract/test_lexical_index_contract.py`
  and are written so the future Chroma-backed `VectorIndex` and any other `LexicalIndex` reuse the
  validity cases: `v == valid_from` (included), `v == valid_until` (excluded), open validity,
  `v < valid_from` (excluded), and a newer valid chunk outranks nothing stale because the stale one
  never appears.
- **S11** — `docs/architecture.md` records the implemented slice (state header, `ReindexCorpus` flow,
  the write port, the BM25 adapter and its validity filter, test strategy).
- **S12** — Out of scope: Chroma, embeddings, `VectorIndex`, reranker, LLM, `bootstrap.py`, CLI.
- **S13** — Code, tests, docs and commit messages in English.
- **S14** — Delivery: branch `feat/reindex-lexical` from `main`; one Conventional Commit per task;
  no `Co-Authored-By` or any AI attribution; do not push or open a PR.
- **S15** — About 400 authored changed lines per task is a planning heuristic, not a cap.
  `uv run pytest -q` passes; the report states `<command>: <observed result>` and RED/GREEN counts
  per task.

## Non-goals
- Chroma, embeddings, `VectorIndex`, reranker, LLM.
- `bootstrap.py` composition root and the CLI commands.
- Stemming, stopword removal, query expansion or any synonym mapping (D2).
- Detecting whether a persisted index is stale against the corpus: the fingerprint is recorded so a
  later task can compare it, nothing compares it yet.

## Acceptance criteria
- One command (`ReindexCorpus.execute`) turns the processed corpus into a persisted lexical index, and
  an empty corpus fails loud instead of writing an index that answers nothing.
- `Bm25Index.search` never returns a chunk that is not valid at the target patch, never returns a
  zero-score chunk, and its ordering is a total order (score desc, `chunk_id` asc).
- A missing or unreadable index raises a specific error; a failed rebuild leaves the previous index
  byte-identical.
- `uv run pytest -q` green with no new dependency.

## Checks
- `uv run pytest -q`
- Import boundary, by inspection plus `grep`: `ingestion/application` and `answering/application`
  import only the standard library plus `poe2_rag.shared.domain` / `poe2_rag.*.domain`; the tokenizer
  and `Bm25Index` import only the standard library plus the domain and the two ports.
- `uv run python -c "import poe2_rag.answering.adapters.outbound.lexical.bm25_index"` on a clean
  import path (no optional dependency).

## Tasks
- [x] T1 — Branch `feat/reindex-lexical`, feature doc, Engram mirror, `data/index/` in `.gitignore`.
  Route: inline. S7, S13, S14.
- [x] T2 — `LexicalIndexWriter` port and its shape test. Route: inline. S1.
- [x] T3 — `EmptyCorpusError`, `ReindexReport`, `ReindexCorpus`, ingestion fakes, tests. Route: inline.
  S2, S9.
- [x] T4 — `tokenizer.py` and its cases. Route: inline. S3, S9.
- [ ] T5 — `Bm25Index` (rebuild, persistence, search), adapter unit tests and the reusable
  `LexicalIndex` contract suite. Route: inline. S4, S5, S6, S9, S10.
- [ ] T6 — Documentation: `docs/architecture.md`. Route: inline. S11.
- [ ] T7 — Close: full suite, import boundary, evidence, per-spec verdict. Route: inline. S9, S15.

## Progress / Evidence
- T1: branch `feat/reindex-lexical` created from `main` (`66f6409`). Baseline on that base:
  `uv run pytest -q` → 550 passed. No RED applicable (documentation and ignore rules only).
  `git check-ignore -v data/index/bm25.json` → `.gitignore:11:data/index/`, and `git ls-files data` is
  empty, so no derived corpus content can be committed.
- T2: RED → collection `ImportError: cannot import name 'LexicalIndexWriter' from
  'poe2_rag.ingestion.application'`, 0 tests run (2 written). GREEN → 24 passed in the ports file (22
  before); full suite **552 passed**.
  - The port is `runtime_checkable`, so the contract suite can assert conformance, and one test pins
    that a reader (`search` only) is not accepted as a writer.
  - Its docstring carries the rule the adapter and the use case share: `rebuild` replaces the whole
    index, must not leave a truncated one behind, and only receives a non-empty corpus because
    `ReindexCorpus` rejects the empty case first.
- T3: RED → collection `ImportError: cannot import name 'EmptyCorpusError' from
  'poe2_rag.ingestion.application'`, 0 tests run (11 written). GREEN → 11 passed; full suite **563
  passed**.
  - The corpus is read once into an immutable tuple and the writer receives that snapshot, so both
    indexes of the next task describe one corpus instead of racing two reads. The happy-path journal
    is exactly `all, rebuild`.
  - An empty corpus raises `EmptyCorpusError` before any writer runs: the previous index survives and
    the run never reports a rebuild it did not do. A writer failure propagates unchanged, so no report
    is returned for a half-rebuilt index.
  - `rebuilt_at` comes from an injected `clock` (default `datetime.now(UTC)`), which keeps the test
    deterministic and the report timezone-aware. The vector writer seam is a documented comment at
    the exact insertion point, not an unused parameter.
  - The blocker about `pytest` failing to resolve is environmental: pyright in this session does not
    see the project `.venv`, and the same warning appears on the pre-existing `bs4` imports. The real
    gate, `uv run pytest`, is green.
- T4: RED → collection `ModuleNotFoundError: No module named 'poe2_rag.answering.adapters'`, 0 tests
  run (18 written, 20 cases with the parametrized ones). First GREEN attempt caught a real bug:
  `test_keeps_a_signed_percent_value_as_one_token` failed (1 failed, 19 passed) because the compound
  split treated the leading `-` of `-20%` as a separator and emitted an empty part plus `20%`. Fixed
  by requiring every part to be non-empty; 20 passed. Full suite **583 passed**.
  - One regex alternation decides the token: numbers first (`[+-]?\d+(\.\d+)?%?`), so `1.5` and
    `+10%` are never mistaken for compounds, then words that may join pieces with `.`, `-`, `_`.
  - Numeric tokens are never split into parts; only words with `-`/`_` contribute parts alongside
    the whole form, which is what makes `area-of-effect` and a query for `effect` meet.
  - No stemming and no stopword list, documented with the D2 reason (`more`/`less`/`increased` are
    mechanics, not noise); repeats survive for term frequency.
  - `tests/unit/answering/adapters/**` holds the first adapter tests of the answering capability, so
    the three package `__init__.py` files of `answering/adapters` were created with it.
