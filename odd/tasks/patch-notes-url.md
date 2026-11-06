# Feature: patch-notes-url

## Objective
Let `poe2-rag ingest patch-notes <url>` fetch an official forum thread over the existing polite HTTP
fetcher, so the next patch-notes ingests (0.5.5, 0.5.5a-0.5.5d) no longer need a hand-written capture
sidecar, and `--refresh` re-captures a thread the same way it does for poe2db.

## Specs
- **S1** — Locator dispatch. Verbatim: "The `patch-notes` locator may be either a local path or an
  http(s) URL." / "Local paths behave exactly as today, sidecar rule included." / "URLs are fetched with
  the polite fetcher, and `captured_at` comes from the fetch time (fetched_at)." / "Make the choice in
  the composition root or in a small fetcher selected by locator. Neither the use case nor the parser
  branches on transport."
- **S2** — URL allow-list. Verbatim: "Accept only https URLs on www.pathofexile.com under
  `/forum/view-thread/<id>`. Reject anything else with a specific error that the CLI reports through its
  existing error contract (non-zero exit, one line). Do not fetch arbitrary hosts."
- **S3** — Refresh. Verbatim: "`--refresh` bypasses the cache for patch-notes URLs, the same way it does
  for poe2db." Interpreted (see L3.3): a local snapshot has no cache, so `--refresh` with a patch-notes
  *path* stays a usage error (exit 2) instead of a silent no-op, and the message names both supported
  cases: `--refresh is only supported by the poe2db source and patch-notes URLs`.
- **S4** — Identity parity. Verbatim: "The snapshot identity and the version detection stay as they are
  today. Ingesting the same thread from a URL and from a local capture of it must produce the same chunk
  ids."
- **S5** — Documentation. Verbatim: "Update the README/CLI help usage line and docs/architecture.md where
  ingest sources are listed."
- **S6** — Tests. Verbatim: "Unit tests with fakes: URL vs. path selection, rejected hosts and paths,
  `--refresh` propagation, and fetched_at used as captured_at." / "Parser test: the existing trimmed
  thread fixture, wrapped as a RawDocument coming from the HTTP fetcher, parses to the same chunks as the
  local path." / "One `network`-marked test fetches the 0.5.5e thread through the real pipeline into a tmp
  data dir (never data/). Run it once manually and report the result. It must n"
  Interpreted (see L2.1, L3.4): the network test must not run in the default suite (`network` marker, run
  once by hand), and the parity test goes through the locator dispatcher with the fake HTTP transport
  instead of a hand-built `RawDocument`, so a drift in `source.type` or `source.url` — the only inputs the
  transport can move — makes it fail. The CLI boundaries this feature fixes are: `ingest patch-notes <url>
  --refresh` exits 0 with no usage error; `ingest patch-notes <path> --refresh` exits 2;
  `ingest poe2db <path>` keeps exiting 1 with the fetcher's one-line `only https URLs are allowed`; and
  `ingest patch-notes ftp://x` returns exit 1 with one `error: only https URLs are allowed, got 'ftp://x'`
  line in both cases, with and without `--refresh`.
- **S7** — Layers. Verbatim: "Domain and application layers stay untouched unless a test proves they must
  change. If one must, stop and report why."
- **S8** — TDD. Verbatim: "TDD: tests first, observe RED, implement GREEN, refactor while green."
- **S9** — Commits (interpreted; see L2.1): "ONE CONVENTIONAL COMMIT PER TASK, in task order, each
  leaving the su[ite green]. Do not hand the whole feature to one worker as each task closes. Do not leave
  the tasks for a single final commit."
- **S10** — Language (interpreted; see L2.1): "Code, tests, docs, the feature document and commit messages
  in Engli[sh]."
- **S11** — Corpus hygiene. Verbatim: "Never commit data/raw, data/processed, data/index, .env or full
  captures. Never log POE2_RAG_HTTP_CONTACT beyond the User-Agent it already builds."
- **S12** — Delivery (interpreted; see L2.1): "Branch feat/patch-notes-url from main. Feature doc
  odd/tasks/patch-ne[notes-url.md with route per task, RED/GREEN evidence and the commit hash per task]."
  Verbatim: "No Co-Authored-By or any AI attribution." / "Do not push or open a PR."
- **S13** — Out of scope (interpreted; see L2.1): "Out of scope: ingesting the 0.5.5–0.5.5d threads into
  the real corpu[s (they land after] the merge), crawling forum indexes, eval dataset, retrieval tuning."
