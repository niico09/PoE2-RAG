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
- **S7** — Fakes live in `tests/` (`tests/unit/answering/application/answering_fakes.py`), not in `src/`. They record
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
- [x] T4 — `AnswerQuestion` happy path + `AnswerResult` + fakes. Route: inline. S3, S7, S8.
- [x] T5 — Stale-chunk guard and the empty-retrieval path. Route: inline. S3, S8.
- [x] T6 — Close: full suite, import boundary, architecture doc, evidence. Route: inline. S6, S12.

## Delivery slices
- One commit per task (S11): `bfb161f` T1 (plan), `69ab572` T2 (ports + `GeneratedAnswer` + errors),
  `57cdb45` T3 (settings), `12bb9f3` T4 (read path + fakes), `5612854` T5 (stale guard + empty path),
  plus the closing commit that carries this record and the architecture update.
- Authored changed lines per commit (additions + deletions, `uv.lock` excluded): 197, 234, 130, 570, 144.
  The per-commit sum (1275) exceeds the T1-T5 range total (`git diff --numstat 7730db6..5612854`,
  `uv.lock` excluded: **1249 added, 0 deleted**) because lines rewritten by a later commit are counted
  twice, e.g. the application `__init__.py` and the feature document. The closing documentation commit
  that carries this record sits outside that count.
- T4 is the largest commit because it carries the fakes every later test reuses. It stays one behaviour
  (the happy path): splitting the fakes from their first consumer would only create a commit with dead
  test infrastructure.
- The whole feature is over the ~400-line review slice. Every commit is green on its own, so native
  review can take any one of them.
- The harness slice warning counts authored lines against `origin/main` for the whole branch, not per
  task.
- The round-1 fixes add `2499f96` (code and tests) plus the documentation commit that carries the round
  record.

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
- T4: RED → `ImportError: cannot import name 'AnswerQuestion' from 'poe2_rag.answering.application'`,
  0 tests run (13 written). GREEN → **403 passed** (13 new).
  - `execute` embeds first and hands the embedder's vector, never the raw text, to `VectorIndex`: the port
    takes a vector, so the embedder is the only source of it.
  - An unknown version surfaces the domain's `InvalidQueryError` from `Query.for_catalog`; no application
    error wraps it, so a bad query has one error type and no port is called (a test asserts the journal
    stayed empty).
  - The final slice (`reranked[:final_top_k]`) keeps the budget even when an adapter ignores `limit`; the
    test that pins it drives the reranker fake with `returned=` to make it over-deliver.
  - The fakes share one `CallRecorder`, so a test asserts the whole call order without duplicating state
    per fake; the journal is `Any`-typed on purpose, and that looseness stays inside `tests/`.
  - `AnswerResult` carries no validation: only `execute` builds it, from values the domain already
    validated, so a second check would only repeat the domain's rules.
- T5: RED → **5 failed, 403 passed** (5 new behaviour tests): the stale chunks travelled through fusion
  and the empty runs still called the model. GREEN → **408 passed** (5 new).
  - Each search is guarded immediately, so a broken lexical index stops the run before the dense search;
    the test asserts the journal ends at the broken call. The reranker output is guarded as well: a stale
    chunk there can only come from an adapter that invented one, so the fake injects it through
    `returned=`.
  - The `final_top_k` slice happens *after* the guard, so a stale chunk beyond the budget still fails
    loud instead of disappearing with the slice.
  - Both empty paths (no fused candidates, empty rerank) return the fixed text through one
    `_unsupported` helper and never call the model; an empty fusion also skips the reranker, which cannot
    create candidates. One test pins the exact message, so changing it is a deliberate edit.

