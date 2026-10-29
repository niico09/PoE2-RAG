# Feature: answering-domain

## Objective
Model the read side of the system as pure domain logic, test-first, so retrieval fusion and
citation integrity hold without any vector store, reranker or LLM in the loop.

## Problem / Why
Two rules of the query flow are correctness rules, not plumbing. Fusion order decides what reaches
the LLM (D3), and a citation that names a chunk nobody delivered is an unbacked claim (D3). If those
rules live in adapters, every adapter can break them and every test has to reach the vector store to
notice. `docs/architecture.md` §7 puts `Query`, `RetrievedChunk`, `Answer`, `Citation` and RRF in
`answering/domain` for exactly that reason: RRF is a pure function over rankings and depends on no
library.

## Specs
- **S1** — `Query(text: str, target_version: PatchVersion)` plus the factory
  `Query.for_catalog(text, catalog, version=None)` that defaults to `catalog.current()` and rejects
  versions the catalog does not know.
- **S2** — `StageScores` (lexical, dense, fused, rerank: each `float | None`) and
  `RetrievedChunk(chunk: Chunk, scores: StageScores)`.
- **S3** — `reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]`
  as a pure function over chunk_ids: score = sum(1 / (k + rank)), rank starting at 1.
  Deterministic tie-break by chunk_id. Reject k <= 0 and duplicate ids within one ranking.
- **S4** — `Citation(chunk_id: str, quoted_text: str)` and `Answer(text, citations, supported: bool)`:
  `Answer.build(text, citations, delivered: Sequence[RetrievedChunk])` validates every citation
  against the delivered chunk_ids; a citation to an undelivered chunk raises `InvalidCitationError`.
  An answer with no citations is built with `supported=False` ("no backing"), never silently accepted.
- **S5** — Specific errors subclassing `DomainError` (`InvalidQueryError`, `InvalidCitationError`, ...).
- **S6** — Standard library only; `answering/domain` may import `shared/domain`, nothing else.
- **S7** — TDD: tests first, observe RED, implement GREEN, refactor while green. Required RRF tests:
  a known hand-computed example, a chunk present in only one ranking, ties broken deterministically,
  empty input.
- **S8** — Validity pre-filtering happens in the index adapters, not here (§5.1 step 2); do not filter
  in RRF.
- **S9** — Out of scope: use case `AnswerQuestion`, ports for indexes/reranker/LLM, adapters, CLI.
- **S10** — Delivery: branch `feat/answering-domain`, feature doc `odd/tasks/answering-domain.md`,
  one Conventional Commit per task, no `Co-Authored-By` or AI attribution.
- **S11** — `uv run pytest -q` passes. Report the RED/GREEN counts per task.

## Non-goals
- `AnswerQuestion` orchestration, the `LexicalIndex` / `VectorIndex` / `Reranker` / `LanguageModel`
  ports, BM25, Chroma, the LLM adapter, the `ask` CLI command.
- Entity detection in queries. §4.2 also lists "entidades detectadas" on `Query`; the request fixes
  the shape as `text + target_version`, so detection stays out until a task asks for it.
- Post-fusion validity filtering. RRF receives already-filtered rankings by contract (S8).

## Acceptance criteria
- RRF output is a total, deterministic order: same input, same list, on every run and every Python
  process. Ties never depend on dict or set iteration order.
- No path builds an `Answer` that claims backing it does not have.
- `answering/domain` imports only the standard library and `poe2_rag.shared.domain`.
- `uv run pytest -q` green; each task's RED and GREEN observed and recorded below.

## Checks
- `uv run pytest -q`
- Import boundary (S6), by inspection plus `grep`: no `poe2_rag.ingestion`, no adapter, no third-party.
- Type check, best effort and **not** a gate: `uvx pyright==1.1.414` (Pyright is not a project
  dependency; see `odd/tasks/patch-catalog.md` T13).

## Tasks
- [x] T1 — Branch `feat/answering-domain`, feature doc, Engram mirror. Route: inline. S10. Commit `759d372`.
- [x] T2 — `Query` + `Query.for_catalog` + `InvalidQueryError` + package skeleton. Route: inline. S1, S5. Commit `bd9816d`.
- [x] T3 — `StageScores` + `RetrievedChunk`. Route: inline. S2, S5. Commit `cbcc0d8`.
- [x] T4 — `reciprocal_rank_fusion`. Route: inline. S3, S5, S7, S8. Commit `ffe11ac`.
- [x] T5 — `Citation` + `Answer` + `Answer.build`. Route: inline. S4, S5. Commit `1ecd13a`.
- [x] T6 — Close: full suite, evidence per task, RED/GREEN report. Route: inline. S7, S11.

