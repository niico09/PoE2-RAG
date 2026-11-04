# Feature: poe2db-level-blocks

Split the poe2db per-level gem table into consecutive row blocks, so no gem chunk exceeds the
512-token window that `bge-base-en-v1.5` and `bge-reranker-base` read, test-first and offline by
default.

## Specs

- **S1** — "Replace the single "levels" section with consecutive row blocks."
  - "Section ids are slug-safe and sortable, e.g. `levels-01-10`, `levels-11-20` (zero-padded to the
    table's max level width). Confirm they satisfy the Chunk section slug rule."
  - "The block size is a parser constructor parameter with a default (10 rows), validated (>= 1)."
- **S2** — "Every block is self-describing, because the embedder and the reranker read the text alone:
  - first line: the gem name and the level range (e.g. "Spark — levels 11–20");
  - then the table header, then the rows, verbatim.
  - Numbers and wording stay exact, because citations quote this text."
- **S3** — "No row is dropped or duplicated across blocks. The last block may be shorter. A table with
  no data rows produces no levels chunk, as today."
- **S4** — "Budget guard: prove with the real tokenizer that the default block size keeps the worst
  observed case (the Spark fixture) under 512 tokens with margin. Do this in an integration test
  (marked `integration`) that loads only the tokenizer. If 10 rows do not fit, lower the default and
  record the measured token counts."
- **S5** — "Re-ingesting a gem after this change produces new section ids at the same valid_from. The
  snapshot-supersession rule (feat/snapshot-supersession) must then remove the old "levels" chunk.
  Confirm this in a test; do not assume it."
- **S6** — "Update odd/tasks/poe2db-source.md: D1 is superseded by this feature."
- **S7** — Testing requested, verbatim:
  - "Parser unit tests against the existing trimmed fixtures:
    - block count and ranges for 40 rows with block size 10, and for a t[able that is not a]
      multiple of it;
    - header and title repeated in each block;
    - the union of the block rows equals the original rows, in order;
    - invalid block size → a specific error."
  - "Application test with fakes: ingesting a gem whose stored snapshot has the old single "levels"
    section at the same valid_from removes it and adds the blocks".
  - "Integration:
    - the tokenizer budget test above;
    - re-ingest a fixture gem through the real pipeline and assert ReindexReport.truncated_chunk_ids
      is empty.
    - Run once and report the token counts."
- **S8** — Constraints: "Domain and application layers stay untouched" (if supersession did not hold,
  stop and report); "TDD: tests first, observe RED, implement GREEN, refactor while green"; "ONE
  CONVENTIONAL COMMIT PER TASK, in task order, each leaving the suite green"; "Code, tests, docs, the
  feature document and commit messages in English only"; "Never commit data/raw, data/processed,
  data/index or full captures"; "About 400 authored changed lines per task is a planning heuristic,
  not a cap"; out of scope: "chunking of other sections, patch-notes chunking, retr[ieval]".

## Tasks

- [x] T1 — Parser: consecutive self-describing row blocks, block size as a validated constructor
  parameter, unit tests against the fixtures and 40-row tables. Route: inline. S1, S2, S3, S7, S8.
- [x] T2 — Application evidence with fakes: the stored single `levels` chunk at the same `valid_from`
  is removed and the blocks are added. Route: inline. S5, S7.
- [x] T3 — Integration budget guard: the real tokenizer over the 40-row worst case, measured counts.
  Route: inline. S4, S7.
- [x] T4 — Integration re-ingest through the real pipeline: `ReindexReport.truncated_chunk_ids` is
  empty. Route: inline. S4, S7.
- [ ] T5 — Docs: `poe2db-source.md` D1 superseded, architecture note, measured evidence, full suite and
  integration run. Route: inline. S6, S8, S4.

## Evidence

- **T1** — RED observed after authoring the tests, before touching the parser: `ImportError: cannot
  import name 'InvalidLevelBlockSizeError' from 'poe2_rag.ingestion.adapters.outbound.poe2db.
  gem_page_parser'` during collection, 0 cases run. GREEN: 46 passed in the parser suite after the
  first round of gate findings added the positional-id branch and the repeated-label case; 1391 passed,
  18 deselected in the offline suite; 15 passed, 1394 deselected with `-m integration` (25.97 s).
  Commit hash: recorded in T5's docs commit.
- **T2** — No RED: the rule already exists in `IngestSource` (`_removed_at_the_same_version`, shipped
  with `feat/snapshot-supersession`), so this is a characterization test and it passed on the first
  run. GREEN: 32 passed in `test_ingest_source.py`, 1393 passed / 18 deselected offline. Same diff
  closes the T1 review minors: the level-label pattern is bounded to four digits so a corrupt cell
  cannot reach `int()`, `build_parser` takes the real default instead of a `None` sentinel, the test
  helpers are typed, and the module docstring points at the durable D1 record. Commit hash: recorded
  in T5's docs commit.
