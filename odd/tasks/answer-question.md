# Feature: answer-question

## Objective
Orchestrate the query read path end to end — query resolution, hybrid retrieval, rank fusion, reranking,
generation and citation validation — over small outbound ports, test-first, with in-memory fakes only, so
the flow is proven before BM25, Chroma, the cross-encoder or the Anthropic SDK exist.

## Problem / Why
`docs/architecture.md` §5.1 fixes the eight steps of the read path and §6 fixes its ports. The domain
already owns the two correctness rules of the flow (fusion order and citation integrity, feature
`answering-domain`); what is missing is the piece that composes them with the indexes, the reranker and the
model. Without that composition nothing proves that both indexes are queried for the target patch, that
validity is enforced *before* fusion, or that a citation the model invents never becomes a delivered
answer. Writing the use case against fakes first keeps those rules testable with no infrastructure (D4)
and gives every future adapter a contract to satisfy (§8).

## Specs
- **S1** — `ports.py` holds `typing.Protocol` ports, one responsibility each, and no third-party import:
  - `Embedder.embed(text: str) -> tuple[float, ...]`
  - `LexicalIndex.search(text: str, version: PatchVersion, limit: int) -> Sequence[RetrievedChunk]`
    (best first, `scores.lexical` set; only chunks valid at `version`)
  - `VectorIndex.search(vector: Sequence[float], version: PatchVersion, limit: int) -> Sequence[RetrievedChunk]`
    (best first, `scores.dense` set; only chunks valid at `version`)
  - `Reranker.rerank(text: str, candidates: Sequence[RetrievedChunk], limit: int) -> Sequence[RetrievedChunk]`
    (best first, `scores.rerank` set)
  - `LanguageModel.generate(question: str, context: Sequence[RetrievedChunk]) -> GeneratedAnswer`
  - `GeneratedAnswer(text: str, citations: tuple[Citation, ...])` is a frozen dataclass defined in this
    layer: raw model output, not yet validated against what was delivered.
- **S2** — `RetrievalSettings(lexical_top_n, dense_top_n, rerank_top_m, final_top_k, rrf_k=60)`: frozen, all
  positive ints, `final_top_k <= rerank_top_m`. Invalid values raise a specific error.
- **S3** — `answer_question.py`: `AnswerQuestion` with all ports, the `PatchCatalog` and the settings
  injected through the constructor. `execute(text: str, version: PatchVersion | None = None) -> AnswerResult`:
  1. `Query.for_catalog(text, catalog, version)`.
  2. Lexical and dense search with the query's target version (sequential is fine in the MVP).
  3. Guard (D1): any returned chunk not valid at `target_version` raises `StaleChunkError` (an index broke
     its contract; fail loud, never filter after fusion).
  4. `reciprocal_rank_fusion` over both chunk_id rankings with `settings.rrf_k`; merge the lexical/dense
     scores of the same chunk and set `scores.fused`.
  5. Rerank the top `rerank_top_m` fused candidates; keep `final_top_k`.
  6. No candidates left: do not call the `LanguageModel`; return an unsupported `Answer` (`supported=False`)
     with a fixed "no information" text.
  7. Generate with the delivered chunks, then `Answer.build(text, citations, delivered)`. Let
     `InvalidCitationError` propagate: a citation to an undelivered chunk invalidates the answer.
  `AnswerResult(answer: Answer, target_version: PatchVersion, delivered: tuple[RetrievedChunk, ...])`, so the
  CLI and evaluation can show and measure what was used.
- **S4** — Application errors subclass a new `AnswerQuestionError` (not `DomainError`).
- **S5** — `answering/application` may import `shared/domain` and `answering/domain` only. No third-party packages.
- **S6** — TDD: tests first, observe RED, implement GREEN, refactor while green.
- **S7** — Fakes live in `tests/` (`tests/unit/answering/application/fakes.py`), not in `src/`. They record
  their calls so tests can assert order and arguments.
- **S8** — Required tests: `version defaults to catalog.current(); unknown version rejected; both indexes
  receive the target version; a chunk present in only one ranking survives fusion; final_top_k is
  respected; empty retrieval skips the LLM and is unsupported; an LLM citation to an undelivered chunk
  raises InvalidCitationError; a stale chunk from an index raises StaleChunkError; zero citations yield
  supported=False`.
- **S9** — Out of scope: real adapters, `bootstrap.py` wiring, CLI `ask` command, entity/modifier detection
  in the query (step 1 analysis beyond version resolution), concurrency.
- **S10** — Code, tests, docs and commit messages in English.
- **S11** — Delivery: branch `feat/answer-question` from `main`, feature doc `odd/tasks/answer-question.md`,
  one Conventional Commit per task with no `Co-Authored-By` or any AI attribution; no push, no PR.
