# Feature: snapshot-supersession

Close the two validity gaps the poe2db source reported (L3, L4): the catalog learns release dates so
a snapshot can be dated exactly, and ingesting a newer snapshot of an entity closes the previous one
so no entity ever holds two open versions.

## Specs

- **S1** — Release-date lookup.
  - `PatchCatalog` gains `current_at(day: date) -> PatchVersion`: the known patch with the latest
    release date on or before `day`.
  - A day before the first release → a specific error.
  - Release dates become part of the catalog model. Every known patch has exactly one date, and
    dates strictly increase with versions, otherwise construction fails.
  - `InMemoryPatchCatalog` and `TomlPatchCatalog` both implement it.
  - `current()` keeps its meaning (the configured pointer). Do not derive it from today's date.
- **S2** — The poe2db parser uses `catalog.current_at(raw.fetched_at)`.
  - Normalize fetched_at to a UTC date.
  - Reject a naive datetime with a specific error rather than guessing a timezone.
- **S3** — Supersession at ingest, in `IngestSource`. The identity of a snapshot is (source, entity
  key). Read how the `chunk_id` carries the source to define it exactly. For each identity in the
  incoming chunks, with new `valid_from` V:
  - stored open-ended chunks of that identity with `valid_from < V` are closed:
    `valid_until = V` (same `chunk_id`, since `valid_until` is not part of the id);
  - stored chunks of that identity with `valid_from == V` that are absent from the new snapshot
    (a removed section) must not survive as current;
  - a stored chunk of that identity with `valid_from > V` means an out-of-order (older) snapshot →
    a specific error, and nothing is written;
  - chunks of other identities are untouched.
  Fail loud, all or nothing: validate everything before the first write.
- **S4** — Port changes follow interface segregation.
  - If removing chunks requires a new repository capability, add the smallest explicit method
    (e.g. `remove(chunk_ids)`) or a separate writer protocol. Do not widen `save`'s contract
    silently.
  - `JsonChunkRepository` implements it with the same atomic-write guarantees it already has.
- **S5** — `IngestReport` also reports how many stored chunks were closed and removed, and the CLI
  prints it.
- **S6** — Testing.
  - Domain:
    - `current_at` boundaries: the exact release day, the day before the next release, after the
      last release, before the first;
    - catalog construction errors: a missing date, non-increasing dates.
  - Application, with fakes:
    - a first snapshot;
    - the same snapshot again (no-op);
    - a newer snapshot closes the old range;
    - a section removed at the same version;
    - an older snapshot is rejected and writes nothing;
    - two entities from one document;
    - patch notes of consecutive versions do not close each other (confirmed separate entities).
  - After a supersession, `Chunk.is_valid_at` holds for exactly one version of the entity at every
    known patch. Write that as a property-style test over the catalog.
  - Adapters: TOML dates round-trip; `JsonChunkRepository` removal is atomic.
  - Integration (marked `integration`): ingest the Spark fixture captured under patch A, then under
    patch B (fake clock), then reindex. BM25 and Chroma searches at A return only the A chunks, and
    at B only the B chunks. Run it once and report.
- **S7** — Rules: domain stays stdlib-only; immutable value objects; use `dataclasses.replace` for
  the closed copies. TDD: tests first, observe RED, implement GREEN, refactor while green. Update
  docs: architecture §4 (catalog dates, supersession rule), the `PatchCatalog` note in
  `config/patches.toml`, and close L3/L4 in `odd/tasks/poe2db-source.md` with a pointer to this
  feature. Code, tests, docs and commit messages in English. About 400 authored changed lines per
  task is a planning heuristic, not a cap. Out of scope: poe2wiki, `RunEvaluation`, crawling, any
  change to retrieval or answering.
- **S8** — Delivery: branch `feat/snapshot-supersession` from `main`; this feature doc with task
  IDs, the route per task and RED/GREEN evidence; one Conventional Commit per task; no
  `Co-Authored-By` or any AI attribution; do not push or open a PR.
- **S9** — Verification: `uv run pytest -q` passes (offline default suite);
  `uv run pytest -q -m integration` is reported separately; the report states
  `<command>: <observed result>` and RED/GREEN counts per task.

## Decisions

- **D1 — Snapshot identity is `(source.type, entity.key)`.** `Chunk.chunk_id` is
  `<source.type>:<entity.key>@<valid_from>#<section>`, so everything before `@` is exactly the
  snapshot identity and it is already carried by every chunk. No new id format is needed.
