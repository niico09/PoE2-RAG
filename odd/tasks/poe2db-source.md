# Feature: poe2db-source

First networked source of PoE2-RAG: a polite HTTP fetcher, an on-disk cache and a poe2db gem-page
parser, exposed as `poe2-rag ingest poe2db <url> [--refresh]`, test-first and offline by default.

## Specs

- **S1** — ADR-0002 "Condiciones de uso de poe2db" is the boundary: non-commercial use, attribution
  through `SourceRef`, no corpus redistribution (`data/raw` and `data/processed` are gitignored; the
  repository is public), and the four respectful-access rules: identifiable `User-Agent` with
  contact, rate limit, local cache, `robots.txt`.
- **S2** — Reuse, do not redefine: `ingestion/application/ports.py` `RawDocument(locator, content,
  fetched_at, source_type)`, `SourceFetcher.fetch(locator)`, `DocumentParser.parse(raw)` (a document
  it cannot handle raises, never returns empty); `local_file_fetcher.py` and `patch_notes_parser.py`
  as the reference style; `shared/domain` `Chunk`, `EntityRef` (with `active_gem`, `support_gem`),
  `Modifier` and `ModifierKind`, `SourceRef`, `Validity`, `PatchCatalog`;
  `ingestion/adapters/inbound/cli/ingest.py` `SOURCES` and its dispatch table; `bootstrap.py` and
  `config.py` wiring exactly like the patch-notes pair.
- **S3** — Observed on 2026-11-01 (one manual request): `poe2db.tw/robots.txt` disallows only
  `/*/marked` and `/*/markdown_history`. `https://poe2db.tw/us/Spark` is about 700 KB of
  server-rendered HTML with classes such as `gemitem`, `gemSkill`, `Stats`, `property`,
  `explicitMod`, `implicitMod` and gem tabs per level. The page carries no explicit patch version.
- **S4** — Validity of a poe2db snapshot: the page shows current values only. `valid_from` = the
  `PatchCatalog` version current at `fetched_at` (the latest release on or before that date),
  open-ended. If ingesting a newer snapshot would leave two open-ended versions of one entity
  overlapping, STOP and report the gap. Do not patch the application layer.
- **S5** — Allowed locators: only `https://poe2db.tw/us/<slug>` pages. Anything else is rejected
  before any network access.
- **S6** — The contact for the `User-Agent` comes from a required setting (`POE2_RAG_HTTP_CONTACT`).
  There is no committed default, and fetching without it is a configuration error.
- **S7** — `ingestion/adapters/outbound/http/polite_http_fetcher.py`: `PoliteHttpFetcher`
  implementing `SourceFetcher`.
  - The httpx2 client is injected (httpx2 is already a dependency; its installed transport-mocking
    API is `httpx2.MockTransport`).
  - `User-Agent` = `poe2-rag/<version> (+<contact>)`.
  - `robots.txt` via `urllib.robotparser`, fetched once per host and cached in memory; disallowed →
    a specific error.
  - Minimum interval between requests per host, with an injected clock and sleeper (no real sleeps in
    tests).
  - Timeouts. A non-2xx response → a specific error carrying the status. No retry loops.
- **S8** — The on-disk cache lives under the configured raw directory: one HTML file plus a small
  JSON sidecar (`url`, `fetched_at`, `status`) per page. A cache hit returns the stored content and
  the original `fetched_at`, with no network call. `--refresh` forces a new fetch. Cache files are
  written atomically.
- **S9** — `ingestion/adapters/outbound/poe2db/gem_page_parser.py`: `Poe2dbGemPageParser`
  implementing `DocumentParser` for `source_type` `"poe2db"`.
  - One entity per page: `active_gem` or `support_gem`, chosen from the page itself.
  - Sections at least: description, properties (tags, cost, cast time and similar), modifiers
    (explicit and implicit mods mapped to `Modifier`/`ModifierKind` where they parse cleanly).
  - Text must keep the exact numbers and wording, because citations quote it.
  - Per-level tables: decide after inspecting the fixture, and document the choice in this doc.
  - A page that is not a gem page → a specific parser error.
  - `SourceRef` carries the page URL and `fetched_at`.