- **S14** — Verification (interpreted; see L2.1): "`uv run pytest -q` passes (offline default suite)." /
  "`uv run pytest -q -m network` result reported separately (one manual run)." / "`git log --oneline
  main..HEAD` shows one commit per task." / "Report `<command>: <observed result>` and RED/GREEN counts
  per task."

## Non-goals
- Ingesting 0.5.5-0.5.5d into the real corpus, crawling forum indexes, the eval dataset, retrieval tuning.
- A general-purpose multi-host HTTP client: the fetch policy stays closed to two hosts (poe2db, the forum).

## Tasks
Surfaces: L1's list plus the three entries L3.1 adds — `tests/network/**`, `pyproject.toml` and
`tests/cli_fakes.py`.

- [ ] T1 — Branch `feat/patch-notes-url` from `main`, this feature document, Engram mirror. Route: inline.
  S12. Commit: `docs(odd): plan the patch-notes URL ingest`.
- [x] T2 — Closed fetch policy with poe2db as the default. `HttpSourcePolicy` is a frozen dataclass with
  `host: str`, `path: PathRule`, `source_type: str` and `cache_directory: str`; `PathRule` is a frozen
  dataclass with `prefix: str`, `slug: re.Pattern[str]` and `slug_message: str`, consumed by one shared
  ordered validator (prefix → trailing slash → slug fullmatch) so the rule is declared and not a mini-DSL.
  The scheme stays fixed to https for every policy, so it is a literal and not a fourth field. One string
  per concern: `cache_directory` is the only cache name and the message noun derives from it
  (`cache_directory.replace("_", "-")`, so poe2db keeps `poe2db` and the forum reads `patch-notes`),
  exactly as `source_type` comes from the parser's export instead of a second literal. Messages that
  derive from the policy: the host (`only {host} is allowed, got …`), the prefix (`path must start with
  {prefix!r}, got …`), the slug (its own `slug_message`) and the cache noun; the ones that stay literal
  because they are host-independent: scheme, port, userinfo, query, fragment and trailing slash. Before the
  refactor, add the four missing message assertions as RED — host (`only poe2db.tw is allowed`), prefix
  (`path must start with '/us/'`), trailing slash and slug (`slug must be non-empty with no slash`) —
  parametrized over the poe2db policy and the forum policy: today only the trailing-slash substring is
  pinned (`test_polite_http_fetcher.py:185,191`) while the host, prefix and slug sentences live unpinned in
  `polite_http_fetcher.py:185,202,208`, which is how the shared validator could change a user-visible
  poe2db message with the suite still green. The same RED covers the three cache sentences the noun change
  rewrites — `polite_http_fetcher.py:310` (`poe2db cache is unreadable …`), `:343` (`poe2db cache sidecar is
  malformed …`) and `:373` (`cannot write poe2db cache …`) — which no test asserts today and which are the
  operator's only remediation hint (`use --refresh to rebuild it`) on a one-line exit 1. Also, a test
  instantiates the policy with exactly the four declared fields, so its signature is fixed. `cache_directory` is a single path segment validated at construction
  (non-blank, no separator, not `.`/`..`) and appended to the constructor's `cache_dir` exactly as
  `_CACHE_SUBDIR` is today: `bootstrap.py:341` keeps passing `cache_dir=self._config.raw_dir`, so the
  operator's `data/raw/poe2db/` captures stay where they are and nothing is orphaned. The forum policy is
  `https://www.pathofexile.com` with `/forum/view-thread/<digits>` and `cache_directory="patch_notes"`; its
  `source_type` is the parser's exported `SOURCE_TYPE`, and a test pins that value to what `PatchNotesParser`
  accepts (the same value parses `tests/fixtures/patch_notes/full_020_thread.html`; a different one raises
  `UnsupportedSourceTypeError`). The exported constant is the only literal on the wired path: the parser's
  own `SourceRef("patch_notes", …)` at `patch_notes_parser.py:220` becomes `SourceRef(SOURCE_TYPE, …)`, and
  `bootstrap.py:321` passes `LocalFileFetcher(source_type=patch_notes_parser.SOURCE_TYPE)`, so the local
  adapter keeps its own `DEFAULT_SOURCE_TYPE` default (a literal that survives only as an unused default)
  while the wired path uses the parser's constant and no adapter imports another. A test pins
  `DEFAULT_SOURCE_TYPE == patch_notes_parser.SOURCE_TYPE`, so the two spellings cannot drift apart silently; `EntityRef("patch_notes", …)` at `patch_notes_parser.py:215-219` is deliberately
  untouched — it is the entity *kind*, validated against `ENTITY_KINDS`
  (`shared/domain/entity_ref.py:10`), a different concept that only coincides in text. `FORUM_POLICY` itself
  is constructed in `bootstrap.py` — the one module that already imports both the parser and the fetcher —
  and passed in as `PoliteHttpFetcher(policy=FORUM_POLICY)`: no adapter imports another, so importing
  `poe2_rag.cli` can never drag `beautifulsoup4` in through `classify_locator`'s module (ADR-0006). Rename `Poe2dbCacheError` to `HttpCacheError` with no alias —
  `EXPECTED_ERRORS` lists `HttpFetchError` (`bootstrap.py:134`), so the old name has no contract behind it —
  updating `http/__init__.py:9,22` and `test_polite_http_fetcher.py:24,479,492,519,549,608`, the test whose
  name still says `..._raises_poe2db_cache_error` included, and stop the three remaining single-host
  sentences in `polite_http_fetcher.py:1`, `:47` and `:108` from claiming one host. Tests, accepted
  locators as literals: `https://www.pathofexile.com/forum/view-thread/1`,
  `.../view-thread/42`, `.../view-thread/123456` and `.../view-thread/4009785` (different id lengths);
  rejected: `http://www.pathofexile.com/forum/view-thread/1`, another host, a port, userinfo, query,
  fragment, a trailing slash, `/forum/view-thread/`, a non-digit id, an extra segment,
  `/forum/post-reply/<id>` and a poe2db URL; robots, throttle and cache behaviour unchanged for both
  policies; a forum cache error names `patch-notes` and its URL and never `poe2db`, and writes under
  `<cache_dir>/patch_notes/`. The poe2db suites keep their paths and constructor untouched:
  `test_poe2db_network.py:42`, `test_polite_http_fetcher.py:97,137,152` and the `tmp_path/"poe2db"`
  assertions at `:108,:443,:552`. Route: inline. S2, S3.
- [x] T3 — Locator dispatch with one classifier. The pure classifier lives in
  `src/poe2_rag/ingestion/adapters/locator.py`, a stdlib-only sibling of `inbound/` and `outbound/` — not
  inside `outbound/`, because the CLI imports it at module scope and importing any outbound submodule also
  loads the `outbound` package the import guard forbids. It exports
  `LocatorKind` (`HTTP`, `OTHER_SCHEME`, `PATH`) and `classify_locator(locator) -> LocatorKind`: an
  `http`/`https` scheme is `HTTP`, any other `scheme://` shape is `OTHER_SCHEME` — it still reaches the HTTP
  branch and fails with its specific `UnsupportedLocatorError`, per S2 — and everything else is `PATH`. It
  is a three-way classifier and not a boolean `is_http_locator` because `ftp://x` must keep the same error
  class with and without `--refresh` (S6), and a boolean would need a second predicate for the CLI guard —
  the divergence this design exists to prevent. The dispatcher lives in
  `src/poe2_rag/ingestion/adapters/outbound/locator_fetching.py` and imports the classifier from that
  sibling module. `LocatorFetcher(local, http)` maps `PATH` to the local branch
  and `HTTP`/`OTHER_SCHEME` to the HTTP branch, and the CLI guard of T4 calls `classify_locator` too, so one
  table feeds both. Nesting
  is explicit and pinned by tests: `LocatorFetcher` sits **on top**, the path branch is today's
  `_RawDirectoryFetcher(raw_dir, LocalFileFetcher())` (relative locator resolved against
  `POE2_RAG_RAW_DIR`, absolute kept as given) and the URL branch is `PoliteHttpFetcher(policy=FORUM_POLICY)`,
  so the raw-directory wrapper never prefixes a URL. One parametrized table (`https://…`, `http://…`,
  `ftp://x`, `https:/x`, relative path, absolute path) drives the classifier test and the per-branch
  dispatch tests; the CLI boundaries of S6 belong to T4, because `classify_locator` is pure and can assert
  no exit code or stderr. Parity runs through `LocatorFetcher` with the fake `MockTransport` client
  that `test_polite_http_fetcher.py` already uses, against the local run over the same fixture, comparing
  full `chunk_id`s, `source.type`, `source.url` and the detected version; a separate test pins
  `captured_at == fetched_at.date()` for a fresh fetch and for a cache hit. Route: inline. S1, S4, S6.
- [ ] T4 — Composition root and CLI. `bootstrap.Container` gets one method
  `ingest_source(*, source: str, refresh: bool) -> IngestSourceRunner` that raises
  `ValueError(f"unknown source: {source!r}")` with the same shape as
  `ingestion/adapters/inbound/cli/ingest.py:88-90` (the `if handler is None:` guard and its `raise`), so an
  unknown name never yields a patch-notes runner; a
  parametrized test over `ingest_cli.SOURCES` × `refresh` in `{False, True}` proves every published source
  resolves and keeps the two layers in sync. Only the patch-notes runner is wired with `LocatorFetcher`;
  the poe2db pair keeps `PoliteHttpFetcher` directly, so `ingest poe2db <path>` still fails in the fetcher
  with `only https URLs are allowed` instead of reading the file and failing inside the gem parser — pinned
  by a new CLI test (exit 1, one error line). `CliContainer.ingest_source` becomes that method and
  `Poe2dbIngestContainer` plus the per-command `cast` disappear (`cli.py:62`, `cli.py:206-214`). The class
  drops `poe2db_ingest_source` as a public entry point as well: the per-policy instance caching moves into
  private builders behind the one method, so there is exactly one way to obtain a runner. That migrates the
  seven call sites in `tests/unit/test_bootstrap.py` — `:349,356` (built lazily from its own fetcher and
  parser), `:362,363` (a refresh run selects a distinct runner), `:379` (the configured HTTP settings),
  `:396` (a missing contact fails when the runner is asked for) and `:414` (building makes no request) —
  which keep their `_fetcher`/`_parser`/`_repository`/`_catalog` and identity assertions through
  `ingest_source(source="poe2db", refresh=…)`. The CLI
  guard at `cli.py:257-259` uses `classify_locator` from T3: `--refresh` is accepted for poe2db and for any
  patch-notes locator that is not `PATH`; a patch-notes path still exits 2 with the message of S3. Both
  `_build_parser` help texts and the `metavar` say `path or URL`. Call sites updated with the protocol:
  `tests/cli_fakes.py:106,132`, `tests/unit/test_cli.py:57,77,194,209,217` and
  `tests/unit/test_bootstrap.py:137,150,312,394`; `FakeContainer` returns its runner from a method that
  records `(source, refresh)` and the assertions read the stored runner. `FakePoe2dbContainer`
  (`tests/unit/test_cli.py:166-175`, with `refresh_calls` at `:171` and its own `poe2db_ingest_source` at
  `:173`) becomes redundant under that record and is deleted, its `refresh_calls` assertions moving to the
  `(source, refresh)` log. The import-boundary guard `test_importing_the_cli_pulls_in_no_adapter_dependency`
  (`tests/unit/test_cli.py:134-152`) grows one assertion beyond `chromadb`/`anthropic`/`torch`: after
  importing `poe2_rag.cli`, `bs4` and, by name,
  `poe2_rag.ingestion.adapters.outbound.patch_notes_parser`,
  `poe2_rag.ingestion.adapters.outbound.http.polite_http_fetcher` and
  `poe2_rag.ingestion.adapters.outbound.local_file_fetcher` must be absent from `sys.modules`. No glob: the
  `outbound` package itself loads with any submodule of it, and the three inbound CLI adapters the module
  imports on purpose are allowed. That is what catches a policy object or a classifier that drags the bs4
  parser into the CLI edge. `tests/unit/test_cli.py:197-211`
  carries the four CLI cases of S6: `ingest patch-notes <url> --refresh` → exit 0; `ingest patch-notes
  <path> --refresh` → exit 2; `ingest poe2db <path>` → exit 1 with one line; and `ingest patch-notes ftp://x`
  with *and* without `--refresh` → exit 1 with one `error: only https URLs are allowed, got 'ftp://x'` line
  each, which is the only case that catches the guard rejecting `OTHER_SCHEME` as a usage error. Route:
  inline. S3, S5, S6.
- [ ] T5 — Network test. `tests/network/test_patch_notes_network.py`, marked `network`, mirrors the poe2db
  network test: the real `PoliteHttpFetcher` with the forum policy, `PatchNotesParser`, `JsonChunkRepository`
  and a catalog containing 0.5.5e run through `IngestSource` with every directory under `tmp_path` (never
  `data/`), asserting the detected version, `source.type`, `source.url`, the chunk list and the cache under
  `tmp_path/patch_notes/`. The `network` marker description in `pyproject.toml:44` is generalized to the
  remote hosts it now covers. Run once manually and record the result. Route: inline. S6.
- [ ] T6 — Documentation. README usage gains the URL form, and the sidecar paragraph says the sidecar is
  the local-snapshot rule while a URL takes `captured_at` from the fetch, naming the URL allow-list, the
  `data/raw/patch_notes/` cache and `--refresh`; `docs/architecture.md` §5.2 (the poe2db paragraph and the
  ingest-source list), the `SourceFetcher` row of §6 and the README status/roadmap lines follow. Two README
  sentences become false with this feature and are named here: `README.md:79` ("La fuente `poe2db` solo
  acepta páginas … y exige `POE2_RAG_HTTP_CONTACT`") must say that any source fetched over HTTP needs the
  contact, naming the forum allow-list next to the poe2db one, and `README.md:133` (the `POE2_RAG_RAW_DIR`
  row, "raíz de los snapshots") must say it is also the root of the HTTP cache. Route: inline. S5.
- [ ] T7 — Close: full suite, per-spec verdict, evidence, commit hashes. The per-spec verdict walks every
  S#, S5 included, and names `README.md:79` and `:133` among the documentation checks. Route: inline. S8,
  S14.

## Evidence
Filled as each task closes: RED → GREEN counts, the focused command, and the commit hash from
`git log --oneline main..HEAD`.

- T1 —
- T2 — RED: 2 collection errors (`ImportError`: `POE2DB_POLICY`, `HttpCacheError`, `HttpSourcePolicy`,
  `PathRule` and `FORUM_POLICY` do not exist), blocking the 42 new cases in `test_polite_http_fetcher.py`,
  `test_bootstrap.py` and `test_local_file_fetcher.py`. GREEN:
  `uv run pytest -q tests/unit/ingestion/adapters/outbound/test_polite_http_fetcher.py
  tests/unit/test_bootstrap.py tests/unit/ingestion/adapters/outbound/test_local_file_fetcher.py` → 136
  passed; `uv run pytest -q` → 1448 passed, 24 deselected (+42 on the 1406 baseline). The four locator
  sentences and the three cache sentences are drift pins: they pass before and after, and fail only if the
  shared validator changes a user-visible message. Commit: `feat(ingest): declare the HTTP fetch policy in
  one place`.
- T3 — RED: `uv run pytest -q tests/unit/ingestion/adapters/test_locator.py
  tests/unit/ingestion/adapters/outbound/test_locator_fetching.py` → 11 failed, 5 passed (both modules
  import `LocatorKind`/`classify_locator`/`LocatorFetcher`, which did not exist). GREEN: the same command →
  17 passed; `uv run pytest -q` → 1465 passed, 24 deselected (+17 on 1448). Later refactor while green:
  the dispatcher table became one row per `(kind, branch)` decision, `captured_at` is compared with the
  cache sidecar's `fetched_at` instead of `date.today()`, and the temporary-path literal lost its `noqa`.
  Commit: `feat(ingest): dispatch the patch-notes locator by kind`.
- T4 —
- T5 —
- T6 —
- T7 —

## Findings applied (gate rounds 1-6)

Every entry was applied to this plan before any source write. The decisions live in L3; this table keeps
the history without repeating document line numbers.

| # | Role | Finding | Fix |
|---|------|---------|-----|
| 1 | QA | T4 hid the `--refresh` guard and the test that pins it | S3 defines the local-path case; T4 names both code sites and both tests |
| 2 | QA | Truncated quotes labelled "Verbatim" | Truncated specs marked "(interpreted; see L2.1)" with the full sentence |
| 3 | QA | T5 had no surface for the network test | L3.1 adds `tests/network/**` |
| 4 | QA | `network` marker named poe2db only | L3.1 adds `pyproject.toml`; T5 generalizes the description |
| 5 | Review | BLOCKER: plan did not name what makes S3 unreachable | T4 names `cli.py:62,206-214,257-259`; one container method replaces the cast |
| 6 | Review | Policy `source_type` duplicated the literal S4 measures | Parser exports `SOURCE_TYPE`; a test pins policy to parser |
| 7 | Review | poe2db nouns left in shared cache code | Policy carries the cache segment and noun; `HttpCacheError` |
| 8 | Review | Parity test could not fail for S4's reason | Parity runs through the dispatcher with the fake transport |
| 9 | Review | Dispatcher position relative to `_RawDirectoryFetcher` unspecified | T3 fixes the nesting and tests relative, absolute and URL |
| 10 | Review | No place to record per-task evidence | `## Evidence` section plus a commit slot per task |
| 11 | QA | Protocol change breaks fakes outside the surfaces | L3.1 adds `tests/cli_fakes.py`; T4 names every call site |
| 12 | QA | "Final cache directory" would orphan the poe2db captures | L3.6: one validated segment appended to `cache_dir` |
| 13 | QA | Unclear whether poe2db goes through the dispatcher | L3.11: only patch-notes is wired with `LocatorFetcher` |
| 14 | QA | Two locator predicates could diverge | L3.7: one three-way classifier feeds dispatcher and guard |
| 15 | Review | `Poe2dbCacheError` alias had no consumer | L3.9: renamed to `HttpCacheError`, no alias |
| 16 | Review | "The path rule" had no declared shape | L3.10/L3.12: declared dataclasses and derived-vs-literal split |
| 17 | QA | Claimed poe2db messages were pinned; only one was | L3.13: four locator and three cache assertions as RED first |
| 18 | QA | Three single-host sentences and a test name missed | T2 names `polite_http_fetcher.py:1,47,108` and the test rename |
| 19 | Review | `LocatorKind.URL` meant the opposite | Renamed `OTHER_SCHEME` |
| 20 | Review | "Accept the four thread shapes" unresolvable | T2 lists the four accepted locators literally |
| 21 | Review | `scheme` and `cache_noun` reintroduced drift | Scheme fixed to https; cache noun derived from the segment |
| 22 | Review | S6 ended in a dangling colon | Closed with the observable outcome |
| 23 | QA | Fail-closed citation pointed at the docstring | `ingest.py:88-90` |
| 24 | Review | T4 never decided `poe2db_ingest_source`'s fate | Method stops being public; seven `test_bootstrap.py` sites named |
| 25 | Review | Two README sentences left false | T6 names `README.md:79` and `:133`; T7 checks them |
| 26 | QA | `FakePoe2dbContainer` missing from T4's inventory | Deleted; `refresh_calls` move to the `(source, refresh)` log |
| 27 | Review | `FORUM_POLICY`'s module could drag bs4 into the CLI | Built in `bootstrap.py`; T4's import guard covers it |
| 28 | Review | Two literals of `SOURCE_TYPE` remained | `SourceRef(SOURCE_TYPE, …)`; bootstrap injects into `LocalFileFetcher` |
| 29 | QA/Review | BLOCKER: the proposed import guard can never pass | Guard names the heavy outbound modules and `bs4`, no glob |
| 30 | QA | T2 had an adapter importing another adapter | Bootstrap passes `source_type=`; the local adapter keeps its default |
| 31 | QA | The guard's glob still matched the `outbound` package | Classifier moved to `ingestion/adapters/locator.py`; module paths declared in T3 |
| 32 | QA | "The only literal" was still false | "The only literal on the wired path" plus a test pinning both spellings equal |
| 33 | Review | `bootstrap.py:322` pointed at the parser argument | Citation corrected to `:321` |

## Log

### L1 — original request (verbatim, as received)

````text
Let `poe2-rag ingest patch-notes` take a forum thread URL, fetched with the existing polite HTTP
fetcher, so patch notes no longer need a hand-written capture sidecar. Work test-first.

## Context (read first)
- Today `ingest patch-notes <path>` only reads a local HTML file through the local fetcher, which
  requires a sidecar `<file>.meta.json` with {"captured_at": "<ISO 8601 tz-aware>"}. The first
  real ingest (0.5.5e, https://www.pathofexile.com/forum/view-thread/4009785) was done that way by
  hand. The next ingests are 0.5.5, 0.5.5a, 0.5.5b, 0.5.5c and 0.5.5d. Copying that manual step for
  each is error-prone.
- poe2db already uses a polite HTTP fetcher. It sends a User-Agent with the contact from
  POE2_RAG_HTTP_CONTACT, honors robots.txt, rate-limits, and caches in data/raw with `.meta.json`
  sidecars. It returns RawDocument(locator, content, fetched_at, source_type).
- Code:
  - src/poe2_rag/ingestion/adapters/inbound/cli/ingest.py (SOURCES and the dispatch table);
  - src/poe2_rag/bootstrap.py (poe2db_ingest_source and the patch-notes pair);
  - src/poe2_rag/ingestion/adapters/outbound/patch_notes_parser*;
  - src/poe2_rag/ingestion/adapters/outbound/http/polite_http_fetcher.py.
- The patch-notes parser already parses the forum thread HTML, including the captured page: it
  keeps the official post and ignores user replies. Verify that it accepts the fetched bytes
  unchanged before changing anything.

## Required behavior
1. The `patch-notes` locator may be either a local path or an http(s) URL.
   - Local paths behave exactly as today, sidecar rule included.
   - URLs are fetched with the polite fetcher, and `captured_at` comes from the fetch time
     (fetched_at).
   - Make the choice in the composition root or in a small fetcher selected by locator. Neither the
     use case nor the parser branches on transport.
2. Accept only https URLs on www.pathofexile.com under /forum/view-thread/<id>. Reject anything else
   with a specific error that the CLI reports through its existing error contract (non-zero exit,
   one line). Do not fetch arbitrary hosts.
3. `--refresh` bypasses the cache for patch-notes URLs, the same way it does for poe2db.
4. The snapshot identity and the version detection stay as they are today. Ingesting the same thread
   from a URL and from a local capture of it must produce the same chunk ids.
5. Update the README/CLI help usage line and docs/architecture.md where ingest sources are listed.

## Testing
- Unit tests with fakes: URL vs. path selection, rejected hosts and paths, `--refresh` propagation,
  and fetched_at used as captured_at.
- Parser test: the existing trimmed thread fixture, wrapped as a RawDocument coming from the HTTP
  fetcher, parses to the same chunks as the local path.
- One `network`-marked test fetches the 0.5.5e thread through the real pipeline into a tmp data dir
  (never data/). Run it once manually and report the result. It must n

## Rules
- Domain and application layers stay untouched unless a test proves they must change. If one must,
  stop and report why.
- TDD: tests first, observe RED, implement GREEN, refactor while green.
- ONE CONVENTIONAL COMMIT PER TASK, in task order, each leaving the su
  worker as each task closes. Do not leave the tasks for a single final commit.
- Code, tests, docs, the feature document and commit messages in Engli
- Never commit data/raw, data/processed, data/index, .env or full captures. Never log
  POE2_RAG_HTTP_CONTACT beyond the User-Agent it already builds.
- About 400 authored changed lines per task is a planning heuristic, not a cap.
- Out of scope: ingesting the 0.5.5–0.5.5d threads into the real corpu
  the merge), crawling forum indexes, eval dataset, retrieval tuning.

## Allowed edit surfaces
src/poe2_rag/ingestion/adapters/**
src/poe2_rag/bootstrap.py
src/poe2_rag/config.py
src/poe2_rag/cli.py
tests/unit/**
tests/integration/**
tests/fixtures/patch_notes/**
odd/tasks/patch-notes-url.md
docs/architecture.md
README.md

## Delivery
- Branch feat/patch-notes-url from main. Feature doc odd/tasks/patch-ne
  route per task, RED/GREEN evidence and the commit hash per task.
- No Co-Authored-By or any AI attribution.
- Do not push or open a PR.

## Verification
- `uv run pytest -q` passes (offline default suite).
- `uv run pytest -q -m network` result reported separately (one manual run).
- `git log --oneline main..HEAD` shows one commit per task.
- Report `<command>: <observed result>` and RED/GREEN counts per task.

## Return
Concise report: tasks done, commits, test counts, the network run resu
prompt and why.
Close with a `## Key Learnings` block of 1–5 numbered standalone facts
````

### L2 — decisions taken while planning
- L2.1 — L1 arrived truncated at six points: "It must n" (S6), "each leaving the su" (S9), "in Engli"
  (S10), "odd/tasks/patch-ne" (S12), "the real corpu" (S13) and "the network run resu" (Return). Each Specs
  entry that carries one of them is now marked "(interpreted; see L2.1)" and states the completion in full;
  the completion never widens scope.
- L2.2 — The choice of transport lives in a new small outbound adapter (`locator_fetching.py`), not in the
  use case and not in the parser, and the composition root wires it. `IngestSource` keeps receiving one
  `SourceFetcher`, so `ingestion/application` is untouched (S7).
- L2.3 — The polite fetcher is parameterised by a closed `HttpSourcePolicy` with poe2db as the default, so
  the existing adapter keeps its behaviour and messages while the forum gets its own closed rule set. This
  is what makes S2's "do not fetch arbitrary hosts" structural rather than a comment.
- L2.4 — `captured_at` needs no change in the parser: it already stores `raw.fetched_at.date()`, and the
  HTTP fetcher's `fetched_at` is the fetch moment (or the original capture moment on a cache hit).
- L2.5 — `--refresh` reaches the patch-notes runner through the composition root, not through a second
  fetch implementation: the cache policy is a property of the fetcher, so one instance cannot serve both.
- L2.6 — Chunk-id parity (S4) is already a property of the parser (`chunk_id` carries source type, entity
  key, version and section, never the capture date), so it is pinned by a test instead of added as
  behaviour.

### L3 — decisions taken after the gate reviews
- L3.1 — Allowed edit surfaces grow by three entries, each required by a round-1 finding:
  `tests/network/**` because this repository keeps network tests there (`test_poe2db_network.py` fixes the
  command, and hiding one under `tests/integration/` would break the convention T5 must follow),
  `pyproject.toml` because the `network` marker description must stay true once the marker also covers
  `www.pathofexile.com`, and `tests/cli_fakes.py` (see L3.8) because the CLI container protocol changes
  shape and every fake that satisfies it lives there.
- L3.2 — The `--refresh` usage error is narrowed, not removed: a patch-notes *URL* accepts it, a patch-notes
  *path* still exits 2, because a local snapshot has no cache to bypass and accepting the flag there would
  promise something the run does not do. The message names both supported cases.
- L3.3 — The container stops exposing per-source refresh methods. One `ingest_source(*, source, refresh)`
  keeps `CliContainer` honest for every source (and removes the per-command `cast`), while the two fetchers
  per cache policy still live in the composition root, which is the only place that knows the policy. The
  removal covers the class as well as the protocol: `poe2db_ingest_source` stops being public, the
  per-policy instance caching lives in private builders behind the one method, and the seven
  `test_bootstrap.py` call sites move with it, so no two entry points can disagree about which fetcher a
  policy gets.
- L3.4 — The URL/local parity test goes through the dispatcher with the fake HTTP transport instead of a
  hand-built `RawDocument`: the hand-built document asserts the parser against itself and cannot catch the
  drift S4 measures (`source.type`, `source.url`).
- L3.5 — The `source_type` the forum policy declares is the parser's exported `SOURCE_TYPE`, so there is one
  literal for the kind and a test pins the policy to the parser's accepted value. No domain or application
  module changes (S7).
- L3.6 — `cache_directory` is one validated segment appended to the constructor's `cache_dir`, not the
  final directory: a final-directory policy paired with `bootstrap.py:341`'s `raw_dir` would migrate every
  poe2db capture out of `data/raw/poe2db/` and orphan the files the citations came from, and it would change
  a constructor contract four existing tests pin. The segment validation (non-blank, no separator, not
  `.`/`..`) is what keeps a policy from escaping the cache root.
- L3.7 — The locator classifier is three-way (`HTTP`, `OTHER_SCHEME`, `PATH`) and is the only implementation:
  the dispatcher and the `--refresh` guard both call it. A boolean `is_http_locator` was rejected because
  `ftp://x` must fail as a locator error with and without the flag, and a boolean would force the guard to
  grow a second predicate — the same divergence in the other direction. `OTHER_SCHEME`, not `URL`: the member
  holds exactly the locators the feature rejects, so a call site reading `is OTHER_SCHEME` says what it means.
- L3.8 — `Container.ingest_source(*, source, refresh)` is the single source-aware entry point and fails
  closed: an unknown name raises `ValueError(f"unknown source: {source!r}")` like
  `ingest.py:88-90`, and a parametrized test over `ingest_cli.SOURCES` × both refresh values keeps the
  composition root and the CLI's `choices` from drifting. The class's `poe2db_ingest_source` stops being
  public in the same move (see L3.3), and `tests/cli_fakes.py` joins the surfaces because that protocol's
  fakes live there.
- L3.9 — `Poe2dbCacheError` is renamed to `HttpCacheError` with no alias: the previous plan justified the
  alias with `EXPECTED_ERRORS`, which lists the base `HttpFetchError` and never this subclass.
- L3.10 — **Superseded by L3.12** for the field list (the repo used the same convention for D1 in
  `odd/tasks/poe2db-source.md`). The policy declares `HttpSourcePolicy(host, path, source_type,
  cache_directory)` and `PathRule(prefix, slug: re.Pattern[str], slug_message)`, consumed by one shared
  ordered validator; `scheme` and `cache_noun` were dropped in L3.12. Host, prefix, slug and cache noun
  derive from the policy; scheme, port, userinfo, query, fragment and trailing slash stay literal because
  they are host-independent.
- L3.11 — Only the patch-notes runner is wired with `LocatorFetcher`; the poe2db pair keeps
  `PoliteHttpFetcher` directly, so `ingest poe2db <path>` keeps failing in the fetcher with its one-line
  locator error instead of reading a file and failing later inside the gem parser.
- L3.12 — One string per concern in the policy: the scheme is fixed to https (no field and no derived
  message), `cache_directory` is the single cache name and the message noun is derived from it, and
  `source_type` comes from the parser's export. Two spellings of one name would drift exactly the way S4
  measures.
- L3.13 — The refactor of a working adapter starts with the message assertions it can silently change:
  host, prefix, trailing slash and slug are pinned for poe2db (and for the forum) before the shared
  validator lands, because today only the trailing-slash substring is asserted and the other three are
  user-visible strings with no test behind them.

### L4 — decisions taken while implementing
- L4.1 — `LocatorFetcher` takes its HTTP branch as a zero-arg factory (`LocatorFetcher(*, local, http)`)
  instead of two ready fetchers. The eager form the plan assumed in T3 was proved wrong by a test:
  `PoliteHttpFetcher` refuses to build without `POE2_RAG_HTTP_CONTACT`, so building the patch-notes
  runner eagerly would make `ingest patch-notes <path>` fail on a machine with no contact. That breaks S1
  ("local paths behave exactly as today") and `test_a_missing_contact_fails_when_the_poe2db_runner_is_asked_for`,
  which asserts patch-notes keeps working without one. The factory is built on the first URL and memoized,
  so one runner still owns one fetcher (robots cache and throttle included) and the nesting is unchanged:
  `LocatorFetcher` on top of `_RawDirectoryFetcher` and `PoliteHttpFetcher`.
