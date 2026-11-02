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
- [ ] T5 — Supersession in `IngestSource` (close, remove, out-of-order, one version per identity),
  `IngestReport` counts and the CLI line. Route: inline. S3, S5.
- [ ] T6 — Integration: the Spark fixture captured under patch A, then under patch B, then reindex;
  BM25 and Chroma per version. Route: inline + one integration run. S6.
- [ ] T7 — Docs (§4, §5.2, `patches.toml`), close L3/L4, full suite and verification. Route: inline.
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