- **S10** — Wiring: add `"poe2db"` to `SOURCES` and the dispatch table, with
  `ingest poe2db <url> [--refresh]` followed by `ReindexCorpus`, exactly like patch-notes; bootstrap
  builds the fetcher and parser lazily; config gains the contact, the request interval and the
  timeout, all validated.
- **S11** — Default suite (offline): fetcher via a mocked httpx2 transport (UA header, robots
  disallow, rate-limit sleeps computed from the fake clock, cache hit and miss, refresh, non-2xx,
  timeout, rejected locators); parser against fixtures; CLI dispatch with a fake container.
- **S12** — Fixtures: the repo is public. Commit only a trimmed excerpt of one active gem page and
  one support gem page, with just the markup the parser needs and an attribution comment (source URL,
  capture date, CC BY-NC-SA 3.0 / GGG content). Never commit a full page.
- **S13** — Integration (marked `integration`, not run by default): ingest the trimmed fixture through
  the real pipeline into `tmp_path`, then `ask` with a fake `LanguageModel`. Run once and report.
- **S14** — Network (marked `network`, excluded by default): one real fetch of
  `https://poe2db.tw/us/Spark` into `tmp_path`. Written but never run in CI; run at most once
  manually and report.
- **S15** — Domain and application layers stay untouched. A missing port, or the validity-overlap gap
  above, is reported, not patched.
- **S16** — Never fetch outside tests marked `network`. Never write outside `tmp_path` in tests.
  Never commit `data/raw`, `data/processed`, `data/index` or a full scraped page.
- **S17** — TDD: tests first, observe RED, implement GREEN, refactor while green. Code, tests, docs
  and commit messages in English. About 400 authored changed lines per task is a planning heuristic,
  not a cap. Out of scope: the poe2wiki adapter, crawling or link discovery (one URL per command),
  item and mechanic pages, `RunEvaluation`.
- **S18** — Branch `feat/poe2db-source` from `main`. One Conventional Commit per task. No
  `Co-Authored-By` or any AI attribution. Do not push or open a PR.
- **S19** — `uv run pytest -q` passes (offline default suite); `uv run pytest -q -m integration` is
  reported separately; `uv run poe2-rag ingest --help` lists `poe2db`; the report states
  `<command>: <observed result>` and RED/GREEN counts per task.

## Decisions

- **D1 — Per-level tables are one `levels` section.** The captured page's per-level table has 41 rows
  (header plus levels 1–40) and 9 columns, including both the base and the Cold-Infused variant. It is
  kept as one chunk whose lines are the header and each row joined exactly as rendered. One chunk per
  level would multiply identities and citations without adding precision for an MVP without
  level-aware retrieval; the ~2.9 KB table will be reported as a truncated chunk by the dense index,
  while the lexical index reads it whole (the visible debt ADR-0004 documents). A page without the
  table simply has no `levels` chunk. The page's "gem tabs" (`Base`, `Cold-Infused`) are variants, not
  per-level tabs; only the first (`Base`) contributes modifiers, so cold variant text is never
  attributed to the base gem. (S9, confirmed against the capture.)
- **D2 — Modifier parsing is deliberately conservative.** Only `N% increased|reduced|more|less X`
  and `+N X` become `Modifier`; a range such as `Adds 1 to 12 Lightning Damage` keeps its exact text
  and stays out of the structured tuple, because collapsing two numbers into one `value` would
  falsify it.
- **D3 — Validity uses `catalog.current()`.** `PatchCatalog` exposes `current()`, `known()` and
  `contains()` only; `TomlPatchCatalog` validates `released_on` and then discards it. There is no
  date-indexed history to ask, so the nearest faithful answer for a fresh fetch is the current
  version, and that is what the parser injects. The deviation and the overlap gap are recorded in
  the Log (L3, L4) and reported at close.

## Tasks

- [x] T1 — Branch `feat/poe2db-source`, feature doc, Engram mirror. Route: inline. S18.
- [x] T2 — `config.py`: `POE2_RAG_HTTP_CONTACT`, `POE2_RAG_HTTP_REQUEST_INTERVAL`,
  `POE2_RAG_HTTP_TIMEOUT`, validated, with tests. Route: inline. S6, S10.
