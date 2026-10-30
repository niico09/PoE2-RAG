# Feature: ingest-patch-notes

## Objective
Turn one official patch notes thread into validated, persisted domain chunks end to end, test-first, with
no network access in tests: the `IngestSource` use case over three outbound ports, a JSON
`ChunkRepository`, a local snapshot `SourceFetcher` and the official forum parser.

## Problem / Why
`docs/architecture.md` §3 defines the write path (source → parse → chunk → index) but nothing implements
it. `Chunk`, `Validity` and `PatchCatalog` already exist; what is missing is the orchestration that turns
raw HTML into chunks whose validity the catalog accepts, and the adapters that read a snapshot and persist
the corpus. §10 names two risks this slice must address: parsers break when sources change (mitigate with
committed minimal parser fixtures and gitignored full snapshots) and patch notes describe changes, not full
states.

## Specs
- **S1** — `ingestion/application/ports.py` holds `typing.Protocol` ports and the `RawDocument` value:
  - `SourceFetcher.fetch(locator: str) -> RawDocument`
  - `RawDocument(locator: str, content: str, fetched_at: datetime, source_type: str)`, frozen.
  - `DocumentParser.parse(raw: RawDocument) -> Sequence[Chunk]`
  - `ChunkRepository.save(chunks: Sequence[Chunk]) -> None` (upsert by `chunk_id`) and
    `ChunkRepository.all() -> Sequence[Chunk]`.
- **S2** — `ingestion/application/ingest_source.py`: `IngestSource(fetcher, parser, repository, catalog)`;
  `execute(locator) -> IngestReport(locator, chunk_ids, skipped)`: fetch → parse → reject chunks whose
  `valid_from` is unknown to the `PatchCatalog` (specific error, fail loud) → reject duplicate `chunk_id`
  values within one document → save. `chunk_ids` are the document's ids in document order; `skipped`
  counts chunks whose stored value is identical and were therefore not rewritten.
- **S3** — Application errors subclass a new `IngestionError`.
- **S4** — `ingestion/adapters/outbound/json_chunk_repository.py`: one JSON file under a directory given by
  the constructor (production default `data/processed/`). Lossless round-trip of every `Chunk` field,
  atomic write (temp file + replace), schema version field. Its contract is shared with an in-memory fake
  in `tests/contract/test_chunk_repository_contract.py`.
- **S5** — `ingestion/adapters/outbound/local_file_fetcher.py`: reads a saved HTML snapshot from disk. No
  HTTP fetcher in this task (polite HTTP fetching is the next task).
- **S6** — `ingestion/adapters/outbound/patch_notes_parser.py`: official forum thread HTML → chunks.
  - Detect the patch version from the thread title; unknown/unparseable → specific error.
  - `SourceRef(type="patch_notes", url=<thread url>, captured_at=<fetched_at date>)`.
  - Strip markup to clean text; keep bullet structure as lines.
  - The domain and application layers stay stdlib-only; `beautifulsoup4` is adapter-only
    (`uv add beautifulsoup4`).
- **S7** — Tests never touch the network. Small hand-written HTML fixtures under `tests/fixtures/` mirror
  the real thread structure (title, post body, headings, bullet lists). At most one real thread may be
  downloaded manually into the gitignored `data/raw/`, with an identifiable User-Agent after checking
  `https://www.pathofexile.com/robots.txt`; it is never committed and fixtures only quote short lines.
- **S8** — Modeling decision (MVP): a patch note is its own entity, not a transition applied to gem chunks:
  `EntityRef(kind="patch_notes", slug="<major>-<minor>-<patch>", name="Patch <version>")`, one `Chunk` per
  top-level section (section = heading slug), `Validity(valid_from=<that patch>, valid_until=None)`.
  Applying notes as transitions that close `valid_until` needs poe2db data first; it is out of scope.
  Recorded in `docs/architecture.md` §10.
- **S9** — TDD: tests first, observe RED, implement GREEN, refactor while green. Required tests: version
  detection (valid, missing, malformed); one chunk per section with correct `chunk_id`; validity starts at
  the detected patch and is open; empty/garbage HTML raises; unknown patch rejected by `IngestSource`;
  duplicate `chunk_id`s rejected; repository round-trip and upsert; atomic write leaves no partial file on
  failure.