- **S12** — `uv run pytest -q` passes; the report states `<command>: <observed result>` and RED/GREEN counts
  per task. About 400 authored changed lines per task is a planning heuristic, not a cap: never trim tests
  or comments to fit it.

## Non-goals
- BM25, Chroma, `bge-base-en-v1.5`, `bge-reranker-base`, the Anthropic SDK and any other real adapter.
- `bootstrap.py` wiring, the `ask` CLI command, and the evaluation dataset.
- Entity or modifier detection inside the query beyond resolving the target version.
- Concurrent retrieval. The two searches run sequentially; parallelism is an adapter-level concern later.
- Persisting, caching or retrying anything. The use case is a single in-process call.

## Acceptance criteria
- The pipeline calls the ports in one documented order and never reaches the model when no chunk was
  delivered: with an empty retrieval, `generate` is not called and the answer is unsupported.
- Both indexes always receive `query.target_version`; a chunk that is not valid at that version aborts the
  run with `StaleChunkError` instead of being filtered after fusion (D1, ADR-0003).
- `AnswerResult.delivered` is exactly the context the model saw and the set `Answer.build` validated the
  citations against.
- `answering/application` imports only the standard library, `poe2_rag.shared.domain` and
  `poe2_rag.answering.domain`.
- `uv run pytest -q` green.

## Checks
- `uv run pytest -q`
- Import boundary (S5), by inspection plus `grep`.
- Type check, best effort and **not** a gate: `uvx pyright==1.1.414` (Pyright is not a project dependency;
  see `odd/tasks/patch-catalog.md` T13).

## Tasks
- [x] T1 — Branch `feat/answer-question`, feature doc, Engram mirror. Route: inline. S11.
- [x] T2 — `ports.py` + `GeneratedAnswer` + application error base. Route: inline. S1, S4, S5.
- [x] T3 — `RetrievalSettings` and its validation. Route: inline. S2, S4.
- [ ] T4 — `AnswerQuestion` happy path + `AnswerResult` + fakes. Route: inline. S3, S7, S8.
- [ ] T5 — Stale-chunk guard and the empty-retrieval path. Route: inline. S3, S8.
- [ ] T6 — Close: full suite, import boundary, architecture doc, evidence. Route: inline. S6, S12.

## Delivery slices
Filled at close.

## Progress / Evidence
- T1: branch `feat/answer-question` created from `main` (`7730db6`). Baseline on that base:
  `uv run pytest -q` → 344 passed. No RED applicable (documentation only).
- T2: RED → collection `ModuleNotFoundError: No module named 'poe2_rag.answering.application'`,
  0 tests run (9 written). GREEN → **353 passed** (9 new).
  - `GeneratedAnswer` validates shape only (non-blank text, citations as a tuple of `Citation`): it cannot
    check delivery, which stays `Answer.build`'s rule; the model may legitimately return no citations.
  - `AnswerQuestionError` derives from `ValueError`, not from `DomainError`: a broken outbound contract is
    not a domain invariant violation, so no caller can swallow it as one by accident.
  - The ports are `runtime_checkable`, so a future contract test can assert an adapter implements its port;
    a test pins that the check works.
- T3: RED → collection `ImportError: cannot import name 'RetrievalSettings' from
  'poe2_rag.answering.application'`, 0 tests run (37 cases written). GREEN → **390 passed** (37 new).
  - `rrf_k` defaults to the domain's `DEFAULT_K`, so the settings cannot drift from the fusion default;
    a test still pins the value at `60`.
  - `bool` is rejected before `int`: `isinstance(True, int)` is true, and `True` as a budget would pass a
    naive check. `StageScores` rejects it the same way.
  - `final_top_k > rerank_top_m` is rejected because the reranker cannot deliver more chunks than it
    was given, so a larger final budget could never be filled.

## Close
Filled at close.

## Next step
Filled at close.

## Log

### L1 — original request (verbatim)

````text
Implement the AnswerQuestion use case and its outbound ports in PoE2-RAG, test-first, using fakes only.

## Context (read first)
- docs/architecture.md §5.1 (the 8-step AnswerQuestion flow), §6 (ports), §7 (layout), §8 (testing).
- docs/adr/0003-vector-store-chroma.md (validity is filtered inside the indexes, before fusion).
- docs/adr/0005-llm-por-api.md (native citations; the domain still validates them).
- Existing domain, reuse it and do not redefine it:
  - shared/domain: PatchVersion, Chunk, PatchCatalog, DomainError.
  - answering/domain: Query.for_catalog, StageScores, RetrievedChunk, reciprocal_rank_fusion,
    Citation, Answer.build, InvalidCitationError.
  Match the existing style: frozen slotted dataclasses, specific errors, short English docstrings.

