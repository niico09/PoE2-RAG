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
- [ ] T1 — Branch `feat/answering-domain`, feature doc, Engram mirror. Route: inline. S10.
- [ ] T2 — `Query` + `Query.for_catalog` + `InvalidQueryError` + package skeleton. Route: inline. S1, S5.
- [ ] T3 — `StageScores` + `RetrievedChunk`. Route: inline. S2, S5.
- [ ] T4 — `reciprocal_rank_fusion`. Route: inline. S3, S5, S7, S8.
- [ ] T5 — `Citation` + `Answer` + `Answer.build`. Route: inline. S4, S5.
- [ ] T6 — Close: full suite, evidence per task, RED/GREEN report. Route: inline. S7, S11.

## Delivery slices
- One commit per task (S10), five work-unit commits for T2-T5 plus two documentation commits (T1, T6).
- The harness counts authored lines against `origin/main`, so its slice warning includes the whole
  `feat/patch-catalog` stack this branch sits on (see L2), not this feature's diff. The slice that
  belongs to this feature is `e7a998b..HEAD`; the real count is recorded at close in T6, not guessed
  here.
- PR target: `main` once `feat/patch-catalog` merges. Until then the branch base is `feat/patch-catalog`.

## Progress / Evidence

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