- **S10** — Code, tests, docs and commit messages in English. `docs/architecture.md` stays Spanish because
  the whole document is (same precedent as `odd/tasks/answer-question.md` L2.7).
- **S11** — Out of scope: HTTP fetching, poe2db and poe2wiki adapters, `ReindexCorpus`, indexes, CLI
  `ingest`.
- **S12** — Branch `feat/ingest` from `main`. Feature doc `odd/tasks/ingest-patch-notes.md`. One
  Conventional Commit per task, no `Co-Authored-By` or any AI attribution. Do not push or open a PR.
- **S13** — `uv run pytest -q` passes; the report states `<command>: <observed result>` and RED/GREEN counts
  per task. About 400 authored changed lines per task is a planning heuristic, not a cap.

## Non-goals
- HTTP fetching, caching, rate limiting and robots handling (the next task).
- poe2db and poe2wiki adapters.
- `ReindexCorpus`, BM25/Chroma indexes and the CLI `ingest` command.
- Applying patch notes as transitions over gem chunks (needs poe2db data).
- Parsing patch versions with a letter suffix (`0.5.5e`): `PatchVersion` is `major.minor.patch`, so such a
  title is reported as unparseable instead of being silently mapped to a different patch.

## Acceptance criteria
- One command (`IngestSource.execute`) takes a locator to persisted, catalog-approved chunks, and every
  rejection fails loud with a specific error instead of dropping data.
- Re-running the same document writes nothing new and reports every chunk as skipped.
- The JSON repository round-trips every `Chunk` field and never leaves a partial file.
- The parser produces one chunk per top-level section, with `chunk_id`
  `<source.type>:patch_notes:<major>-<minor>-<patch>@<version>#<section>` and an open validity range.
- `uv run pytest -q` green; no test reads the network.

## Checks
- `uv run pytest -q`
- Import boundary, by inspection plus `grep`: `ingestion/application` and `shared/domain` import only the
  standard library and `poe2_rag.shared.domain`; `beautifulsoup4` appears only in the parser adapter.
- Type check, best effort and **not** a gate: `uvx pyright==1.1.414` (Pyright is not a project dependency;
  see `odd/tasks/patch-catalog.md` T13).
- Import order, as used by the repository: `uvx ruff check --select I src/poe2_rag/ingestion tests/unit/ingestion`.

## Tasks
- [x] T1 — Branch `feat/ingest`, feature doc, Engram mirror. Route: inline. S12.
- [x] T2 — Ports, `RawDocument` and the `IngestionError` base with tests. Route: inline. S1, S3.
- [x] T3 — `IngestSource`, `IngestReport`, unknown-patch and duplicate guards, fakes, tests. Route: inline. S2, S3, S9.
- [x] T4 — `JsonChunkRepository`, shared contract suite with an in-memory fake, adapter unit tests (schema version, atomic write, load errors). Route: inline. S4, S9.
- [x] T5 — `LocalFileFetcher` with tests. Route: inline. S5.
- [x] T6 — `PatchNotesParser`, `beautifulsoup4`, hand-written fixtures, tests. Route: delegated to `gentle-ai-worker`, reviewed inline. S6, S7.
- [x] T7 — Record the modeling decision and parser-fixture rule in `docs/architecture.md` §10, and the implemented state in the header. Route: inline. S8.
- [x] T8 — Close: full suite, import boundary, type check, evidence, per-spec verdict. Route: inline. S9, S13.

## Progress / Evidence
- T1: branch `feat/ingest` reset to `main` (`c907558`; the old local branch sat at `b8e19af` with no work
  of its own). Baseline on that base: `uv run pytest -q` → 419 passed. No RED applicable (documentation
  only).
