# Feature: patch-notes-chunking

Make the forum patch-notes parser handle Content Update threads (banner heading, no `h1`) and keep
every notes chunk inside the 512-token window that `bge-base-en-v1.5` and `bge-reranker-base` read,
test-first and offline by default.

## Context

- Real ingest on 2026-11-06: thread 4000864 (0.5.5) fails with `no thread title found`; thread
  4006357 (0.5.5c) parses to one 562-token `notes` chunk with no headings, which `ReindexReport`
  reports as truncated.
- `data/raw/patch_notes/` is git-ignored (CC BY-NC-SA captures, ADR-0002). The captures live in the
  main worktree, so fixtures here are trimmed excerpts, never full captures.

## Specs

- **S1 — Title fallback.** Verbatim: "Title: keep the topBar `<h1>` lookup. When there is none, fall
  back to og:title, then `<title>`. Version extraction already tries every token."
- **S2 — Banner heading and navigation.** Verbatim: "Banner heading: when the shallowest level holds
  exactly one heading and it comes before the others, treat it as a banner and split on the next
  level. Drop the "Table of Contents" section and the "Report Forum Post" heading as navigation, the
  same way the title heading is dropped today. Confirm both against the real page."
- **S3 — Size budget.** Verbatim: "Size budget: split any section whose text exceeds a token budget
  into consecutive blocks. Split at block boundaries (paragraphs, list items), never mid-sentence.
  Each block repeats a context line ("Patch 0.5.5 — Endgame Changes (part 2/4)"), because the
  embedder and the reranker see the text alone. Section ids stay slug-safe and sortable
  (`endgame-changes-01`). A section that fits keeps its current id. The no-heading case (0.5.5c)
  follows the same rule under `notes`."
- **S4 — Budget guard.** Verbatim: "The budget is a parser constructor parameter with a default,
  validated (>= 1). An integration test (marked `integration`) proves with the real tokenizer that
  the default keeps the worst real block under 512 tokens with margin. Report the measured token
  counts."
- **S5 — Verbatim text.** Verbatim: "Wording and numbers stay verbatim: citations quote this text.
  No text is dropped or duplicated across blocks, except the repeated context line."
- **S6 — Coexistence.** Verbatim: "Add the coexistence test: ingesting a 0.5.5 thread and then a
  0.5.5e thread through IngestSource with in-memory fakes leaves both open, and re-capturing the same
  thread supersedes it."