- **D2 — `PatchRelease` is the catalog's release model.** `InMemoryPatchCatalog` is built from
  `PatchRelease(version, released_on)` values, so a version without a date is not constructible and
  "every known patch has exactly one date" holds by type. `TomlPatchCatalog` requires
  `released_on` and maps each entry to one `PatchRelease`.
- **D3 — The out-of-order rule also covers a stored range that reaches past V.** A stored chunk
  whose `valid_until` is greater than V proves the entity was captured at that later version, so
  ingesting V after it is out of order too. Checking only `valid_from > V` would let `[V0, V2)`
  overlap the new `[V1, V2')` range.
- **D4 — One version per identity per document.** A document that produced two versions of one
  identity would create two open-ended versions of it, which is exactly what S3 forbids, so
  `IngestSource` rejects it with a specific error instead of guessing which version wins.
- **D5 — `remove` is a new explicit method on `ChunkRepository`, not a widened `save`.** The port
  already owns both `save` and `all`; a new read-only protocol would split every caller for no
  behavioural gain, while a second explicit method keeps "`save` never deletes" true.

## Tasks

- [x] T1 — Branch `feat/snapshot-supersession`, feature doc, Engram mirror. Route: inline. S8.
- [x] T2 — Release dates in the catalog: `PatchRelease`, `PatchCatalog.current_at`,
  `InMemoryPatchCatalog`, `TomlPatchCatalog`, domain/contract/adapter tests and every construction
  site. Route: inline. S1, S7.
- [x] T3 — poe2db parser: `valid_from` from `current_at(fetched_at)` normalized to UTC, naive
  `fetched_at` rejected. Route: inline. S2.
- [x] T4 — `ChunkRepository.remove` and an atomic `JsonChunkRepository.remove`, with contract tests
  and the port fakes. Route: inline. S4.
- [x] T5 — Supersession in `IngestSource` (close, remove, out-of-order, one version per identity),
  `IngestReport` counts and the CLI line. Route: inline. S3, S5.
- [x] T6 — Integration: the Spark fixture captured under patch A, then under patch B, then reindex;
  BM25 and Chroma per version. Route: inline + one integration run. S6.
- [x] T7 — Docs (§4, §5.2, `patches.toml`), close L3/L4, full suite and verification. Route: inline.
  S7, S9.

## Evidence

- **T1** — `git switch -c feat/snapshot-supersession main` → branch created at `cd1d018`; feature doc
  written; Engram mirror saved. No RED (documentation only). Baseline before any change:
  `uv run pytest -q` → **1102 passed, 15 deselected**.

- **T2** — RED (domain) → `ImportError: cannot import name 'PatchNotReleasedError'`, 2 collection
  errors, 0 tests run (`test_patch_release.py` and the rewritten `test_patch_catalog.py`). GREEN
  (domain) → 47 passed after `PatchRelease`, `PatchNotReleasedError` and `current_at` landed. RED
  (adapter) → 8 failed, 21 passed on the rewritten `test_toml_patch_catalog.py` (`current_at` missing,
  `released_on` still optional). GREEN → 236 passed across `tests/unit/shared`, 54 passed on the
  catalog contract for both implementations. Full suite **1148 passed, 15 deselected** (was 1102
  before the task). `uvx pyright==1.1.414 src tests` → **0 errors, 0 warnings, 0 informations**.
  - `InMemoryPatchCatalog` now takes `Iterable[PatchRelease]`; a bare `PatchVersion` is a patch with
    no date and is rejected, and release dates must strictly increase with the versions (equal or
    backwards dates fail). `current_at` rejects a `datetime` because it is a `date` subclass whose
    comparison against dates raises `TypeError`.
  - `TomlPatchCatalog` requires `released_on` per entry (a missing one is a `PatchCatalogLoadError`
    naming the entry) and delegates `current_at`, so a date round-trips from the file to the answer.
  - Every in-memory construction site moved to releases: the answering fakes and the `SpyCatalog`
    (which also gained `current_at`, caught by pyright), the ingestion fakes and the patch-notes
    pipeline. `config/patches.toml` documents that `released_on` is now part of the model.
  Commit: `feat(shared): date every known patch and look one up by day`.

- **T3** — RED → `ImportError: cannot import name 'NaiveFetchedAtError'`, 1 collection error, 0
  tests run (6 cases added, 3 of them pinning the UTC normalization across a release boundary).
  GREEN → 33 passed on the parser suite; full suite **1154 passed, 15 deselected**;
  `uvx pyright==1.1.414 src tests` → **0 errors**. The adapter now computes `captured_on` from
  `fetched_at.astimezone(UTC).date()`, rejects a naive instant with `NaiveFetchedAtError` before
  reading the page, uses `current_at(captured_on)` for `valid_from` and the same UTC day for
  `SourceRef.captured_at`, and lets `PatchNotReleasedError` propagate for a capture older than the
  first known release.
  Commit: `feat(ingestion): date a poe2db page by its capture day`.

