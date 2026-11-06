# Feature: chunk-context-lines

Make every ingested chunk self-describing: both parsers prefix each chunk's text with one context
line naming its entity and section, the way split blocks already do. Work test-first, offline by
default, with the domain and application layers untouched.

## Context

- Baseline 2026-11-06 (20 cases, `data/eval/runs/20261106T193709Z-b9f34f7-e3d45cd6.json`): overall
  Hit@1 0.737, Hit@3 0.842, MRR 0.781; gems Hit@1 0.556; patch notes Hit@1 0.900.
- All four misses share one root cause: only split blocks carry a context line. Every other chunk
  names its entity and section only in `chunk_id`, and the embedder, the reranker and BM25 never read
  `chunk_id`.
- `poe2db` `#properties` reads `AoE, Projectile, Fire, Repeatable Tier: 3 ... Cast Time: 1.20 sec`
  and never says "Fireball"; patch notes `#ritual-changes` starts with its first bullet and never
  says "Ritual Changes".
- Split blocks already do this: `Spark — levels 11–20` (poe2db),
  `Patch 0.5.5 — Endgame Changes (part 2/3)` (patch notes).

## Specs

- **S1 — One context line per chunk.** Verbatim: "Every chunk's text begins with exactly one context
  line, followed by the section text unchanged." poe2db: "<Gem name> — <Section title>", e.g.
  "Fireball — Properties", "Spark — Explicit Modifiers", "Spark — Description". patch notes:
  "Patch <version> — <Section heading>", e.g. "Patch 0.5.5 — Ritual Changes". Heading-less hotfix
  threads: "Patch 0.5.5b — Notes". Verbatim: "Use the section's human-readable title (the page
  heading), not its slug."
- **S2 — Split blocks keep their line, one helper.** Verbatim: "Split blocks keep their current
  context line format (it already carries the part/range). Never emit two context lines. Extract one
  helper per parser so whole sections and blocks build the line the same way."
- **S3 — The line counts toward the budget.** Verbatim: "The context line counts toward each parser's
  token budget. Re-check the integration budget tests with the real tokenizer and report the new
  worst-case token counts. If a block now exceeds the budget, the splitter must account for the line;
  do not raise the budget."
- **S4 — Ids and re-ingest do not change.** Verbatim: "Section ids and chunk_ids do not change. Only
  the text changes. Re-ingesting the same snapshot updates the text in place through the existing
  upsert/supersession [snapshot identity, so] no duplicate chunk appears."
- **S5 — Verbatim body and citations.** Verbatim: "Wording and numbers below the context line stay
  verbatim, because c[itations quote this text]." Verbatim: "Confirm the Claude citation adapter needs
  no change: the context line is part of the document text."
- **S6 — Testing.** Verbatim: "Unit tests for each parser: every chunk starts with its context line;
  exact lines for one gem section, one patch-notes section and one heading-less hotfix; blocks have
  exactly one context line; the body after the line equals today's text. Integration: the token budget
  tests for poe2db level blocks and patch [notes include the context] line included. Report the
  counts." Verbatim: "Do not touch eval/dataset.jsonl or data/. The parent re-ingests and [re-runs the
  baseline]."
- **S7 — Constraints.** Verbatim: "Domain and application layers stay untouched. If one must change,
  stop and report why." Verbatim: "TDD: tests first, observe RED, implement GREEN, refactor while
  green". Verbatim: "Code, tests, the feature document and commit messages in English. README and
  docs/architecture.md stay Spanish." Verbatim: "Never commit data/raw, data/processed, data/index,
  data/eval, eval/dataset.jsonl, .env or full captures." Verbatim: "Out of scope: retrieval or
  reranking tuning, new sources, eval datas[et changes]."

## Tasks

- T1 — poe2db context lines: one helper builds the line for whole sections and level blocks; unit
  tests updated and added. Route: inline (parent), test-first. Commit: pending the flow gate.
- T2 — patch-notes context lines: one helper builds the line for whole sections and budget blocks; the
  fit test counts the line; unit tests updated and added. Route: inline (parent), test-first. Commit:
  pending the flow gate.
- T3 — Budget evidence: re-run both integration budget tests with the real tokenizer, record the
  measured worst-case counts, and note the chunk-text contract in `docs/architecture.md`. Route:
  inline (parent). Commit: pending the flow gate.

## Budget evidence (`bge-base-en-v1.5` tokenizer, `uv run pytest -q -m integration -s`)

- patch notes, worst default chunk over the blocked fixtures: **264 tokens** (margin 248 of 512). It is
  a 0.5.5c `notes` block, unchanged: the line was already there. Per fixture: `content_update_thread`
  2 chunks, max 51; `oversized_section_thread` 4 chunks, max 248; `notes_only_thread` 3 chunks, max
  264. The widest whole-section chunk with its new line is 51 tokens, so no section crossed the
  400-token budget and no `chunk_id` moved.