- **S7 — Testing.** Verbatim: "Use trimmed fixtures taken from the cached pages (a few sections,
  enough to exercise each rule). Never commit a full capture." Unit tests: "title fallback order,
  banner detection (and a page without a banner keeps today's behavior), navigation sections dropped,
  block ids/context lines/union of text equal to the original, invalid budget raises a specific
  error." Integration: "the tokenizer budget test above; parse the trimmed 0.5.5 and 0.5.5c fixtures
  through the real embedder and assert no truncated chunk ids."
- **S8 — Constraints.** Domain and application layers stay untouched (stop and report if one must
  change). TDD with observed RED. Code, tests, feature doc and commit messages in English;
  `README`/`docs/architecture.md` stay Spanish. Never commit `data/raw`, `data/processed`,
  `data/index`, `.env` or full captures. Out of scope: real-corpus ingest, poe2db, eval dataset,
  retrieval tuning.

## Purposes of the new rules (why)

- The Content Update thread has **no `h1`**; the title only lives in `<title>`/`og:title`, and the
  staff post uses the `tr.newsPost` layout (`tr.newsPost > td > div.content`) instead of
  `tr.staff > td.content-container > div.content`. Both defects sit on the same page, so a title fix
  alone would surface `no staff post body found` next.
- Every section is wrapped in `div.lbox-container > div.lbox > div.box-content`, so headings are
  **not** direct children of `div.content`. The parser therefore flattens layout wrappers that carry
  a heading before it looks for split levels; without that, the whole 30k-char thread is one `notes`
  chunk.
- The budget is measured with a tokenizer-free estimate inside the adapter (the default suite must
  stay offline and dependency-free); the integration test is what proves the estimate is
  conservative with the real tokenizer.

## Tasks

- [x] T1 — Title fallback (`h1` → `og:title` → `<title>`) and the `tr.newsPost` body shape. Route:
  inline. S1, S7, S8.
- [x] T2 — Layout flattening, banner detection, navigation sections dropped, split on the level below
  the banner. Route: inline. S2, S5, S7, S8.
- [x] T3 — Token-budgeted blocks: `max_block_tokens` constructor parameter (validated `>= 1`),
  context line, sortable ids, `notes` case. Route: inline. S3, S4, S5, S7, S8.
- [x] T4 — Integration: real tokenizer budget proof over the trimmed captures, and the real embedder
  over the trimmed 0.5.5/0.5.5c fixtures with no truncated chunk ids. Route: inline. S4, S7.
- [x] T5 — Coexistence: 0.5.5 then 0.5.5e through `IngestSource` with in-memory fakes, recapture
  supersedes. Route: inline. S6, S7.
- [x] T6 — Docs (`docs/architecture.md`, Spanish), full suite and integration runs, evidence and
  measured counts. Route: inline (the `docs/architecture.md` rewrite was delegated to
  `gentle-ai-worker` with a bounded brief). S8.

Commit per task: **pending the Herdr flow gate**, see L2. The work-unit split is T1-T6 in the order
above, each leaving the suite green.

## Evidence

### Baseline (before T1)

- `PatchNotesParser().parse()` over the five real captures: 4000864 fails with
  `no thread title found`; 4004106 (0.5.5b), 4006357 (0.5.5c), 4008534 (0.5.5d) and 4009785 (0.5.5e)
  parse to a single `notes` chunk.
- 4006357: one `ul` of 2,540 chars / 562 tokens, no section headings — the truncation
  `ReindexReport` reported.
- 4000864: 30,035 chars in the first staff post, `h1` count 0, heading skeleton `h2` banner
  ("Content Update 0.5.5 — Path of Exile 2: Forbidden Rites") + ten `h3` sections, each inside its
  own `div.lbox-container` wrapper.
- Navigation, confirmed on 4000864: `h3` "Table of Contents" (220 chars of anchor list) is inside the
  staff post; the `h3` "Report Forum Post" lives in a page-chrome `div.layoutBoxContent` box outside
  the post body, next to its `<form>`, so the parser never renders it. The drop rule is applied to
  both slugs anyway and pinned by a fixture.

### T1 — Title fallback and the news-post body

- RED: 4 failed, 36 passed in `tests/unit/ingestion/adapters/outbound/test_patch_notes_parser.py`
  (`no thread title found` on the Content Update fixture; the fallback fixtures and the news-post body
  test failed the same way).
- GREEN: 40 passed in that file; 1486 passed, 25 deselected in the offline suite.
- Fixtures: `content_update_thread.html` (no `h1`, `og:title` + `<title>`, `tr.newsPost > td >
  div.content`, one `h2` banner over `h3` sections, `style` block, ToC, page-chrome
  "Report Forum Post" box), `og_title_only_thread.html`, `title_element_only_thread.html`,
  `h1_precedes_meta_thread.html`.

### T2 — Flattening, banner and navigation

- RED: 7 failed, 40 passed (banner detection, navigation drops, and the "keeps today's split" case).
- GREEN: 47 passed in the parser suite; 1493 passed, 25 deselected offline.
- The fixture lesson: today's split emits a synthetic `overview` section for the preamble that
  precedes the first real heading, so the "no banner keeps today's behavior" assertion pins
  `overview`, `general-changes`, `skill-changes` for `full_020_thread.html`.
- New fixture: `navigation_headings_thread.html` (title repeat + ToC + real section + report heading
  + a section after it, so the skip is proven to end).

### T3 — Token-budgeted blocks

- RED: collection error, 0 cases run (`ImportError: cannot import name 'InvalidTokenBudgetError'`).
- GREEN: 55 passed in the parser suite; 1501 passed, 25 deselected offline.
- New fixtures: `oversized_section_thread.html` (15 real bullets of "Trial of Chaos Improvements",
  3,420 chars) and `notes_only_thread.html` (15 real bullets of 0.5.5c, 2,442 chars, no headings).
- Two estimator corrections came out of measurement, not guesswork: `chars/4 + words*4/3` undercounted
  the widest real block (real 418 vs estimate 388) and then the shortest one (real 42 vs estimate 39,
  1.83 tokens/word). The final constants are 3.6 characters per token and 1.9 tokens per word, and
  no block of the five captures or the fixtures estimates below its real count.

### T4 — Real tokenizer and real embedder

- `uv run pytest tests/integration/test_patch_notes_chunk_budget_integration.py -q -m integration -s`:
  4 passed in 4.05 s.
- Unblocked shape (the defect this replaces): `oversized_section_thread.html` 696 tokens,
  `notes_only_thread.html` 550 tokens, both over the 512 window.
- Default blocks: `content_update_thread.html` 2 blocks, max 39 tokens (margin 473);
  `oversized_section_thread.html` 4 blocks, max 248 (margin 264); `notes_only_thread.html` 3 blocks,
  max 264 (margin 248). Worst default block over the fixtures: **264 tokens**.
- Through the real `BgeEmbedder`: 9 chunks, `overflowing_indices` empty, so
  `ReindexReport.truncated_chunk_ids == ()`.
- On the five real captures (manual run, nothing committed): 4000864 → 32 chunks, worst **326 tokens**;
  4006357 → 3 chunks (`notes-01/02/03`), worst 264; 4008534 → 2 chunks (`notes-01/02`), worst 262;
  4004106 → 1 `notes` chunk, worst 210; 4009785 → 1 `notes` chunk, worst 76. No CSS, ToC or report
  text survives into any chunk.
- The estimate over-splits a short-word page on purpose: 4008534's whole `notes` text measures 262 real
  tokens per block but ~647 estimated, so a text that would fit the 512 window is still cut in two.
  The error always points at a block too small, never at a block over the window: the measured worst
  block stays at 326 with 122 tokens of headroom.

### T5 — Coexistence

- No RED: the rule already exists (`_removed_at_the_same_version`, shipped with
  `feat/snapshot-supersession`), so this is a characterization test over the real parser and two
  captures; it passed on the first run. GREEN: 2 passed in
  `tests/unit/ingestion/application/test_ingest_patch_notes_coexistence.py`; 1503 passed, 29
  deselected offline.
- New fixtures: `suffixed_version_e_thread.html` (0.5.5e, thread 4009785) and
  `content_update_thread_edited.html` (the same 0.5.5 post with the "Endgame Changes" section removed).

### T6 — Docs and final runs

- `docs/architecture.md` (Spanish): §5.2 parse step, the patch-notes risk row, and the entity/chunk
  shape list now describe the title fallback, the news-post layout, the banner, the navigation drop
  and the token blocks with their measured numbers. The stale "a deleted section stays in the corpus"
  row is replaced by the behavior `_removed_at_the_same_version` implements, which T5 pins.
- `uv run pytest -q`: 1503 passed, 29 deselected in 5.72 s.
- `uv run pytest -q -m integration`: 25 collected, 24 passed, 1 failed —
  `test_hotfix_versions_integration.py::test_two_hotfix_captures_supersede_each_other_and_both_indexes_follow`,
  see L5. That failure predates this feature.
- `uv run ruff check` on every touched Python file: all checks passed (the 10 findings elsewhere are
  pre-existing and untouched).

## Log

### L1 — Original request (verbatim)

> Make the patch-notes parser handle Content Update threads and keep every patch-notes chunk inside
> the 512-token embedding and reranking window. Work test-first.
>
> ## Context (read first)
> - The real ingest on 2026-11-06 exposed two defects in
>   src/poe2_rag/ingestion/adapters/outbound/patch_notes_parser.py.
> - 0.5.5 (https://www.pathofexile.com/forum/view-thread/4000864) fails with "no thread title found".
>   The cached page is data/raw/patch_notes/www.pathofexile.com-forum-view-thread-4000864.html; read
>   it locally and do not re-fetch it.
>   - Content Update threads have no <h1>. The title exists only in <title> and og:title
>     ("Early Access Patch Notes - 0.5.5 Patch Notes - Forum - Path of Exile").
>   - The staff post opens with one banner <h2> ("Content Update 0.5.5 — Path of Exile 2: Forbidden
>     Rites"), followed by <h3> sections: Table of Contents, The Forbidden Rites Event League, Ritual
>     Changes, ..., Bug Fixes, and a trailing "Report Forum Post".
>   - The parser splits on the shallowest heading level, so the whole thread, about 31,700 chars,
>     would become one chunk.
> - 0.5.5c (thread 4006357) parses to one 562-token notes chunk with no headings, and ReindexReport
>   flags it as truncated.
> - BAAI/bge-base-en-v1.5 and bge-reranker-base read at most 512 tokens. Anything past that never
>   reaches dense retrieval or reranking. The poe2db level table had the same problem; see
>   odd/tasks/poe2db-level-blocks.md for the accepted approach (self-describing blocks, budget proven
>   with the real tokenizer).
>
> ## Required behavior
> 1. Title: keep the topBar <h1> lookup. When there is none, fall back to og:title, then <title>.
>    Version extraction already tries every token.
> 2. Banner heading: when the shallowest level holds exactly one heading and it comes before the
>    others, treat it as a banner and split on the next level. Drop the "Table of Contents" section and
>    the "Report Forum Post" heading as navigation, the same way the title heading is dropped today.
>    Confirm both against the real page.
> 3. Size budget: split any section whose text exceeds a token budget into consecutive blocks. Split at
>    block boundaries (paragraphs, list items), never mid-sentence. Each block repeats a context line
>    ("Patch 0.5.5 — Endgame Changes (part 2/4)"), because the embedder and the reranker see the text
>    alone. Section ids stay slug-safe and sortable (`endgame-changes-01`). A section that fits keeps
>    its current id. The no-heading case (0.5.5c) follows the same rule under `notes`.
> 4. The budget is a parser constructor parameter with a default, validated (>= 1). An integration test
>    (marked `integration`) proves with the real tokenizer that the default keeps the worst real block
>    under 512 tokens with margin. Report the measured token counts.
> 5. Wording and numbers stay verbatim: citations quote this text. No text is dropped or duplicated
>    across blocks, except the repeated context line.
> 6. Add the coexistence test: ingesting a 0.5.5 thread and then a 0.5.5e thread through IngestSource
>    with in-memory fakes leaves both open, and re-capturing the same thread supersedes it.
>
> ## Testing
> - Use trimmed fixtures taken from the cached pages (a few sections, enough to exercise each rule).
>   Never commit a full capture.
> - Unit tests: title fallback order, banner detection (and a page without a banner keeps today's
>   behavior), navigation sections dropped, block ids/context lines/union of text equal to the
>   original, invalid budget raises a specific error.
> - Integration:
>   - the tokenizer budget test above;
>   - parse the trimmed 0.5.5 and 0.5.5c fixtures through the real embedder and assert no truncated
>     chunk ids.
>
> ## Rules
> - Domain and application layers stay untouched. If one must change, stop and report why.
> - TDD: tests first, observe RED, implement GREEN, refactor while green.
> - ONE CONVENTIONAL COMMIT PER TASK, in task order, each leaving the suite green. Commit inside the
>   worker as each task closes. A branch that only holds `docs(odd)` is NOT done: implement every task
>   before reporting.
> - Code, tests, the feature document and commit messages in English. README and docs/architecture.md
>   are Spanish; keep that.
> - Never commit data/raw, data/processed, data/index, .env or full captures.
> - About 400 authored changed lines per task is a planning heuristic, not a cap.
> - Out of scope: ingesting into the real corpus (the parent does it), poe2db changes, eval dataset,
>   retrieval tuning.
>
> ## Allowed edit surfaces
> src/poe2_rag/ingestion/adapters/outbound/patch_notes_parser.py
> src/poe2_rag/bootstrap.py
> tests/unit/**
> tests/integration/**
> tests/fixtures/patch_notes/**
> odd/tasks/patch-notes-chunking.md
> docs/architecture.md
>
> ## Delivery
> - Branch feat/patch-notes-chunking from main. Feature doc odd/tasks/patch-notes-chunking.md with
>   task IDs, the route per task, RED/GREEN evidence and the commit hash per task.
> - No Co-Authored-By or any AI attribution.
> - Do not push or open a PR.
>
> ## Verification
> - `uv run pytest -q` passes (offline default suite).
> - `uv run pytest -q -m integration` result reported separately, with the measured token counts.
> - `git log --oneline main..HEAD` shows one commit per task, with feat/test commits, not only docs.
> - Report `<command>: <observed result>` and RED/GREEN counts per task.
>
> ## Return
> Concise report: tasks done, commits, test counts, the default budget with its token evidence,
> deviations from this prompt and why.
> Close with a `## Key Learnings` block of 1–5 numbered standalone facts.

### L2 — The Herdr flow gate blocks commits, and the branch is `patchs`

The Herdr flow rule for this worktree says no commit until `herdr-flow gate poe2-rag@patchs` exits 0,
and the gate currently reports `commit bloqueado … sin review: qa, review`. The same rule says to end
the turn without committing so the watcher reviews the uncommitted diff. The prompt asks for one
commit per task on `feat/patch-notes-chunking`; that conflicts with the harness rule and with the
worktree branch (`patchs`, one commit ahead of `main`), which is the branch the gate tracks. Decision:
implement every task on `patchs`, keep the whole change uncommitted, and hand the commit-per-task
plan to the parent after the gate returns its commit instructions. Recorded here because it changes
the delivery shape, not the implementation.

### L3 — The captures are not in this worktree

`data/raw/` is git-ignored, so the five cached pages exist only in the main worktree at
`/home/niico09/Desarrollo/Personales/PoE2-RAG/data/raw/patch_notes/`. They were read there (the
worktree is registered in this session) and never re-fetched; fixtures are trimmed excerpts.

### L4 — The estimate was recalibrated twice against the real tokenizer

The first constants (`chars/4`, `words*4/3`) looked safe on the six long sections of the captures, but
measuring every default block showed the widest block real count at 418 against an estimate of 388,
and later the shortest block at 42 against 39. Both are undercounts, and an undercount is exactly the
failure this feature removes. The constants are now 3.6 characters per token and 1.9 tokens per word
(the measured ratios of those two blocks), and the integration test pins `estimate >= real` on every
block of the fixtures so a future recalibration cannot silently break it.

### L5 — One integration test fails, and it already failed at the branch base

`test_hotfix_versions_integration.py::test_two_hotfix_captures_supersede_each_other_and_both_indexes_follow`
asserts that capturing the 0.5.5e thread closes the open 0.5.5d chunk; the run reports `closed == 0`.
The same test fails identically on a pristine copy of `217ec8c` (`git archive` into `/tmp/base-pn`), so
it predates this feature and is unrelated to chunking: with entity keys carrying the full version, a
cross-entity close no longer happens. Fixing it means changing the application-layer supersession
rule, which S8 forbids here, so it is reported and not touched.

### L6 — Round 1 review findings, applied

RED first: 5 failed, 55 passed in the parser suite, then 60 passed.

- **`_extract_body` picked a later post.** It scanned `tr.newsPost` before `tr.staff` regardless of
document order and only looked inside the row's first cell. It now makes one document-order pass over
rows that carry either class and reads `div.content` from anywhere in the row, so the earliest post
wins and a later reply in either layout is skipped. New fixtures `mixed_layout_thread.html` (staff OP
followed by a news-post reply) and `news_post_second_cell_thread.html` (body in the second cell).
- **`_flatten_layout` unwrapped content.** It unwrapped anything holding a heading, `ul` and spoiler
  boxes included, so a nested heading could rename or split a section of a normal thread. It now
  unwraps only the forum's layout boxes (`box-content`, `lbox`, `lbox-container`), which are the ones
  that make the news-post headings miss the top level. New fixtures
  `nested_heading_list_thread.html` and `nested_box_heading_thread.html` pin that a nested heading
  stays text.
- **`_drop_banner_heading` promised the wrong thing.** Its docstring said the banner body "belongs to
  the first section"; in practice the nodes before the first real heading form the synthetic
  `overview` section. Docstring corrected, and the function now returns the nodes together with the
  level to split on, so `_build_chunks` no longer computes `_shallowest_heading_level` twice.
- **`_pack_blocks` off-by-one.** It reserved the context line but not the newline joining it to the
  body, so a block could exceed the budget by one token. It now reserves that token, takes the plain
  `prefix` instead of a single-use callable, and builds `part N/M` itself, which also drops the
  `Callable` import.
- **`InvalidTokenBudgetError` was a document error.** It inherited `PatchNotesParseError`, which
  `bootstrap.EXPECTED_ERRORS` reports as "could not parse the document"; a bad budget is a
  composition-root bug. It now inherits `ValueError`, and a test pins that it is not a
  `PatchNotesParseError`.
- **Constants.** `3.6` and `1.9` are literals now, and the heading tag set is declared once
  (`_HEADING_TAGS = frozenset(_HEADING_LEVELS)`); the per-node `list(...)` rebuild is gone.
- **Docs.** `docs/architecture.md` said the worst real block measures 323 tokens; the measurement is
  326 (T4), so the doc now says 326.
- **Junk.** The two 0-byte files at the repo root (`div.box-content`, `div.lbox`, created by an
  unquoted shell redirect in an earlier turn) are deleted, so they cannot ride into the commit.

Runs after the fixes: `uv run pytest -q` 1508 passed, 29 deselected; the patch-notes integration
module 4 passed (fixtures worst block still 264 tokens, 9 chunks through the real embedder with none
truncated); the five real captures re-measured unchanged — 4000864 still splits to 32 chunks with a
326-token worst block, and no block estimates below its real count. Still no commit: the gate decides.

### L7 — Round 2 review findings, applied

RED first: 2 failed, 60 passed in the parser suite, then 62 passed; 1510 passed, 29 deselected.

- **The body cell lost its anchor.** Reading `div.content` from anywhere in a post row made any cell
  win, including a metadata block, with no error to notice. A `tr.staff` row now yields the
  `div.content` that is a direct child of its `td.content-container`, and a staff row without that
  cell is not a post body again (`MissingPatchNotesBodyError`, as before this feature); a `tr.newsPost`
  row yields the first direct `div.content` among its cells, because the real page leaves the body
  cell unclassed. A nested quote or metadata block can no longer win either, since only a direct
  child of a cell is accepted. New fixture `staff_body_anchor_thread.html` plus an inline case for
  the missing cell.
- **`_LAYOUT_WRAPPER_CLASSES` moved** into the header constants block, next to `_NAVIGATION_SLUGS`, so
  the module declares constants in one place.
- **The T4 evidence was stale.** It claimed 4008534 keeps one `notes` chunk; it splits in two
  (`notes-01/02`). Re-measured all five captures after the fixes: 4000864 32 chunks / worst 326,
  4006357 3 / 264, 4008534 2 / 262, 4004106 1 / 210, 4009785 1 / 76, and no block estimates below its
  real count. The entry now also records that the estimate over-splits short-word pages on purpose
  (4008534 measures ~647 estimated against 262 real per block): a block too small, never a block over
  the window.