- **T4** — RED → 10 failed, 44 passed: the `json` parameter of the contract suite and five adapter
  tests (`JsonChunkRepository` had no `remove`). GREEN → 83 passed across the contract, the adapter
  and the port-shape suites; full suite **1170 passed, 15 deselected**; `uvx pyright==1.1.414 src
  tests` → **0 errors**. `ChunkRepository.remove(chunk_ids)` is a separate explicit method (D5): an
  empty sequence is a no-op, an unknown id is ignored and a call that would drop nothing does not
  rewrite the file, and the surviving corpus is replaced in the same temp-file-plus-`os.replace` step
  `save` uses. A failed replace leaves the previous corpus and no temporary file; a read failure
  stops the removal.
  Commit: `feat(ingestion): let the chunk repository drop stored chunks`.

- **T5** — RED → `ImportError: cannot import name 'MixedSnapshotVersionError'`, 1 collection error,
  0 tests run (13 cases authored: 12 supersession scenarios plus the property-style invariant).
  GREEN → the application suite passes 31 cases. The CLI line was then reverted alone to observe its
  own RED: 3 failed, 7 passed with the old `... skipped.` line, 10 passed with the counts added.
  Full suite **1183 passed, 15 deselected**; `uvx pyright==1.1.414 src tests` → **0 errors**.
  - `IngestSource` groups the document by snapshot identity `(source.type, entity.key)` and rejects
    a document holding two versions of one identity (`MixedSnapshotVersionError`). It closes every
    stored open range that starts earlier at the incoming version with `dataclasses.replace`, removes
    the stored ids at the same version the document no longer contains, and rejects a stored chunk
    that reaches past the incoming version (`OutOfOrderSnapshotError`) before the first write.
  - A range closed by an earlier run is history and is neither re-closed nor counted, so a second run
    over the same snapshot is a no-op. Patch notes never supersede each other because the version is
    part of their entity slug, which a test pins.
  - `IngestReport.closed` and `.removed` default to zero, and the CLI prints
    `N chunks produced, S skipped, C closed, R removed`.
  Commit: `feat(ingestion): close the previous snapshot of an entity at ingest`.

- **T6** — First run failed on one over-strict assertion of mine: `ReindexReport.truncated_chunk_ids`
  is empty because the committed Spark fixture keeps only three level-table rows (6.8 KB trimmed
  excerpt), so it fits the model window. The supersession and index assertions before it already
  passed. After removing that unsupported assertion:
  `uv run pytest -q -m integration tests/integration/test_snapshot_supersession_integration.py` →
  **1 passed in 3.84 s** (real `bge-base-en-v1.5`; the scripted fetcher is the only fake).
  - Patch A = 0.1.0 captured 2024-12-10, patch B = 0.1.1 captured 2025-01-20, both from the same
    fixture; the catalog carries the two release days and treats 0.1.1 as current.
  - Ingest A: 4 open chunks, `closed=0`, `removed=0`. Ingest B: `closed=4`, `removed=0`; the corpus
    holds 8 chunks and every A chunk has `valid_until=0.1.1` and is not valid at 0.1.1.
  - `ReindexCorpus` rebuilt both indexes from all 8 chunks. At 0.1.0 the dense search returned exactly
    the A ids and BM25 only A ids (non-empty); at 0.1.1 the dense search returned exactly the B ids
    and BM25 only B ids.
  Commit: `test(ingestion): prove a second capture supersedes the first end to end`.

- **T7** — Docs and close. `docs/architecture.md` §4.2 gained `PatchRelease` and a `PatchCatalog` row
  with `current_at`; §5.2 step 6 is now the supersession rule (identity, close, remove, out-of-order,
  counts) instead of the "sections are not pruned" debt; the poe2db paragraphs keep the fetcher and
  parser description and replace the two debt paragraphs with the capture-date rule; §10.1 states that
  patch notes carry the version in the entity slug, so supersession never closes one with the next.
  `odd/tasks/poe2db-source.md` closes L3 and L4 with pointers to this feature and marks D3 superseded.
  - One wiring fix the new error made necessary: `PatchNotReleasedError` joins `EXPECTED_ERRORS`, so a
    cached capture older than the first known release prints one line (exit 1) instead of a traceback,
    like every other operator-facing failure (ADR-0006). A bootstrap test pins it.
  - **Commands**
    - `uv run pytest -q` → **1183 passed, 16 deselected** (offline default suite; the new integration
      test is collected and deselected by its marker).
    - `uv run pytest -q -m integration` → **13 passed, 1186 deselected in 17.58 s** (opt-in; includes
      the new supersession test).
    - `uv run pytest -q -m integration tests/integration/test_snapshot_supersession_integration.py` →
      **1 passed in 3.84 s**.
    - `uvx pyright==1.1.414 src tests` → **0 errors, 0 warnings, 0 informations**.
    - `uvx ruff check --select I src tests` → `All checks passed!`.
    - `git log main..HEAD --format=%B | grep -ci 'co-authored-by\|generated with\|claude'` → `0`.
  Commit: `docs: close the poe2db validity gaps in the architecture`.