- [x] T3 — `PoliteHttpFetcher` and its cache, with the offline test suite. Route: inline. S5, S7, S8, S11.
- [x] T4 — Network-marked Spark fetch; one manual run to capture the real markup. Route: inline. S14.
- [x] T5 — `Poe2dbGemPageParser`, trimmed fixtures and tests. Route: inline. S3, S9, S12.
- [ ] T6 — Wiring: `SOURCES`, dispatch, `--refresh`, bootstrap, `pyproject` markers and httpx2 as a
  runtime dependency, CLI tests. Route: inline. S10, S11, S14.
- [ ] T7 — Integration test through the real pipeline, run once. Route: inline. S13.
- [ ] T8 — README, ADR, architecture, full suite, independent verification, close. Route: inline +
  read-only verifier. S19.

## Evidence

- **T1** — `git switch -c feat/poe2db-source main` → branch created at `d47c870`; feature doc written;
  Engram mirror saved. No RED (documentation only). Commit `afd61e6`.
- **T2** — RED (implementation stashed after authoring the tests) → `ImportError: cannot import name
  'DEFAULT_HTTP_REQUEST_INTERVAL' from 'poe2_rag.config'`, collection error, 0 tests run (19 cases
  added). GREEN → `uv run pytest -q tests/unit/test_config.py` 44 passed; full suite 1013 passed, 13
  deselected. Contact defaults to `None`, a blank override means unset, and a header-breaking contact
  is rejected; interval and timeout are positive finite floats with the variable named on failure.
  Commit `7bea6f4`.

- **T3** — Delegated to `gentle-ai-worker` (context backstop) and reviewed inline. RED → collection
  error (`ModuleNotFoundError` for the new module, then the test directory shadowing the stdlib
  `http` package); the first implementation run showed 12 failed, 33 passed. GREEN → focused suite
  45 passed after the worker's fix, then 48 passed after the parent review; full suite 1061 passed, 13
  deselected. Commit: `feat(ingestion): fetch poe2db pages politely over HTTP`.
  - Review changes (test-first): the test file moved from `tests/unit/ingestion/adapters/outbound/http/`
    to `tests/unit/ingestion/adapters/outbound/` and the worker's `_httpx2` path-removal helper was
    deleted: the required production path is `outbound/http/`, but a *test* directory of that name
    is put on `sys.path` by pytest and shadows the stdlib `http` package that `httpx2` imports.
  - The sidecar gained `sha256` beside the required `url`, `fetched_at` and `status`: a failed
    sidecar replace could otherwise pair new HTML with an older sidecar's capture date. A digest
    mismatch raises `Poe2dbCacheError` and `--refresh` repairs it.
  - `fetch` returns the canonical URL as the locator (uppercase host canonicalizes), so a cache miss
    and a cache hit describe the same source. An unparseable port is rejected before any request.
  - Robots is fetched with the injected client (not `urllib.request`), so the default suite stays
    offline and the robots request obeys the same User-Agent, timeout and per-host interval.
- **T4** — `POE2_RAG_HTTP_CONTACT="<author>" uv run pytest -q -s -m network tests/network
  --basetemp=/tmp/poe2db-capture` → **1 passed in 3.42 s**; the adapter fetched robots.txt and then
  `https://poe2db.tw/us/Spark`, wrote 700,297 characters into
  `/tmp/poe2db-capture/.../poe2db/poe2db.tw-us-Spark.html`, and the sidecar carried the URL, the
  fetch time, status 200 and the digest. This is the single authorized live run; the page stayed
  outside the repository and is not committed. It confirmed the ~700 KB observation from S3.
  Commit: `test(ingestion): add the network-marked poe2db fetch`.

## Delivery slices

- One commit per task (S18). The feature branches from `main` at `d47c870`.
- The offline fetcher suite is the largest unit (48 cases plus the adapter); it lands as its own
  commit so the adapter and its tests are reviewable alone. The feature document is large because L1
  quotes the request verbatim; it travels with T1 and is extended with each task's evidence.