## Goal
Orchestrate the full read path end to end against fakes, so the flow is proven before any
infrastructure (BM25, Chroma, cross-encoder, Anthropic SDK) exists.

## Scope (src/poe2_rag/answering/application/)
1. ports.py: typing.Protocol ports, one responsibility each:
   - `Embedder.embed(text: str) -> tuple[float, ...]`
   - `LexicalIndex.search(text: str, version: PatchVersion, limit: int) -> Sequence[RetrievedChunk]`
     (best first, `scores.lexical` set; only chunks valid at `version`)
   - `VectorIndex.search(vector: Sequence[float], version: PatchVersion, limit: int) -> Sequence[RetrievedChunk]`
     (best first, `scores.dense` set; only chunks valid at `version`)
   - `Reranker.rerank(text: str, candidates: Sequence[RetrievedChunk], limit: int) -> Sequence[RetrievedChunk]`
     (best first, `scores.rerank` set)
   - `LanguageModel.generate(question: str, context: Sequence[RetrievedChunk]) -> GeneratedAnswer`
     where `GeneratedAnswer(text: str, citations: tuple[Citation, ...])` is a frozen dataclass
     defined in this layer (raw model output, not yet validated).
2. `RetrievalSettings(lexical_top_n, dense_top_n, rerank_top_m, final_top_k, rrf_k=60)`: frozen,
   all positive ints, `final_top_k <= rerank_top_m`. Invalid values raise a specific error.
3. answer_question.py: `AnswerQuestion` with all ports, the PatchCatalog and the settings
   injected through the constructor. `execute(text: str, version: PatchVersion | None = None) -> AnswerResult`:
   1. `Query.for_catalog(text, catalog, version)`.
   2. Lexical and dense search with the query's target_version (sequential is fine in the MVP).
   3. Guard (D1): any returned chunk not valid at target_version raises `StaleChunkError`
      (an index broke its contract; fail loud, never filter after fusion).
   4. `reciprocal_rank_fusion` over both chunk_id rankings with `settings.rrf_k`; merge the
      lexical/dense scores of the same chunk and set `scores.fused`.
   5. Rerank the top `rerank_top_m` fused candidates; keep `final_top_k`.
   6. No candidates left: do not call the LanguageModel; return an unsupported Answer
      (`supported=False`) with a fixed "no information" text.
   7. Generate with the delivered chunks, then `Answer.build(text, citations, delivered)`.
      Let `InvalidCitationError` propagate: a citation to an undelivered chunk invalidates the answer.
   `AnswerResult(answer: Answer, target_version: PatchVersion, delivered: tuple[RetrievedChunk, ...])`
   so the CLI and evaluation can show and measure what was used.
4. Application errors subclass a new `AnswerQuestionError` (not DomainError).

## Rules
- answering/application may import shared/domain and answering/domain only. No third-party packages.
- TDD: tests first, observe RED, implement GREEN, refactor while green.
- Fakes live in tests/ (e.g. tests/unit/answering/application/fakes.py), not in src/.
  They record their calls so tests can assert order and arguments.
- Required tests: version defaults to catalog.current(); unknown version rejected; both indexes
  receive the target version; a chunk present in only one ranking survives fusion; final_top_k is
  respected; empty retrieval skips the LLM and is unsupported; an LLM citation to an undelivered
  chunk raises InvalidCitationError; a stale chunk from an index raises StaleChunkError;
  zero citations yield supported=False.
- Out of scope: real adapters, bootstrap.py wiring, CLI `ask` command, entity/modifier detection
  in the query (step 1 analysis beyond version resolution), concurrency.
- Code, tests, docs and commit messages in English.
- About 400 authored changed lines per task is a planning heuristic, not a cap: never trim tests
  or comments to fit it.

## Allowed edit surfaces
src/poe2_rag/answering/application/**
src/poe2_rag/answering/__init__.py
tests/unit/answering/application/**
odd/tasks/answer-question.md
docs/architecture.md

## Delivery
- Branch feat/answer-question from main. Feature doc odd/tasks/answer-question.md with task IDs,
  route per task and RED/GREEN evidence.
- One Conventional Commit per task. No Co-Authored-By or any AI attribution.
- Do not push or open a PR.

## Verification
- `uv run pytest -q` passes. Report `<command>: <observed result>` and RED/GREEN counts per task.

## Return
Concise report: tasks done, commits, test counts, deviations from this prompt and why.
Close with a `## Key Learnings` block of 1–5 numbered standalone facts.
````