## Delivery slices
- One commit per task (S10): `759d372` doc, `bd9816d` T2, `cbcc0d8` T3, `ffe11ac` T4, `1ecd13a` T5.
- Authored lines for this feature (`e7a998b..HEAD`, lock files excluded): **933**, split as 138 (doc), 135 (T2),
  174 (T3), 191 (T4), 295 (T5). The whole feature is over the ~400-line review slice; the split is
  one green commit per task, which keeps every commit reviewable alone and lets the native review take
  any single one.
- The harness counts authored lines against `origin/main`, so its slice warning includes the whole
  `feat/patch-catalog` stack this branch sits on (see L2), not this feature's diff.
- PR target: `main` once `feat/patch-catalog` merges. Until then the branch base is `feat/patch-catalog`.

## Progress / Evidence
- T1: branch `feat/answering-domain` created from `feat/patch-catalog` (`e7a998b`, see L2). Baseline on that
  base: `uv run pytest -q` → 213 passed. No RED applicable (documentation only). Commit `759d372`.
- T2: RED → collection `ModuleNotFoundError: No module named 'poe2_rag.answering'`, 0 tests run (14 written).
  GREEN → **227 passed** (14 new).
  - `Query.for_catalog` validates the catalog, the text and the version *before* asking the catalog, so a
    non-`PatchVersion` argument raises `InvalidQueryError` instead of leaking the catalog port's
    `InvalidPatchCatalogError`: one error type for everything wrong with a query.
  - An unknown version is rejected rather than accepted, because answering for a patch that does not exist
    would retrieve nothing while looking like a valid answer (D1). Commit `bd9816d`.
- T3: RED → collection `ImportError: cannot import name 'InvalidRetrievedChunkError'`, 0 tests run (21 written).
  GREEN → **248 passed** (21 new).
  - `StageScores` rejects `bool` and non-finite values, matching `Modifier`: a NaN score would make every
    downstream comparison false and silently reorder retrieval.
  - `RetrievedChunk` validates both fields, so a chunk cannot enter the context window without its scores.
    Commit `cbcc0d8`.
- T4: RED → collection `ImportError: cannot import name 'InvalidRankingError'`, 0 tests run (36 written).
  GREEN → **284 passed** (36 new). The four required cases are present: hand-computed `1/(60+rank)` example,
  a chunk in only one ranking, deterministic ties, empty input.
  - Ties break by ascending `chunk_id` inside the sort key, so the order is total and never depends on dict
    insertion order or on the order the rankings arrive in.
  - Rejected as `InvalidRankingError`: `k <= 0`, non-int `k` (`bool` included), a bare string or any
    non-`Sequence` as `rankings`, a non-`Sequence` ranking (a set has no rank order), blank or non-string ids,
    and a repeated id inside one ranking. The same id in *different* rankings is the point of RRF and is accepted.
  - No validity filtering (S8): the module never imports `Validity`, and its docstring states that rankings
    must arrive pre-filtered. Commit `ffe11ac`.
- T5: RED → collection `ImportError: cannot import name 'Answer'`, 0 tests run (27 written).
  GREEN → **311 passed** (27 new).
  - `Answer.build` validates citations against the delivered `chunk_id` set, never against object identity:
    the model returns ids (ADR-0005), so ids are the contract. A citation to an undelivered chunk raises
    `InvalidCitationError` naming that id.
  - No citations → `supported=False`, both through `build` and through the constructor, and
    `Answer(text, (), supported=True)` is rejected: an answer can never claim backing it does not have.
    Commit `1ecd13a`.
- T6: close. Full suite **311 passed**; `uvx pyright==1.1.414` → `0 errors, 0 warnings, 0 informations`.
  - A commit-hygiene defect found and fixed before close: `tests/.../test_fusion.py` had landed in the T3
    commit because the T3 commit and the T4 test write ran in the same parallel batch, which made the T3
    commit fail collection on its own. The three unpushed commits (T3, T4, T5) were replayed; each rewritten
    tree was verified green alone (T3 → 248, T4 → 284, T5 → 311), and `git diff` against the pre-rewrite
    final commit is empty, so no content changed. Old hashes are only in the reflog.