- The prompt's ~400 authored lines per task is a planning heuristic, not a cap (S17): the fetcher
  commit carries the adapter and the suite that proves it, rather than splitting a port
  implementation from its own tests.

- **T5** — RED → `ModuleNotFoundError: No module named
  'poe2_rag.ingestion.adapters.outbound.poe2db.gem_page_parser'`, 0 tests run (19 cases authored).
  GREEN → `uv run pytest -q tests/unit/ingestion/adapters/outbound/test_poe2db_gem_page_parser.py`
  19 passed; full suite 1080 passed, 14 deselected (the network test is excluded).
  - The parser was then run offline on the captured page (not committed): 4 chunks — description
    (184 chars), properties (205 chars, tags `Projectile/Lightning/Duration/Repeatable`),
    explicit-modifiers (194 chars, 4 lines, ranges preserved, no structured modifier because a range
    has two numbers) and levels (2,873 chars, 41 lines).
  - The tests caught two real defects before the fixtures passed: implicit mods sit nested inside the
    Base `Stats` block (the first search only looked at direct children), and a support modifier
    reads `Supported Skills deal 10% increased Projectile Damage`, so the percentage is not always the
    first token; the pattern now anchors on the percentage anywhere in the line and keeps the stat
    after it. `+12 to maximum Mana` maps to `Modifier("maximum Mana", 12.0, FLAT)`.
  - Only the first `.GemPopup` is read: the page repeats it four times for responsive layouts. The
    later variant tab (`Cold-Infused`) is excluded from modifiers so its cold values are never
    attributed to the base gem; quality mods are excluded too.
  - The page has neither a canonical link nor an `h1`, so a locator that is not an absolute http(s)
    URL raises `MissingPageUrlError` instead of inventing a citation URL. The fetcher always supplies
    the page URL, and the integration test goes through it.
  Commit: `feat(ingestion): parse poe2db gem pages`.

## Log

### L1 — Original request (verbatim)