- **T3** — RED against `150887e` (base, via `git archive` into `/tmp`): the same 40-row page parses to one
  `levels` section and the budget test fails (`ValueError: not enough values to unpack`). Measured with
  the real `bge-base-en-v1.5` tokenizer, one block of 40 rows = **1,283 tokens**, over the 512 window.
  GREEN over the six captures with 10-row blocks: Spark 357, Fireball 291, Lightning Arrow 266, Ice Nova
  380, Arc 251, Raging Spirits 182; the generated 40-row page (rows at least as wide as the widest
  observed, 73 vs 72 chars) measures **353 tokens max, margin 159**. `uv run pytest -q -m integration
  -s` prints `one block of 40 rows: 1283 tokens, budget 512` and `4 blocks of 10 rows: generated proxy
  max 353 tokens (margin 159); observed worst 380 tokens (margin 132)`. The budget reads
  `DEFAULT_MAX_SEQUENCE_LENGTH` and counts through `bge_embedder.token_lengths`, the batch function
  `overflowing_indices` uses (covered offline in `test_bge_embedder.py`), so the guard cannot drift from
  the window the index enforces; the `transformers` import lives inside the tokenizer fixture, so
  collecting the offline suite no longer pays for it (3.82 s → 2.34 s). Commit hash: recorded in T5's
  docs commit.
- **T4** — RED against `150887e`: the same 40-row page ingests to 3 chunks and the real reindex reports
  `Truncated chunk ids: poe2db:active_gem:spark@0.1.1#levels.` GREEN with the blocks: 6 chunks, the four
  `levels-*` sections in order, the 40 rows once and in order across them, no `Truncated chunk ids` line
  and `ReindexReport.truncated_chunk_ids == ()`; re-ingesting the same page reports `0 removed` and keeps
  one set of blocks. Commit hash: recorded in T5's docs commit.

## Log

### L1 — Original request (verbatim)