- T2: RED → collection `ModuleNotFoundError: No module named 'poe2_rag.ingestion'`, 0 tests run (22
  written). GREEN → 22 passed in the focused file; full suite **441 passed**. The first full-suite run
  exposed an import collision: `tests/unit/answering/application/test_ports.py` and
  `tests/unit/ingestion/application/test_ports.py` share a basename, and pytest's default prepend import
  mode names modules by basename, so the ingestion file was renamed `test_ingestion_ports.py` (the same
  rule the answering feature applied to `answering_fakes.py`).
  - `RawDocument` validates shape only: blank locator or source type, non-string content and a fetch time
    that is not a `datetime` (a `date` is rejected because it cannot carry the capture time) raise
    `InvalidRawDocumentError`. Content may be empty: the parser owns the "empty document" rule, so a
    fetcher never has to invent one.
  - The three protocols are `runtime_checkable`, so the contract suite can assert conformance, and a test
    pins that a partial implementation is not accepted.
  - `fetched_at` is documented as the capture moment, not the read moment, which is what makes it a
    citation date instead of a pipeline timestamp.
- T3: RED (implementation moved aside after authoring the tests) → collection `ImportError: cannot import
  name 'DuplicateChunkIdError' from 'poe2_rag.ingestion.application'`, 0 tests run (16 written). GREEN → 458
  passed (16 new + 1 entity-kind case).
  - The use case validates before it reads the repository: an unknown patch or a repeated id stops the
    run without a repository access, and an empty document returns before touching it. The happy-path
    journal is exactly `fetch, parse, all, save`.
  - `IngestReport.chunk_ids` lists every id the document produced in document order, so a no-op re-run
    still reports the document; `skipped` counts the identical entries and `save` receives only new or
    changed chunks (one test gives it one of each).
  - `patch_notes` had to join the closed `EntityRef` kind vocabulary in
    `src/poe2_rag/shared/domain/entity_ref.py`, outside the prompt's edit surfaces; the shared test that
    enumerates that vocabulary gained the matching value. Without it the modelling decision of S8 cannot
    be represented at all.
- T4: RED → 2 collection errors, `ModuleNotFoundError: No module named
  'poe2_rag.ingestion.adapters.outbound.json_chunk_repository'`, 0 tests run (38 cases written: 24
  contract, 14 adapter). GREEN → 38 passed; full suite **496 passed**.
  - The in-memory implementation inside the contract file is the executable specification: the same 12
    scenarios run against both it and `JsonChunkRepository` (24 cases). The round-trip chunk carries text
    with a newline, an em dash and quotes, tags, two modifiers and a closed validity range, so equality
    of the frozen dataclass proves every field survived.
  - `save` reads the corpus before merging: a corrupt file stops the write instead of dropping the chunks
    it could not decode. An empty `save` is a true no-op (no read, no rewrite).
  - Atomicity is proven by patching `os.replace`: the previous file keeps its exact bytes, the temporary
    file is removed, and a failed first write leaves the directory empty.
  - Insertion order and the position of an updated `chunk_id` are part of the contract, so a JSON diff
    stays stable across upserts.
- T5: RED → 1 collection error, `ModuleNotFoundError: No module named
  'poe2_rag.ingestion.adapters.outbound.local_file_fetcher'`, 0 tests run (12 written). GREEN → 12
  passed; full suite **508 passed**.
  - `fetched_at` comes from the snapshot's mtime in UTC, pinned by `os.utime`, so the citation date is
    the capture date instead of the read date.
  - The locator is echoed verbatim: the parser needs the original path for its report and recovers the
    thread URL from the document when the locator is not a URL.
  - Missing files, non-UTF-8 bytes and directories raise `LocalSnapshotError(RuntimeError)`, the same
    environment-failure boundary as the catalog and repository load errors. An empty file is fetched,
    not rejected: the parser owns the "empty document" rule.