## Tooling notes
- The gentle-pi lens reports `Import "pytest" could not be resolved` and `Import block is un-sorted` on
  these test files in this worktree. Both are analyzer-environment defects, not code defects:
  `effective_config` shows the per-file analysis loads no project configuration (`documents: []`, every
  provenance count `0`), so its Pyright never sees the committed `pyrightconfig.json` (`venvPath`, `venv`,
  `extraPaths: ["src"]`) and its isort classifies `poe2_rag` as third-party. The authoritative checks in
  this worktree are green: `.venv/bin/python -c "import pytest"` → `9.2.0`, `uv run pytest -q` → 408
  passed, `uvx pyright==1.1.414 src/poe2_rag/answering/application tests/unit/answering/application` →
  `0 errors, 0 warnings, 0 informations`, and `uvx ruff check --select I` → `All checks passed`. Following
  the analyzer's hints (grouping `poe2_rag` with `pytest`) would break `ruff`, the tool the repository
  actually uses, so no code change was made.
- The lens also emits `Potential SQL injection sink` advisories on `pipe.use_case.execute(...)`. The rule
  that fires is `no-sql-in-code-execute` (TypeScript) and its own header documents `.execute` as a
  method-name false-positive class. There is no SQL, cursor or database call in the repository.

## Close
- Final `uv run pytest -q` → **408 passed** (baseline on `main`: 344; **+64 tests**).
- `uvx pyright==1.1.414` (bare, project config) → `0 errors, 0 warnings, 0 informations`.
- `uvx ruff check --select I src/poe2_rag/answering/application tests/unit/answering/application` →
  `All checks passed`.
- Import boundary (S5): every import in `src/poe2_rag/answering/application/*.py` is `__future__`,
  `collections.abc`, `dataclasses`, `typing`, `poe2_rag.answering.domain*`, `poe2_rag.shared.domain` or a
  sibling module of the package. No third party, no ingestion, no adapter.
- Per-spec verdict:
  - S1 ✅ five `typing.Protocol` ports with the requested signatures, plus `GeneratedAnswer(text,
    citations)` as a frozen slotted dataclass that validates shape only.
  - S2 ✅ `RetrievalSettings(lexical_top_n, dense_top_n, rerank_top_m, final_top_k, rrf_k=60)`, all
    positive ints, `final_top_k <= rerank_top_m`, `InvalidRetrievalSettingsError` otherwise.
  - S3 ✅ `AnswerQuestion.execute` runs the seven steps; every step is named by at least one test.
  - S4 ✅ `AnswerQuestionError` subclasses `ValueError`, not `DomainError`; `InvalidGeneratedAnswerError`,
    `InvalidRetrievalSettingsError` and `StaleChunkError` derive from it.
  - S5 ✅ see the import boundary above.
  - S6 ✅ RED observed before every implementation, then GREEN; counts per task above.
  - S7 ✅ fakes in `tests/unit/answering/application/answering_fakes.py`, sharing one `CallRecorder`.
  - S8 ✅ every required case is a named test: default and explicit version, unknown version, both
    indexes receiving the version, single-ranking survivor, `final_top_k`, empty retrieval skipping the
    model, undelivered citation, a stale chunk from each stage, zero citations.
  - S9 ✅ no adapter, no `bootstrap.py`, no CLI command, no entity detection, no concurrency.
  - S10 ✅ code, tests, the feature document and every commit message are English;
    `docs/architecture.md` stays Spanish because the whole file is (L2).
  - S11 ✅ branch from `main`, one Conventional Commit per task, no attribution, nothing pushed or opened.
  - S12 ✅ the numbers above, per task.
- `Risk: none`. The feature adds modules and ports; it changes no stored data, no credential or guard, no
  existing contract (nothing consumes these modules yet), no concurrency and no delivery path, and every
  rule it introduces has a test that fails without it.
- No gate was skipped. The two lens findings (`import pytest` unresolved, import block "un-sorted") are
  analyzer defects documented in Tooling notes, with the commands that prove the files resolve and sort.

## Round 1 — QA and review findings

Ten findings from the round-1 QA and review passes, all applied on this branch in `2499f96`.

- R1 (QA) The reranker could deliver a chunk that no index retrieved, and a citation to it counted as
  supported. `execute` now requires the reranked ids to be a subset of the candidates it passed, raising
  `UnretrievedChunkError` (two tests: within and beyond the budget). Being valid is not enough: the
  delivered context must come from retrieval (D3).