- patch notes, the unblocked shapes the splitter replaces: 707 and 559 tokens against a 512 window.
- poe2db level blocks: generated 40-row proxy **max 353 tokens** (margin 159); observed worst on the
  real captures **380 tokens** (margin 132). Both unchanged, because the block line already existed
  and still counts. One block of all 40 rows would be 1 283 tokens.
- `estimate_tokens` stays above the real count on every captured shape (that integration test passes).

## Log

- L1 (Herdr flow rule, verbatim): "Regla del flow: NO commitees hasta que `herdr-flow gate
  poe2-rag@imp-chunk` devuelva 0. Cuando termines un cambio, terminá el turno sin commitear: la review
  (qa + review) corre sola sobre tu diff sin commitear y te llegan los hallazgos o el OK con las
  instrucciones de commit, push y PR. Nunca mergees ni hagas force push. No uses comandos destructivos
  (rm -rf, git clean, reset --hard) ni para limpiar temporales: dejalos en /tmp, así no quedás
  esperando una confirmación humana. Respondé solo 'ok' y terminá el turno."
- L2 (task, verbatim excerpt): "Make every ingested chunk self-describing: prefix each chunk's text
  with one context line naming its entity and section, in both parsers. Work test-first." ... "Code:
  src/poe2_rag/ingestion/adapters/outbound/poe2db/gem_page_parser.py,
  src/poe2_rag/ingestion/adapters/outbound/patch_notes_parser.py".
- L3 (measured before the change, offline): over every `tests/fixtures/patch_notes/*.html` capture the
  widest whole-section chunk estimates 61 tokens against a 400-token budget; only split blocks reach
  390. Counting the context line in the fit test therefore cannot move any `chunk_id` on these
  fixtures (S4).
- L4 (measured, `token_lengths` on `bge-base-en-v1.5`): see "Budget evidence" above.
- L5 (T1 RED): `uv run pytest tests/unit/ingestion/adapters/outbound/test_poe2db_gem_page_parser.py
  -q` → 9 failed, 42 passed. The failures are the exact text assertions that still saw the first
  content line where the context line now belongs.
- L6 (T1 GREEN): same command → 51 passed, after `_describe` became the one place a gem chunk gets
  its line and `_level_blocks` started returning a label instead of a pre-formatted title.
- L7 (T2 RED): `uv run pytest tests/unit/ingestion/adapters/outbound/test_patch_notes_parser.py -q` →
  9 failed, 55 passed.
- L8 (T2 GREEN): same command → 64 passed, after `_context_line` and the fit test that counts the
  line: `estimate_tokens(f"{context}\n{text}") <= max_block_tokens`. `_pack_blocks` still reserves the
  part counter, so the budget never moved.
- L9 (S4, measured): over every committed fixture no whole-section chunk's fit decision flips when
  its line is counted — the widest is 61 tokens before the line — so section and chunk ids are
  unchanged; the poe2db ids are the same list as before (`description`, `properties`,
  `explicit-modifiers`, `levels-01-20`). A text-only difference still reaches `IngestSource.changed`
  and the same `chunk_id` is upserted, so a re-ingest updates in place and adds no duplicate.
- L10 (S5): the Claude citation adapter needs no change. It sends one citable document block per
  non-blank line and accepts `cited_text` as a whitespace-stripped substring of `chunk.text`; the
  context line is now the first such block and remains part of the document text.
- L11 (verification): `uv run pytest -q` → 1516 passed, 29 deselected. `uv run pytest -q -m
  integration` → 24 passed, 1 failed, 1520 deselected; the failure is
  `test_hotfix_versions_integration.py::test_two_hotfix_captures_supersede_each_other_and_both_indexes_follow`
  (`second.closed` 0 where the test expects 1). It is pre-existing on this branch: the same test fails
  identically with the two `HEAD` parser modules restored on `PYTHONPATH`, and the rule it asserts
  lives in `ingestion/application/ingest_source.py`, where a hotfix thread's own entity key means the
  0.5.5e snapshot never closes 0.5.5d. Out of scope here; reported for a separate decision.
- L12 (delivery): work left uncommitted on `imp-chunk`, per L1. Commit hashes per task stay pending
  until `herdr-flow gate poe2-rag@imp-chunk` returns 0.
- L13 (review round): four findings applied. (1) Docstring wording corrected so the dense embedder
  and reranker are described as scoring only the chunk text, while the lexical index additionally
  appends the entity name and section; updated in both parser modules and their helper docstrings.
  (2) `gem_page_parser._sections` now builds its final 4-tuples directly through `_describe`, and
  `_level_blocks` returns ready 4-tuples taking the gem `name`. (3) Shared test helper module
  `tests/unit/ingestion/adapters/outbound/chunk_text.py` extracted, both parser test modules use it,
  and `test_no_chunk_carries_a_second_context_line` asserts the exact block context line occurs once.
  (4) Citation-adapter test `test_a_context_line_is_citable_and_a_body_quote_still_verifies` added,
  pinning that the context line is sent as its own citable block and that both body and context-line
  quotes verify. Also corrected `docs/architecture.md` wording on synthetic `Overview` and
  `rows <a>–<b>` titles, and updated the `README.md` chunk-schema example so it mirrors the new
  `text`.