> Split the poe2db per-level table into retrievable row blocks so no gem chunk exceeds the embedding
> and reranking window, test-first.
>
> ## Context (read first)
> - Observed on 2026-11-04 with the first real ingest of six gems (Spark, Fireball, Lightning Arrow,
>   Ice Nova, Arc, Raging Spirits). Every `#levels` chunk was reported in
>   ReindexReport.truncated_chunk_ids. The Spark one is 2,993 chars / 873 words: a header plus about
>   40 rows and 9 columns.
> - BAAI/bge-base-en-v1.5 and BAAI/bge-reranker-base read at most 512 tokens, so rows past roughly
>   the first third never reach dense retrieval or reranking. Only BM25 sees them, and a question
>   like "Spark damage at level 15" is exactly the numeric case D2 protects.
> - Code:
>   - ingestion/adapters/outbound/poe2db/gem_page_parser.py: `_level_table` (around line 365) renders
>     the table as a single "levels" section;
>   - odd/tasks/poe2db-source.md D1 records that choice and its rationale;
>   - ingestion/application/reindex_corpus.py already detects truncation through the embedder.
> - Re-ingesting a gem after this change produces new section ids at the same valid_from. The
>   snapshot-supersession rule (feat/snapshot-supersession) must then remove the old "levels" chunk.
>   Confirm this in a test; do not assume it.
>
> ## Required behavior
> 1. Replace the single "levels" section with consecutive row blocks.
>    - Section ids are slug-safe and sortable, e.g. `levels-01-10`, `levels-11-20` (zero-padded to the
>      table's max level width). Confirm they satisfy the Chunk section slug rule.
>    - The block size is a parser constructor parameter with a default (10 rows), validated (>= 1).
> 2. Every block is self-describing, because the embedder and the reranker read the text alone:
>    - first line: the gem name and the level range (e.g. "Spark — levels 11–20");
>    - then the table header, then the rows, verbatim.
>    - Numbers and wording stay exact, because citations quote this text.
> 3. No row is dropped or duplicated across blocks. The last block may be shorter. A table with no
>    data rows produces no levels chunk, as today.
> 4. Budget guard: prove with the real tokenizer that the default block size keeps the worst observed
>    case (the Spark fixture) under 512 tokens with margin. Do this in an integration test (marked
>    `integration`) that loads only the tokenizer. If 10 rows do not fit, lower the default and record
>    the measured token counts.
> 5. Update odd/tasks/poe2db-source.md: D1 is superseded by this feature
>
> ## Testing
> - Parser unit tests against the existing trimmed fixtures:
>   - block count and ranges for 40 rows with block size 10, and for a t
>     multiple of it;
>   - header and title repeated in each block;
>   - the union of the block rows equals the original rows, in order;
>   - invalid block size → a specific error.
> - Application test with fakes: ingesting a gem whose stored snapshot has the old single "levels"
>   section at the same valid_from removes it and adds the blocks (repor
> - Integration:
>   - the tokenizer budget test above;
>   - re-ingest a fixture gem through the real pipeline and assert ReindexReport.truncated_chunk_ids
>     is empty.
>   - Run once and report the token counts.
>
> ## Rules
> - Domain and application layers stay untouched. If supersession n
>   stop and report it.
> - TDD: tests first, observe RED, implement GREEN, refactor while green
> - ONE CONVENTIONAL COMMIT PER TASK, in task order, each leaving the suite green. Commit inside the
>   worker as each task closes. Do not leave the tasks for a single fina
> - Code, tests, docs, the feature document and commit messages in English only.
> - Never commit data/raw, data/processed, data/index or full captures.
> - About 400 authored changed lines per task is a planning heuristic, not a cap.
> - Out of scope: chunking of other sections, patch-notes chunking, retr
>
> ## Allowed edit surfaces
> src/poe2_rag/ingestion/adapters/outbound/poe2db/**
> src/poe2_rag/bootstrap.py
> tests/unit/**
> tests/integration/**
> tests/fixtures/poe2db/**
> odd/tasks/poe2db-level-blocks.md
> odd/tasks/poe2db-source.md
> docs/architecture.md
>
> ## Delivery
> - Branch feat/poe2db-level-blocks from main. Feature doc odd/tasks/poe2db-level-blocks.md with task
>   IDs, the route per task, RED/GREEN evidence and the commit hash per
> - No Co-Authored-By or any AI attribution.
> - Do not push or open a PR.
>
> ## Verification
> - `uv run pytest -q` passes (offline default suite).
> - `uv run pytest -q -m integration` result reported separately, with t
> - `git log --oneline main..HEAD` shows one commit per task.
> - Report `<command>: <observed result>` and RED/GREEN counts per task.
>
> ## Return
> Concise report: tasks done, commits, test counts, the final default block size with its token
> evidence, deviations from this prompt and why.
> Close with a `## Key Learnings` block of 1–5 numbered standalone facts.

### L2 — The prompt arrived truncated in a few places

The request lost a few line ends (`for a t multiple of it`, `(repor`, `If supersession n`, `a single
fina`, `with t`, `retr`, `per`). Each one is read as the obvious continuation (a table that is not a
multiple of the block size; `report`; supersession not holding; a single final commit; `with the
counts`; retrieval; `per task`) and implemented that way; nothing in those gaps changes a decision.

### L3 — Worst observed case is Ice Nova, not Spark

Measured over the six captures in the local (gitignored) `data/raw/poe2db/` with the real tokenizer,
the whole-table token counts are Spark 1282, Fireball 1059, Lightning Arrow 935, Ice Nova 1315, Arc
887, Raging Spirits 615. With 10-row blocks the maxima are Spark 357 and Ice Nova 380, so Spark is not
the widest table: Ice Nova is. The budget guard uses the wider shape and reports both.

### L4 — A 40-row page is generated in the tests, not committed

The committed `active_gem_spark.html` is a two-row excerpt (L6 of `poe2db-source.md`) and a full
capture may not be committed (ADR-0002). The 40-row tables in the tests are therefore built from the
committed fixture's own header plus rows wide enough to match the observed worst row, so the budget is
measured on the shape the parser really sees without redistributing more of the corpus.

### L5 — A header-only level table produces a chunk today

S3 says "A table with no data rows produces no levels chunk, as today". Today a table with a header
and no data rows returns the header line, and `_sections` turns it into a `levels` chunk containing
only `Level | Mana`. The requirement is implemented (no rows, no chunk); the parenthetical is wrong
about the current behavior and is recorded here instead of silently preserved.

### L7 — One file outside the allowed surfaces

The T3 review asked for the budget guard to count tokens through the same rule
`BgeEmbedder.overflowing_indices` uses instead of a copy, and to read the window from the adapter
rather than restating 512. That is a small extraction in
`src/poe2_rag/answering/adapters/outbound/dense/bge_embedder.py` (private `_token_lengths`, public
`count_tokens`, `overflowing_indices` reusing them), outside the listed edit surfaces but inside the
layer the prompt protects only for domain and application. The dense adapter's own 147 unit tests and
the 19 integration tests stay green, and no public behaviour changed: same flags, same batch call,
same `overflowing_indices` contract.

### L6 — `docs/architecture.md` stays Spanish

The new artifacts (code, tests, feature doc, commits) are English. `docs/architecture.md` is a Spanish
document from end to end, so the one stale sentence is corrected in Spanish to keep the file coherent;
ADR-0001 governs the corpus, queries, answers and eval dataset, not the documentation language.