- T6: route changed from inline to delegated (`gentle-ai-worker`, a bounded writer) because the parser,
  its fixtures and its tests were the largest unit left; the parent reviewed the result and hardened it.
  Worker RED → 18 failed, 2 passed against its stub (`NotImplementedError`); worker GREEN → 20 passed;
  full suite **528 passed**. Parent review then added two behaviours test-first: a title-only staff post
  must raise instead of returning no chunks, and nested bullets must stay separate lines even when the
  HTML has no whitespace between the item text and the nested `<ul>` (the fixture is deliberately
  minified there). Review RED → 2 failed, 19 passed; review GREEN → 21 passed; full suite **529 passed**.
  The main fixture now mirrors the downloaded snapshot one step closer: the repeated post title and the
  sections are `<h3>` (the observed post used `<h3>`), the nested heading is `<h4>`, and the community
  row is a plain `<tr>` like the real page.
  - URL recovery: an absolute locator wins; otherwise the first `/forum/post-reply/<id>` link is
    preferred over `/forum/view-thread/<id>`, because the real page lists other threads in its
    navigation and only the reply links reliably identify the current one.
  - Multiple staff posts are concatenated in document order; nested lists render as flat bullet lines
    (no indentation) and blank sections are dropped. Duplicate headings are emitted, not merged:
    `IngestSource` owns the duplicate `chunk_id` rejection (S2).
  - `beautifulsoup4==4.15.0` (with `html.parser`, no lxml) is the only third-party import, and `grep`
    shows `bs4` only in the parser adapter.
  - Error classes stay outside `DomainError` and `IngestionError`: a page that does not match the forum
    shape is an environment/content failure, so `PatchNotesParseError(RuntimeError)` is the boundary,
    matching `PatchCatalogLoadError` and `LocalSnapshotError`.
- T7: documentation only, no RED applicable. `docs/architecture.md` now has: the updated state header
  (ingestion slice implemented, HTTP/other parsers/query adapters/CLI/evaluation still pending); a new
  §5.2 with the five `IngestSource` steps and their fail-loud rules; the `patch_notes` kind in the §4.2
  `EntityRef` row; the ingestion port location in §6; and, in §10, the parser-fixture rule (minimal
  fixtures committed, full snapshots in gitignored `data/raw/`), the new §10.1 modeling decision (patch
  notes as their own entity with open validity; transitions deferred until poe2db exists) and three new
  risk rows (suffixed versions, repeated headings, snapshot URL recovery). The document stays Spanish
  (S10) because the whole file is; everything new elsewhere is English.
- T8: close. Final `uv run pytest -q` → **530 passed** (baseline on `main`: 419; **+111**: 110 cases in the
  new ingestion suites plus the `EntityRef` kind case). Per suite: ports 22, `IngestSource` 16, repository
  contract 24 (12 scenarios × 2 implementations), JSON adapter 14, local fetcher 12, parser 22.
  - The per-spec verdict found the "garbage HTML raises" half of S9 pinned only for an empty file. A
    `garbage.html` fixture and a characterization test were added; the parser already raised
    `PatchVersionNotFoundError` (a `PatchNotesParseError`), so no RED applied and the suite went 529 → 530.
  - `uvx pyright==1.1.414` (whole workspace, best effort, not a gate) → `0 errors, 0 warnings, 0 informations`.
  - `uvx ruff check --select I src/poe2_rag/ingestion tests/unit/ingestion tests/contract/test_chunk_repository_contract.py`
    → `All checks passed`.
  - Import boundary: `grep` over `ingestion/application` and `shared/domain` shows only `__future__`, the
    standard library and `poe2_rag.shared.domain`; `bs4` appears only in `patch_notes_parser.py`; no test
    imports `requests`, `httpx`, `urlopen` or `urllib.request`.
  - `git log main..HEAD --format=%B | grep -ci 'co-authored-by\|generated with\|claude'` → `0`.
  - The reference snapshot `data/raw/patch-notes-0.5.5e.html` exists and `git check-ignore` names
    `.gitignore:7`; `git ls-files data` is empty. The worktree is clean.

## Delivery slices
- One commit per task (S12). The adapters are one vertical slice of the same feature: they share the ports,
  the fixtures and the domain, so they stay inline instead of being split across parallel writers.
- Slice 1 (`793dd61`..`cb69d6f`): T1-T2, 493 authored lines (the plan document is 236 of them). It crosses
  the ~400-line review slice because the user's rule is one commit per task and the plan is a required
  deliverable; each commit is green on its own, so native review can take either one.