- R2 (QA) The documented order "guard before the `final_top_k` slice" had no test: slicing first left the
  suite green. Two tests now put the offending chunk beyond the budget, one stale and one foreign.
  Mutation-checked: moving `delivered = reranked[:k]` before `_require_reranked` fails both.
- R3 (QA) Duplicate ids from one index escaped `execute` as the domain's `InvalidRankingError`, outside
  the application taxonomy. Each index result and the reranker output are checked with `_require_distinct`
  and `DuplicateChunkError`, before fusion.
- R4 (QA) The ports promise each stage's score and nothing enforced it. Every stage result goes through
  `_require_stage_score`; a missing `lexical`, `dense` or `rerank` score raises `MissingScoreError`, which
  is what keeps the delivered context attributable for the §8 evaluation.
- R5 (QA) `docs/architecture.md` §5.1 step 3 still said "en paralelo". It now states that the MVP runs
  the two searches sequentially and that parallelism is an adapter decision.
- R6 (QA) The fusion comment promised that equal `chunk_id` means the same chunk, but the id does not
  cover text or `valid_until`. `_fuse` compares the two views and raises `ChunkMismatchError` when they
  differ, instead of silently keeping the lexical one.
- R7 (QA) `from fakes import ...` relied on pytest's `sys.path` insertion and would collide with the next
  capability's `fakes.py`. The module is now `answering_fakes.py`, and its docstring says why.
- R8 (review) `README.md` still described the pre-feature state. The status line and the roadmap now
  mention `AnswerQuestion` and its ports.
- R9 (review) The "model sees exactly the delivered slice" criterion was untested: passing `reranked`
  instead of `delivered` kept the suite green. A test now asserts the `generate` context and that a
  citation to the chunk cut by the budget raises `InvalidCitationError`; mutation-checked.
- R10 (review) No test used a `rrf_k` other than 60, so `_fuse` could ignore the setting. A test with
  `rrf_k=10` pins `fused=2/11`; mutation-checked.

The eleven new tests live in `tests/unit/answering/application/test_answer_question.py`; the taxonomy test
in `test_ports.py` now covers all seven `AnswerQuestionError` subclasses. Suite: 408 → **419 passed**.
New errors: `UnretrievedChunkError`, `DuplicateChunkError`, `MissingScoreError`, `ChunkMismatchError`,
all `AnswerQuestionError` subclasses.

## Next step
The user's decision, not taken here: push `feat/answer-question` and open the PR against `main`. Nothing
was pushed and no PR was opened. The natural follow-up is the query adapters (BM25, Chroma, cross-encoder,
LLM) behind the port contract suite of §8: they are the only reading work left before `bootstrap.py` can
wire a real pipeline.

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

### L2 — implementation decisions (not fixed by L1)

1. `settings.py` and `errors.py` are separate modules in `answering/application`: L1 named `ports.py` and
   `answer_question.py` and left the rest to the package style (one responsibility per module).
2. `execute` embeds the query before searching. `VectorIndex.search` takes a vector, so `Embedder` is the
   only source of it and the dense index never receives raw text.
3. The D1 guard runs immediately after each search and after the rerank, and before the `final_top_k`
   slice. "Any returned chunk" covers the reranker: a stale chunk there can only come from an adapter
   that invented it, and slicing first could hide it.
4. Both empty paths share one `_unsupported` helper; the empty-fusion path also skips the reranker, which
   cannot create candidates, and no path reaches the model without delivered chunks.
5. The use case does not validate its ports in the constructor. It is wired once in `bootstrap.py` (a
   future task) and an incompatible port fails on the first call; a runtime Protocol check would add
   ceremony without protecting a domain invariant.
6. `RetrievalSettings.rrf_k` defaults to the domain's `DEFAULT_K`, so the settings cannot drift from the
   fusion default while a test still pins the value at `60`.
7. `docs/architecture.md` is edited in Spanish: it is the repository's existing design document and ADR
   index, and a bilingual file would read worse. Everything new (code, tests, feature document, commits)
   is English, as L1 asks.