## Delivery slices

One Conventional Commit per task (S8), which is the strategy this feature follows instead of cutting a
commit at roughly 400 authored lines: the prompt fixes that number as planning guidance, not a cap, and a
task is the smallest slice that keeps a rule, the tests that pin it and the feature document together.
The commits and their changed-line totals are: T1 230 (feature doc), T2 562 (release dates, the domain
value object, both catalog implementations, the contract suite and every construction site), T3 109 (the
parser's capture date), T4 184 (the repository `remove` capability), T5 423 (supersession, the report and
the CLI line), T6 186 (the integration test) and T7 (documentation and the error classification). T2 and
T5 pass the heuristic because their two halves are one contract: splitting the domain invariants from the
TOML mapping, or the supersession rules from the report, would separate a rule from its proof. A reviewer
who wants smaller slices can read each of those two commits in its two halves.

## Deviations

- **Tests outside the listed edit surfaces.** The catalog constructor and the `ChunkRepository` and
  `PatchCatalog` protocols changed, so `tests/contract/**`, the answering fakes (`SpyCatalog` gained
  `current_at`), `tests/unit/answering/**` and `tests/unit/ingestion/test_ingest_patch_notes_pipeline.py`
  had to move with them. No production code outside the listed surfaces changed.
- **`PatchNotReleasedError` in `EXPECTED_ERRORS`.** T3 makes a normal ingest path raise it (a cached
  capture older than the first known release), so the composition root classifies it as an expected
  failure: one line and exit 1 instead of a traceback (ADR-0006). The prompt does not ask for it.
- **`config/patches.toml` in T2.** Its `PatchCatalog` note changed meaning with the adapter, so it
  travels with that commit instead of the documentation one.
- **pyright and ruff reported.** The prompt verifies pytest; both extra gates are the repository's
  precedent and pyright caught a real issue (`SpyCatalog` no longer satisfied `PatchCatalog`).

## Log

### L1 — Original request (verbatim)