- Slice 2: T3 (use case, errors, fakes and tests).
- Slice 3: T4 (JSON repository, contract suite and adapter tests).
- Slice 4: T5 (local snapshot fetcher).
- Slice 5: T6 (patch notes parser, fixtures and the beautifulsoup4 dependency); the delegated writer did not
  commit, so the slice is one parent commit.
- Slice 6: T7 (architecture decisions, one documentation commit).
- Slice 7: T8 (close: one documentation commit with the final evidence and per-spec verdict).

## Tooling notes
- The pi-lens pass replays diagnostics from the RED state (unknown import symbols that exist by the time
  the suite is green). The authoritative checks in this worktree are `uv run pytest -q`, bare
  `uvx pyright==1.1.414 <paths>` and `uvx ruff check --select I`; all three are run at close. The lens
  also cannot resolve `import pytest` (its analysis loads no project configuration, documented in
  `odd/tasks/answer-question.md`) and emits `no-sql-in-code-execute` advisories on `.execute` method
  calls, a documented false-positive class: this repository has no SQL, cursor or database call.

## Close
- Final `uv run pytest -q` → **530 passed** (baseline before the feature: 419; **+111 tests**).
- `<command>: <observed result>`:
  - `uv run pytest -q`: `530 passed in 0.23s`.
  - `uvx pyright==1.1.414`: `0 errors, 0 warnings, 0 informations` (best effort, not a gate).
  - `uvx ruff check --select I src/poe2_rag/ingestion tests/unit/ingestion tests/contract/test_chunk_repository_contract.py`: `All checks passed`.
  - `git log main..HEAD --format=%B | grep -ci 'co-authored-by\|generated with\|claude'`: `0`.
  - `git check-ignore -v data/raw/patch-notes-0.5.5e.html`: `.gitignore:7:data/raw/`.
  - `grep -rn "requests\|httpx\|urlopen\|urllib.request" tests`: no matches.
- Per-spec verdict:
  - S1 ✅ `SourceFetcher`, `DocumentParser` and `ChunkRepository` are runtime-checkable protocols, with the frozen `RawDocument` (22 tests).
  - S2 ✅ `IngestSource.execute` runs fetch → parse → unknown patch → duplicate id → read → upsert and returns `IngestReport(locator, chunk_ids, skipped)` (16 tests).
  - S3 ✅ `IngestionError(ValueError)` with `InvalidRawDocumentError`, `UnknownPatchError` and `DuplicateChunkIdError`.
  - S4 ✅ `JsonChunkRepository`: `schema_version: 1`, one `chunks.json`, atomic temp-file replace, production default `data/processed/`; the same contract runs against it and the in-memory fake (24 cases + 14 adapter tests).
  - S5 ✅ `LocalFileFetcher` reads a UTF-8 snapshot and uses its mtime as `fetched_at` (12 tests).
  - S6 ✅ `PatchNotesParser`: version from the thread title, one chunk per top-level section, `SourceRef("patch_notes", <thread url>, <fetched_at date>)`, bullets as lines and inline markup stripped; `beautifulsoup4` only in the adapter (22 tests).
  - S7 ✅ eleven hand-written fixtures in `tests/fixtures/patch_notes/`, no network in any test, one reference thread downloaded manually into the gitignored `data/raw/` (checked).
  - S8 ✅ `patch_notes` entity kind, open validity and section slugs; the modeling decision is recorded in `docs/architecture.md` §10.1 and L2.
  - S9 ✅ RED and GREEN observed and recorded per task; every required case has a named test (the per-task evidence lists them).
  - S10 ✅ code, tests, this document and every commit message are English; `docs/architecture.md` stays Spanish by the documented precedent (answer-question L2.7).
  - S11 ✅ no HTTP fetcher, poe2db/poe2wiki adapters, `ReindexCorpus`, indexes or `ingest` CLI.
  - S12 ✅ `feat/ingest` reset to `main` at `c907558`; one Conventional Commit per task plus this closing commit; no attribution; nothing pushed or opened.
  - S13 ✅ the commands and counts above.