> Add the first networked source to PoE2-RAG: a polite HTTP fetcher and a poe2db gem-page parser,
> exposed as `poe2-rag ingest poe2db <url>`, test-first and offline in the default suite.
>
> ## Context (read first)
> - docs/adr/0002-fuentes-de-datos-mvp.md, especially "Condiciones de uso de poe2db": non-commercial
>   use, attribution through SourceRef, no corpus redistribution (data/raw and data/processed are
>   gitignored; the repo is public), and the four respectful-access rules (identifiable User-Agent
>   with contact, rate limit, local cache, robots.txt).
> - Existing code to reuse, not redefine:
>   - ingestion/application/ports.py: RawDocument(locator, content, fetched_at, source_type),
>     SourceFetcher.fetch(locator), DocumentParser.parse(raw) (a document it cannot handle raises,
>     never returns empty).
>   - ingestion/adapters/outbound: local_file_fetcher.py and patch_notes_parser.py are the reference
>     style.
>   - shared/domain: Chunk, EntityRef (ENTITY_KINDS includes active_gem, support_gem), Modifier and
>     ModifierKind, SourceRef, Validity, PatchCatalog.
>   - ingestion/adapters/inbound/cli/ingest.py: the SOURCES tuple and its dispatch table.
>   - bootstrap.py and config.py: how the patch-notes pair is wired and how POE2_RAG_* settings are
>     read.
> - Observed on 2026-11-01 (one manual request): poe2db.tw/robots.txt disallows only /*/marked and
>   /*/markdown_history. https://poe2db.tw/us/Spark is about 700 KB of server-rendered HTML with
>   classes such as gemitem, gemSkill, Stats, property, explicitMod, implicitMod and gem tabs per
>   level. The page carries no explicit patch version.
>
> ## Design decisions (already taken)
> - Validity of a poe2db snapshot: the page shows current values only.
>   - valid_from = the PatchCatalog version current at `fetched_at` (the latest release on or before
>     that date), open-ended.
>   - Read how PatchCatalog exposes release dates and how IngestSource/JsonChunkRepository handle a
>     newer snapshot of the same entity. If ingesting a newer snapshot would leave two open-ended
>     versions of one entity overlapping, STOP and report the gap. Do not patch the application
>     layer.
> - Allowed locators: only https://poe2db.tw/us/<slug> pages. Anything else is rejected before any
>   network access.
> - The contact for the User-Agent comes from a required setting (POE2_RAG_HTTP_CONTACT). There is no
>   committed default, and fetching without it is a configuration error.
>
> ## Scope
> 1. ingestion/adapters/outbound/http/polite_http_fetcher.py: `PoliteHttpFetcher` implementing
>    SourceFetcher.
>    - The httpx2 client is injected. httpx2 is already a dependency; verify its transport-mocking
>      API in the installed version.
>    - User-Agent `poe2-rag/<version> (+<contact>)`.
>    - robots.txt via urllib.robotparser, fetched once per host and cached in memory. Disallowed →
>      a specific error.
>    - Minimum interval between requests per host, with an injected clock and sleeper (no real
>      sleeps in tests).
>    - Timeouts. A non-2xx response → a specific error carrying the status. No retry loops.
> 2. The on-disk cache lives under the configured raw directory: one HTML file plus a small JSON
>    sidecar (url, fetched_at, status) per page.
>    - A cache hit returns the stored content and the original fetched_at, with no network call.
>    - A `--refresh` flag forces a new fetch.
>    - Cache files are written atomically.
> 3. ingestion/adapters/outbound/poe2db/gem_page_parser.py: `Poe2dbGemPageParser` implementing
>    DocumentParser for source_type "poe2db".
>    - One entity per page: active_gem or support_gem, chosen from the page itself.
>    - Sections at least: description, properties (tags, cost, cast time and similar), modifiers
>      (explicit and implicit mods mapped to Modifier/ModifierKind where they parse cleanly).
>    - Text must keep the exact numbers and wording, because citations quote it.
>    - Per-level tables: decide after inspecting the fixture, and document the choice in the feature
>      doc.
>    - A page that is not a gem page → a specific parser error.
>    - SourceRef carries the page URL and fetched_at.
> 4. Wiring:
>    - add "poe2db" to SOURCES and the dispatch table, with `ingest poe2db <url> [--refresh]`
>      followed by ReindexCorpus, exactly like patch-notes;
>    - bootstrap builds the fetcher and parser lazily;
>    - config gains the contact, the request interval and the timeout, all validated.
>
> ## Testing
> - Default suite (offline):
>   - fetcher via a mocked httpx2 transport: UA header, robots disallow, rate-limit sleeps computed
>     from the fake clock, cache hit and miss, refresh, non-2xx, timeout, rejected locators;
>   - parser against fixtures;
>   - CLI dispatch with a fake container.
> - Fixtures: the repo is public. Commit only a trimmed excerpt of one active gem page and one
>   support gem page, with just the markup the parser needs and an attribution comment (source URL,
>   capture date, CC BY-NC-SA 3.0 / GGG content). Never commit a full page.
> - Integration (marked `integration`, not run by default): ingest the trimmed fixture through the
>   real pipeline into tmp_path, then `ask` with a fake LanguageModel. Run once and report.
> - Network (marked `network`, excluded by default): one real fetch of https://poe2db.tw/us/Spark
>   into tmp_path. Write it but DO NOT run it in CI; run it at most once manually and report.
>
> ## Rules
> - Domain and application layers stay untouched. A missing port, or the validity-overlap gap above,
>   is reported, not patched.
> - Never fetch outside tests marked `network`. Never write outside tmp_path in tests.
> - Never commit data/raw, data/processed, data/index or a full scraped page.
> - TDD: tests first, observe RED, implement GREEN, refactor while green.
> - Code, tests, docs and commit messages in English.
> - About 400 authored changed lines per task is a planning heuristic, not a cap.
> - Out of scope: the poe2wiki adapter, crawling or link discovery (one URL per command), item and
>   mechanic pages, RunEvaluation.
>
> ## Allowed edit surfaces
> src/poe2_rag/ingestion/adapters/outbound/http/**
> src/poe2_rag/ingestion/adapters/outbound/poe2db/**
> src/poe2_rag/ingestion/adapters/inbound/cli/**
> src/poe2_rag/bootstrap.py
> src/poe2_rag/config.py
> tests/unit/**
> tests/integration/**
> tests/network/**
> tests/fixtures/poe2db/**
> pyproject.toml
> uv.lock
> README.md
> .env.example
> odd/tasks/poe2db-source.md
> docs/adr/0002-fuentes-de-datos-mvp.md
> docs/architecture.md
>
> ## Delivery
> - Branch feat/poe2db-source from main. Feature doc odd/tasks/poe2db-source.md with task IDs, the
>   route per task and RED/GREEN evidence.
> - One Conventional Commit per task. No Co-Authored-By or any AI attribution.
> - Do not push or open a PR.
>
> ## Verification
> - `uv run pytest -q` passes (offline default suite).
> - `uv run pytest -q -m integration` result reported separately. Network test result reported only
>   if run manually.
> - `uv run poe2-rag ingest --help` lists poe2db.
> - Report `<command>: <observed result>` and RED/GREEN counts per task.
>
> ## Return
> Concise report: tasks done, commits, test counts, the per-level table decision, whether the
> validity-overlap gap exists, deviations from this prompt and why.
> Close with a `## Key Learnings` block of 1–5 numbered standalone facts.

### L2 — Edit surface vs. the required CLI flag

`--refresh` and the help listing live in `src/poe2_rag/cli.py` (`_build_parser` and `_dispatch`),
which the prompt does not list among the allowed edit surfaces. Dropping the flag would drop a
stated requirement, so the minimal change is made there (the subparser flag plus the source-aware
runner selection) and reported as a deviation. No business logic moves into the top-level CLI.

### L3 — `PatchCatalog` exposes no release dates

`shared/domain/patch_catalog.py` publishes `current()`, `known()` and `contains()`; the protocol has
no date-indexed lookup. `shared/adapters/outbound/toml_patch_catalog.py` validates each
`released_on` entry and then discards it (`_entry_version` returns only the version). Therefore
"the latest release on or before `fetched_at`" is not computable from the port, and the parser uses
`catalog.current()` (D3). The correct fix is a port change (a release-date-aware lookup), which S15
forbids here; it is reported instead of patched.

### L4 — The validity-overlap gap exists

`IngestSource` (`ingestion/application/ingest_source.py`) validates and then upserts by `chunk_id`;
`chunk_id` embeds `valid_from` but not the document. `JsonChunkRepository.save` replaces by
`chunk_id` and leaves unmentioned chunks untouched. A second snapshot of the same gem parsed at a
later `catalog.current()` therefore produces a new `chunk_id` set, while the previous open-ended
(`valid_until=None`) chunks remain stored: two versions of one entity both claim validity from their
own patch onwards. No code closes the older range, and the parser cannot see stored chunks. This is
the gap S4 predicts; it is reported, not patched (S15). Consequence: until a document-scoped port
exists, refresh after a patch bump adds a second open-ended version instead of superseding the
first.

### L5 — httpx2 in the dev group only

`pyproject.toml` lists `httpx2` under `[dependency-groups].dev`. The fetcher is a runtime adapter, so
an installed wheel would fail without it. It moves to `[project].dependencies` (same version, same
lock) and the dev group keeps only `pytest`. This is a runtime dependency change and triggers the
independent verifier at close.

### L6 — Fixture provenance

The repository had no committed poe2db capture and the prompt forbids fetching outside `network`
tests. The single authorized `network` run (T4) captured Spark into `tmp_path`; the committed
`active_gem_spark.html` is a trimmed excerpt of that capture (header, properties, description, Base
modifier tab and three rows of the level table; 6.8 KB instead of the 700 KB page; remote/interactive
attributes stripped; attribution comment included). The captured page contained no support-gem
markup at all, so `support_gem_pierce.html` is a structural reconstruction of the same observed
shape, labelled as such in its comment, with illustrative values for the parser tests. Neither file
is a full page and both stay well under a page's worth of markup.