> Close the two validity gaps reported in odd/tasks/poe2db-source.md (L3, L4), test-first:
> (1) PatchCatalog can answer "which patch was current on a given date", and
> (2) ingesting a newer snapshot of an entity closes the validity of the previous one, so no entity
> ever has two overlapping versions (driver D1).
>
> ## Context (read first)
> - odd/tasks/poe2db-source.md: D3, L3 and L4 (the observed gaps and their root causes).
> - docs/architecture.md §4 (Validity is half-open [valid_from, valid_until); chunks store ranges,
>   never an "is current" flag) and docs/adr/0003 (Chroma encodes valid_until; open = sentinel).
> - Code involved:
>   - shared/domain/patch_catalog.py: the PatchCatalog protocol (current, known, contains) and
>     InMemoryPatchCatalog.
>   - shared/adapters/outbound/toml_patch_catalog.py: validates `released_on`, then discards it.
>   - config/patches.toml.
>   - ingestion/application/ingest_source.py, ports.py (ChunkRepository.save upserts by chunk_id;
>     all) and errors.py.
>   - ingestion/adapters/outbound/json_chunk_repository.py.
>   - ingestion/adapters/outbound/poe2db/gem_page_parser.py: currently uses `catalog.current()`.
>   - patch_notes_parser.py: one entity per patch version. Confirm this, because it decides whether
>     supersession ever applies to patch notes.
>   - ingestion/application/reindex_corpus.py: confirm it fully rebuilds both indexes from
>     `repository.all()`, so updated valid_until values reach BM25 and Chroma.
>
> ## Required behavior
> 1. Release-date lookup.
>    - PatchCatalog gains `current_at(day: date) -> PatchVersion`: the known patch with the latest
>      release date on or before `day`.
>    - A day before the first release → a specific error.
>    - Release dates become part of the catalog model. Every known patch has exactly one date, and
>      dates strictly increase with versions, otherwise construction fails.
>    - InMemoryPatchCatalog and TomlPatchCatalog both implement it.
>    - `current()` keeps its meaning (the configured pointer). Do not derive it from today's date.
> 2. The poe2db parser uses `catalog.current_at(raw.fetched_at)`.
>    - Normalize fetched_at to a UTC date.
>    - Reject a naive datetime with a specific error rather than guessing a timezone.
> 3. Supersession at ingest, in IngestSource. The identity of a snapshot is (source, entity key).
>    Read how the chunk_id carries the source to define it exactly. For each identity in the
>    incoming chunks, with new valid_from V:
>    - stored open-ended chunks of that identity with valid_from < V are closed:
>      valid_until = V (same chunk_id, since valid_until is not part of the id);
>    - stored chunks of that identity with valid_from == V that are absent from the new snapshot
>      (a removed section) must not survive as current;
>    - a stored chunk of that identity with valid_from > V means an out-of-order (older) snapshot →
>      a specific error, and nothing is written;
>    - chunks of other identities are untouched.
>    Fail loud, all or nothing: validate everything before the first write.
> 4. Port changes follow interface segregation.
>    - If removing chunks requires a new repository capability, add the smallest explicit method
>      (e.g. `remove(chunk_ids)`) or a separate writer protocol. Do not widen `save`'s contract
>      silently.
>    - JsonChunkRepository implements it with the same atomic-write guarantees it already has.
> 5. IngestReport also reports how many stored chunks were closed and removed, and the CLI prints it.
>
> ## Testing
> - Domain:
>   - current_at boundaries: the exact release day, the day before the next release, after the last
>     release, before the first;
>   - catalog construction errors: a missing date, non-increasing dates.
> - Application, with fakes:
>   - a first snapshot;
>   - the same snapshot again (no-op);
>   - a newer snapshot closes the old range;
>   - a section removed at the same version;
>   - an older snapshot is rejected and writes nothing;
>   - two entities from one document;
>   - patch notes of consecutive versions do not close each other (if confirmed separate entities).
> - After a supersession, `Chunk.is_valid_at` holds for exactly one version of the entity at every
>   known patch. Write that as a property-style test over the catalog.
> - Adapters: TOML dates round-trip; JsonChunkRepository removal is atomic.
> - Integration (marked `integration`): ingest the Spark fixture captured under patch A, then under
>   patch B (fake clock), then reindex. BM25 and Chroma searches at A return only the A chunks, and at
>   B only the B chunks. Run it once and report.
>
> ## Rules
> - Domain stays stdlib-only. Immutable value objects; use dataclasses.replace for the closed copies.
> - TDD: tests first, observe RED, implement GREEN, refactor while green.
> - Update docs: architecture.md §4 (catalog dates, supersession rule), the PatchCatalog note in
>   config/patches.toml, and close L3/L4 in odd/tasks/poe2db-source.md with a pointer to this feature.
> - Code, tests, docs and commit messages in English.
> - About 400 authored changed lines per task is a planning heuristic, not a cap.
> - Out of scope: poe2wiki, RunEvaluation, crawling, any change to retrieval or answering.
>
> ## Allowed edit surfaces
> src/poe2_rag/shared/domain/**
> src/poe2_rag/shared/adapters/outbound/toml_patch_catalog.py
> src/poe2_rag/ingestion/application/**
> src/poe2_rag/ingestion/adapters/outbound/json_chunk_repository.py
> src/poe2_rag/ingestion/adapters/outbound/poe2db/**
> src/poe2_rag/ingestion/adapters/inbound/cli/**
> src/poe2_rag/bootstrap.py
> config/patches.toml
> tests/unit/**
> tests/integration/**
> tests/fixtures/**
> odd/tasks/snapshot-supersession.md
> odd/tasks/poe2db-source.md
> docs/architecture.md
>
> ## Delivery
> - Branch feat/snapshot-supersession from main. Feature doc odd/tasks/snapshot-supersession.md with
>   task IDs, the route per task and RED/GREEN evidence.
> - One Conventional Commit per task. No Co-Authored-By or any AI attribution.
> - Do not push or open a PR.
>
> ## Verification
> - `uv run pytest -q` passes (offline default suite).
> - `uv run pytest -q -m integration` result reported separately.
> - Report `<command>: <observed result>` and RED/GREEN counts per task.
>
> ## Return
> Concise report: tasks done, commits, test counts, the exact snapshot identity used, the port
> change chosen and why, deviations from this prompt and why.
> Close with a `## Key Learnings` block of 1–5 numbered standalone facts.