- Branch totals before this closing commit: `git diff --shortstat main..HEAD -- . ':(exclude)uv.lock'` → 32 files changed, **2509 insertions(+), 11 deletions(-)**, including this feature document. Per-commit authored lines: T1 236, T2 260, T3 456, T4 638, T5 164, T6 757 (35 of them `uv.lock`), T7 46. T3, T4 and T6 are over the ~400-line review slice; each commit is green on its own, so native review can take them one at a time.
- `Risk: none`. The feature adds modules, ports and adapters and only touches shared code by adding one value to the closed `EntityRef` kind vocabulary, an additive change no consumer can break (the only enumeration, a test, was extended). It changes no stored data, credential, guard or existing contract, adds no concurrency and no delivery path, and every rule it introduces has a test that fails without it. `data/processed/` is gitignored working data that any snapshot can rebuild.
- No check was skipped. Push and PR remain the user's decision, not taken here.

## Log

### L1 — original request (verbatim)

````text
Implement the first ingestion slice of PoE2-RAG: the IngestSource use case, its ports,
a JSON ChunkRepository and the official patch notes parser, test-first.

## Context (read first)
- docs/architecture.md §4 (Chunk schema), §5 (IngestSource), §6 (ports), §7 (ingestion/ layout),
  §8 (testing), §10 (risks: parser snapshots; patch notes describe changes, not full states).
- docs/adr/0002-fuentes-de-datos-mvp.md: patch notes are indexed and are the source of truth for
  validity; access rules (identifiable User-Agent, rate limit, cache, robots.txt);
  data/raw/ and data/processed/ are gitignored because the repo is public.
- Existing shared domain: Chunk, EntityRef, SourceRef, Validity, PatchVersion, PatchCatalog,
  DomainError. Existing answering/application shows the port and use-case style to mirror.

## Goal
Turn one official patch notes thread into validated, persisted domain chunks, end to end,
without network access in tests.

## Modeling decision (MVP)
A patch note is its own entity, not a transition applied to gem chunks:
- `EntityRef(kind="patch_notes", slug="<major>-<minor>-<patch>", name="Patch <version>")`
- one Chunk per top-level section of the notes (section = slug of the heading),
- `Validity(valid_from=<that patch>, valid_until=None)`: a change is a fact from that patch on.
Applying notes as transitions that close `valid_until` on gem chunks needs poe2db data first;
it is out of scope here. Record this decision in the feature doc and in architecture.md §10.

## Scope
1. ingestion/application/ports.py (typing.Protocol):
   - `SourceFetcher.fetch(locator: str) -> RawDocument`
     where `RawDocument(locator, content: str, fetched_at: datetime, source_type: str)` is frozen.
   - `DocumentParser.parse(raw: RawDocument) -> Sequence[Chunk]`
   - `ChunkRepository.save(chunks: Sequence[Chunk]) -> None` (upsert by chunk_id)
     and `ChunkRepository.all() -> Sequence[Chunk]`.
2. ingestion/application/ingest_source.py: `IngestSource(fetcher, parser, repository, catalog)`;
   `execute(locator) -> IngestReport(locator, chunk_ids, skipped)`:
   fetch → parse → reject chunks whose valid_from is unknown to the PatchCatalog
   (specific error, fail loud) → reject duplicate chunk_ids within one document → save.
   Application errors subclass a new `IngestionError`.
3. ingestion/adapters/outbound/json_chunk_repository.py: one JSON file under a directory given
   by constructor (production default data/processed/). Lossless round-trip of every Chunk field,
   atomic write (temp file + replace), schema version field. Contract tests shared with an
   in-memory fake (tests/contract/test_chunk_repository_contract.py).
4. ingestion/adapters/outbound/local_file_fetcher.py: reads a saved HTML snapshot from disk.
   No HTTP fetcher in this task (polite HTTP fetching is the next task).