## Close
- Final `uv run pytest -q` → **311 passed** (baseline on the stacked base: 213; +98 tests, +933 authored lines).
- `uvx pyright==1.1.414` (bare, project config) → `0 errors, 0 warnings, 0 informations`.
- Per-spec verdict:
  - S1 ✅ `Query(text, target_version)` + `for_catalog(text, catalog, version=None)` defaulting to
    `catalog.current()`, rejecting unknown versions (`InvalidQueryError`).
  - S2 ✅ `StageScores(lexical, dense, fused, rerank)` all `float | None` (default `None`) and
    `RetrievedChunk(chunk, scores)`.
  - S3 ✅ `reciprocal_rank_fusion(rankings, k=60)`, `score = sum(1/(k+rank))` with rank from 1, tie-break by
    `chunk_id`, `k <= 0` and duplicate ids within one ranking rejected.
  - S4 ✅ `Citation(chunk_id, quoted_text)`, `Answer(text, citations, supported)`, `Answer.build` validating
    against delivered ids (`InvalidCitationError`), no citations → `supported=False`.
  - S5 ✅ Six `DomainError` subclasses in `answering/domain/errors.py`; each test module asserts the subclass
    relationship, not just the raise.
  - S6 ✅ Imports read back with `grep`: only `__future__`, `collections.abc`, `dataclasses`, `math`,
    `poe2_rag.answering.domain.*` and `poe2_rag.shared.domain.*`. No third party, no ingestion, no adapter.
  - S7 ✅ RED observed for every task, then GREEN; the four required RRF cases are named tests in
    `tests/unit/answering/domain/test_fusion.py`.
  - S8 ✅ `grep -rn 'is_valid_at\|Validity' src/poe2_rag/answering/` → no matches; RRF documents the contract.
  - S9 ✅ `src/poe2_rag/answering/` contains only `domain/`; no `application/`, no `adapters/`, no CLI.
  - S10 ✅ Five Conventional Commits, one per task; `git log e7a998b..HEAD --format=%B | grep -ci`
    `'co-authored-by\|generated with\|claude\|assisted-by'` → `0`.
  - S11 ✅ Counts above, per task.
- No check was skipped; nothing is pending.

## Next step
Push and open the PR against `feat/patch-catalog` (or `main` once that branch merges): the user's decision,
not taken here. Nothing is committed beyond this feature branch.

## Log

### L1 — original request (verbatim)

````text
Implement the answering domain of PoE2-RAG (pure logic, no external services), test-first.

## Context (read first)
- docs/architecture.md §4.2 (Query, RetrievedChunk, Answer), §5.1 (AnswerQuestion flow), §7 (answering/ layout).
- docs/adr/0005-llm-por-api.md (native citations map to Citation(chunk_id); the domain still validates them).
- Existing shared domain: src/poe2_rag/shared/domain/ (Chunk, PatchVersion, PatchCatalog, DomainError).
  Match its style.

## Goal
Model the read side so retrieval fusion and citation integrity are guaranteed by the domain,
independent of any vector store or LLM.

## Scope (src/poe2_rag/answering/domain/)
1. `Query(text: str, target_version: PatchVersion)`, plus a factory
   `Query.for_catalog(text, catalog, version=None)` that defaults to `catalog.current()`
   and rejects versions the catalog does not know.
2. `StageScores` (lexical, dense, fused, rerank: each `float | None`) and
   `RetrievedChunk(chunk: Chunk, scores: StageScores)`.
3. `reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]`
   as a pure function over chunk_ids: score = sum(1 / (k + rank)), rank starting at 1.
   Deterministic tie-break by chunk_id. Reject k <= 0 and duplicate ids within one ranking.
4. `Citation(chunk_id: str, quoted_text: str)` and
   `Answer(text, citations, supported: bool)`:
   - `Answer.build(text, citations, delivered: Sequence[RetrievedChunk])` validates every citation against
     the delivered chunk_ids; a citation to an undelivered chunk raises `InvalidCitationError`.
   - An answer with no citations is built with `supported=False` ("no backing"), never silently accepted.
5. Specific errors subclassing DomainError (`InvalidQueryError`, `InvalidCitationError`, ...).

## Rules
- Standard library only; answering/domain may import shared/domain, nothing else.
- TDD: tests first, observe RED, implement GREEN, refactor while green.
- Required RRF tests: a known hand-computed example, a chunk present in only one ranking,
  ties broken deterministically, empty input.
- Validity pre-filtering happens in the index adapters, not here (§5.1 step 2); do not filter in RRF.
- Out of scope: use case AnswerQuestion, ports for indexes/reranker/LLM, adapters, CLI.

## Delivery
- Branch feat/answering-domain from main. Feature doc odd/tasks/answering-domain.md.
- One Conventional Commit per task. No Co-Authored-By or AI attribution.

## Verification
- `uv run pytest -q` passes. Report the RED/GREEN counts per task.
````

### L2 — decision: base branch

The request says "branch feat/answering-domain from main" and states that `PatchCatalog` already
exists in `shared/domain`. On `main` it does not: the port only exists on the unmerged
`feat/patch-catalog` branch (`src/poe2_rag/shared/domain/patch_catalog.py`, with `current()`,
`known()` and `contains(version)`, plus `InMemoryPatchCatalog`). `main` is at `b8e19af` and has no
`answering` package at all.

Asked the human; answer: **stack `feat/answering-domain` on `feat/patch-catalog`** (option 1 of 4).
Consequences recorded here so the deviation from S10 is explicit: the branch base is
`feat/patch-catalog` at `e7a998b`, not `main`; the PR must be retargeted to `main` after that branch
merges. Nothing in `shared/domain` is modified by this feature, so the stack stays conflict-free.