5. ingestion/adapters/outbound/patch_notes_parser.py: official forum thread HTML → chunks.
   - Detect the patch version from the thread title; unknown/unparseable → specific error.
   - `SourceRef(type="patch_notes", url=<thread url>, captured_at=<fetched_at date>)`.
   - Strip markup to clean text; keep bullet structure as lines.
   - beautifulsoup4 is allowed as an adapter-only dependency (`uv add beautifulsoup4`);
     the domain and application layers stay stdlib-only.

## Fixtures
- Tests never touch the network. Use small hand-written HTML fixtures under tests/fixtures/
  that mirror the real thread structure (title, post body, headings, bullet lists).
- To learn the real structure you may download ONE thread manually into data/raw/ (gitignored),
  after checking https://www.pathofexile.com/robots.txt, with an identifiable User-Agent.
  Never commit it. Short quoted lines in fixtures are fine; whole posts are not.
- Update architecture.md §10: parser tests run on committed minimal fixtures; full snapshots
  stay in the gitignored data/raw/.

## Rules
- TDD: tests first, observe RED, implement GREEN, refactor while green.
- Required tests: version detection (valid, missing, malformed); one chunk per section with
  correct chunk_id; validity starts at the detected patch and is open; empty/garbage HTML raises;
  unknown patch rejected by IngestSource; duplicate chunk_ids rejected; repository round-trip
  and upsert; atomic write leaves no partial file on failure.
- Out of scope: HTTP fetching, poe2db and poe2wiki adapters, ReindexCorpus, indexes, CLI `ingest`.
- Code, tests, docs and commit messages in English.
- About 400 authored changed lines per task is a planning heuristic, not a cap.

## Allowed edit surfaces
src/poe2_rag/ingestion/**
tests/unit/ingestion/**
tests/contract/test_chunk_repository_contract.py
tests/fixtures/patch_notes/**
pyproject.toml
uv.lock
odd/tasks/ingest-patch-notes.md
docs/architecture.md

## Delivery
- Branch feat/ingest from main (the local feat/ingest has no commits; reset it to main or
  recreate it). Feature doc odd/tasks/ingest-patch-notes.md with task IDs, route per task and
  RED/GREEN evidence.
- One Conventional Commit per task. No Co-Authored-By or any AI attribution.
- Do not push or open a PR.

## Verification
- `uv run pytest -q` passes. Report `<command>: <observed result>` and RED/GREEN counts per task.

## Return
Concise report: tasks done, commits, test counts, deviations from this prompt and why.
Close with a `## Key Learnings` block of 1–5 numbered standalone facts.
````

### L2 — decisions taken while planning
- `EntityRef` has a closed kind vocabulary that does not include `patch_notes`. Adding the kind to
  `src/poe2_rag/shared/domain/entity_ref.py` (one entry) is required by the modeling decision, so it is
  done even though it sits outside the listed edit surfaces; the shared domain test that enumerates the
  vocabulary gets the matching value. Everything else stays inside the surfaces.
- `RawDocument` has no URL field and a local snapshot is not a URL, so the parser recovers the thread URL
  from the thread's own `/forum/post-reply/<thread_id>` links (falling back to `/forum/view-thread/<id>`);
  when the locator already is an absolute http(s) URL, the locator wins. The future HTTP fetcher therefore
  needs no parser change.
- A document with no section headings (hotfix notes, as the downloaded snapshot shows) produces one chunk
  whose section is `notes`; text before the first section heading becomes a chunk whose section is
  `overview`. Both are documented in the parser docstring.
- The parser splits at the shallowest heading level found after the document title, so deeper headings stay
  inside their section instead of creating chunks; this is what "one chunk per top-level section" means.
- `IngestSource` skips chunks already stored with an identical value, so `IngestReport.skipped` is
  observable and a second run over the same snapshot writes nothing.
- `LocalFileFetcher` uses the snapshot's mtime as `fetched_at`: the citation date must be when the snapshot
  was captured, not when it was read.
- The downloaded reference thread is `0.5.5e` (thread 4009785), whose title the MVP rejects as unparseable
  because `PatchVersion` is `major.minor.patch`. It is used only to learn the page skeleton; the committed
  fixtures use numeric patch titles. The limitation is recorded in §10.
