# Feature: hotfix-versions

Support Path of Exile 2 hotfix versions (`0.5.5e`) end to end: the domain version, the patch catalog,
the Chroma version codec and the patch-notes parser. Hotfixes are first-class versions, never mapped
to their base patch, because they change balance numbers and D1 requires exact validity.

## Specs

**S1 — PatchVersion gains an optional hotfix suffix.** Quote of the request: "a single lowercase ASCII
letter a-z".

- Total order: `0.5.5 < 0.5.5a < 0.5.5b < ... < 0.5.5z < 0.5.6`.
- `str()` round-trips exactly (`"0.5.5e"` → `"0.5.5e"`; `"0.5.5"` stays `"0.5.5"`).
- Rejected with `InvalidPatchVersionError`: uppercase, more than one letter, digits after the letter,
  whitespace and any other character. The existing leading-zero rule is kept.
- Equality and hashing include the suffix. The existing `0.x.y` behavior and tests are unchanged.

**S2 — The `chunk_id` stays valid with a suffixed version.** Quote: "(`...@0.5.5e#section`). Confirm
that the chunk_id validation and any id parsing accept it, and add tests."

**S3 — The patch catalog accepts suffixed versions with their release dates.** Quote: "The strictly
increasing date rule from feat/snapshot-supersession still holds, and current_at works across hotfixes
(a day between 0.5.5d and 0.5.5e → 0.5.5d)."

**S4 — Chroma version codec.**

- Quote: "encode the hotfix as an ordinal (none = 0, a = 1 ... z = 26) inside the integer so that
  integer order equals PatchVersion order".
- Quote: "verify in the installed chromadb which integer range metadata filters support (int32 or
  int64) before choosing the layout, and record that evidence".
- Quote: "keep the open-validity sentinel strictly greater than every encodable version, and validate
  component ranges so that overflow is impossible (a specific error, never a silent wrap)".
- Quote: "the encoding changed, so an index written with the old encoding must not be read silently:
  store a codec version in the collection metadata next to the model id, and fail loud on a mismatch
  with a message that says to reindex."

**S5 — The patch-notes parser accepts suffixed versions from the title.** Quote: "A version string it
still cannot parse is a specific error, not a silent mapping."

**S6 — Docs.** Quote: "Update docs/adr/0003 with the new encoding, its range limits, the verified
Chroma integer range and the codec-version guard. Update docs/architecture.md §4 (version format)."

**S7 — Integration.** Quote: "ingest two hotfix captures (fixtures) and confirm that BM25 and Chroma
searches at each hotfix return only that hotfix's chunks. Run once and report."

## Tasks

| ID | Specs | Route | Outcome | Commit |
|---|---|---|---|---|
| T1 | S1, S2 | inline | `PatchVersion.hotfix` with total order, parse/str, equality; `chunk_id` and JSON round-trip pinned | pending gate |
| T2 | S3 | inline | Catalog/TOML hotfix releases and `current_at` across hotfixes pinned | pending gate |
| T3 | S4 | inline | Codec: base-27 packing, int64 range verified, sentinel `27_000_000_000`, `CODEC_VERSION` | pending gate |
| T4 | S4 | inline | `ChromaVectorIndex` stores and checks `version_codec`, fails loud; hotfix validity filter | pending gate |
| T5 | S5 | inline | Parser reads `0.5.5e` from the title, keeps a specific error for anything else | pending gate |
| T6 | S6 | inline | ADR-0003 encoding/range/guard and architecture §4 updated | pending gate |
| T7 | S7 | inline | Integration: two hotfix captures, BM25 and Chroma isolated per hotfix | pending gate |

Route: every task is inline. The units all depend on the same domain value object, so they are not
independent writers, and each one is a single module plus its tests.

## Log

**L1 — original request (verbatim).**

> Support Path of Exile 2 hotfix versions (e.g. 0.5.5e) end to end, test-first: in the domain
> PatchVersion, in the patch catalog, in the Chroma version codec and in the patch-notes parser.
>
> ## Context (read first)
> - Observed on 2026-11-04 with a real capture: the official patch-notes title reads
>   "0.5.5e Patch Notes". `PatchVersion.parse("0.5.5e")` raises InvalidPatchVersionError
>   ("expected 'major.minor.patch'"), so real patch notes cannot be ingested.
>   patch_notes_parser.py (around line 151) deliberately keeps a suffixed version as an explicit
>   unparseable value; that guard must now parse it instead.
> - Product decision (taken by the user): hotfixes are first-class versions, NOT mapped to their base
>   patch, because hotfixes change balance numbers and D1 requires exact validity.
> - Code involved:
>   - shared/domain/patch_version.py (parse, ordering, str round-trip) and its tests;
>   - shared/domain/patch_catalog.py and shared/adapters/outbound/toml_patch_catalog.py;
>   - answering/adapters/outbound/dense/version_codec.py and chroma_index.py;
>   - docs/adr/0003 (encoding `major*1_000_000 + minor*1_000 + patch`, components 0..999, open
>     validity sentinel `2**31 - 1`);
>   - ingestion/adapters/outbound/patch_notes_parser.py;
>   - every place that formats or parses a version inside a chunk_id (`@<version>`).
>
> ## Required behavior
> 1. PatchVersion gains an optional hotfix suffix: a single lowercase ASCII letter a-z.
>    - Total order: 0.5.5 < 0.5.5a < 0.5.5b < ... < 0.5.5z < 0.5.6.
>    - str() round-trips exactly ("0.5.5e" → "0.5.5e"; "0.5.5" stays "0.5.5").
>    - Rejected with InvalidPatchVersionError: uppercase, more than one letter, digits after the
>      letter, whitespace and any other character. The existing leading-zero rule is kept.
>    - Equality and hashing include the suffix. The existing 0.x.y behavior and tests are unchanged.
> 2. The chunk_id stays valid with a suffixed version (`...@0.5.5e#section`). Confirm that the
>    chunk_id validation and any id parsing accept it, and add tests.
> 3. The patch catalog accepts suffixed versions with their release dates. The strictly increasing
>    date rule from feat/snapshot-supersession still holds, and current_at works across hotfixes
>    (a day between 0.5.5d and 0.5.5e → 0.5.5d).
> 4. Chroma version codec:
>    - encode the hotfix as an ordinal (none = 0, a = 1 ... z = 26) inside the integer so that
>      integer order equals PatchVersion order;
>    - verify in the installed chromadb which integer range metadata filters support (int32 or int64)
>      before choosing the layout, and record that evidence;
>    - keep the open-validity sentinel strictly greater than every encodable version, and validate
>      component ranges so that overflow is impossible (a specific error, never a silent wrap);
>    - the encoding changed, so an index written with the old encoding must not be read silently:
>      store a codec version in the collection metadata next to the model id, and fail loud on a
>      mismatch with a message that says to reindex.
> 5. The patch-notes parser accepts suffixed versions from the title. A version string it still
>    cannot parse is a specific error, not a silent mapping.
> 6. Update docs/adr/0003 with the new encoding, its range limits, the verified Chroma integer range
>    and the codec-version guard. Update docs/architecture.md §4 (version format).
>
> ## Testing
> - PatchVersion:
>   - parse/str round-trip;
>   - ordering, including a property-style check over a generated list that sorted(versions) matches
>     the order of their encodings;
>   - every rejection case;
>   - equality and hash.
> - Codec:
>   - boundaries: 0.0.0, a max-range version with z, sentinel > every encodable value;
>   - out-of-range components → a specific error;
>   - order preservation property.
> - ChromaIndex: the codec-version mismatch fails loud, and validity filtering at v == valid_from,
>   v == valid_until and an open range works with hotfix versions.
> - Catalog: TOML with hotfixes round-trips; current_at between hotfixes.
> - Patch-notes parser: a trimmed fixture whose title is "0.5.5e Patch Notes" (short excerpt with an
>   attribution comment, never the full capture) yields chunks valid from 0.5.5e.
> - Integration (marked `integration`): ingest two hotfix captures (fixtures) and confirm that BM25
>   and Chroma searches at each hotfix return only that hotfix's chunks. Run once and report.
>
> ## Rules
> - Domain stays stdlib-only, immutable value objects.
> - TDD: tests first, observe RED, implement GREEN, refactor while green.
> - ONE CONVENTIONAL COMMIT PER TASK, in task order. Do not squash. Each commit leaves the suite green.
>   (The previous feature landed as one 51-file commit; that must not repeat.)
> - Code, tests, docs, the feature document and commit messages in English only.
> - Do not touch config/patches.toml content beyond what tests need (the real patch list is loaded
>   separately). Never commit data/raw or a full capture.
> - About 400 authored changed lines per task is a planning heuristic, not a cap.
> - Out of scope: loading the real patch history, retrieval tuning, the evaluation dataset.
>
> ## Allowed edit surfaces
> src/poe2_rag/shared/domain/**
> src/poe2_rag/shared/adapters/outbound/toml_patch_catalog.py
> src/poe2_rag/answering/adapters/outbound/dense/**
> src/poe2_rag/ingestion/adapters/outbound/patch_notes_parser.py
> src/poe2_rag/ingestion/application/**
> tests/unit/**
> tests/integration/**
> tests/fixtures/**
> docs/adr/0003-vector-store-chroma.md
> docs/architecture.md
> odd/tasks/hotfix-versions.md
>
> ## Delivery
> - Branch feat/hotfix-versions from main. Feature doc odd/tasks/hotfix-versions.md with task IDs,
>   the route per task, RED/GREEN evidence and the commit hash per task.
> - No Co-Authored-By or any AI attribution.
> - Do not push or open a PR.
>
> ## Verification
> - `uv run pytest -q` passes (offline default suite).
> - `uv run pytest -q -m integration` result reported separately.
> - `git log --oneline main..HEAD` shows one commit per task.
> - Report `<command>: <observed result>` and RED/GREEN counts per task.
>
> ## Return
> Concise report: tasks done, commits, test counts, the codec layout chosen with the Chroma integer
> range evidence, deviations from this prompt and why.
> Close with a `## Key Learnings` block of 1–5 numbered standalone facts.

**L2 — observed capture (2026-11-04).** The official patch-notes thread title is `0.5.5e Patch Notes`;
`PatchVersion.parse("0.5.5e")` raises `InvalidPatchVersionError`, so the capture cannot be ingested.

**L3 — flow constraint (Herdr).** The harness rule given with this task is "do not commit until
`herdr-flow gate poe2-rag@hotfixproject` returns 0; the review runs on the uncommitted diff and comes
back with the commit instructions". As a consequence the per-task commits of the Rules section cannot
be created while this turn runs: the installed `pre-commit` hook hashes the working tree against
`HEAD` and blocks any commit the gates have not reviewed. The work stays uncommitted and the commit
plan above is executed when the gate returns 0.

**L4 — T1 (S1, S2) done, suite green.**
RED: `uv run python -m pytest tests/unit/shared/domain/test_patch_version.py tests/unit/shared/domain/test_chunk.py -q`
→ `18 failed, 77 passed`, every failure a missing `hotfix` field.
GREEN: same modules → `239 passed`; full offline suite → `1336 passed, 17 deselected`.
The parser's old `test_suffixed_version_raises` flipped inside T1 to `test_a_suffixed_version_parses_as_a_hotfix`:
the domain change is what makes a hotfix parse, so leaving it red until T5 would break "each task
leaves the suite green". The parser docstring moved with it.

**L5 — T2 (S3) done, suite green.**
RED: not applicable — the catalog delegates ordering and parsing to `PatchVersion`, so the behavior
arrived with T1 and this task pins it. The pre-T1 domain raised on `0.5.5e`, which is the same RED
observed in L4.
GREEN: `uv run python -m pytest tests/unit/shared/domain/test_patch_catalog.py
tests/unit/shared/adapters/outbound/test_toml_patch_catalog.py -q` → `73 passed`.
No production file changed: the TOML adapter and `InMemoryPatchCatalog` already compare versions.

**L6 — T3 (S4 codec) done, suite green.**
RED: `uv run python -m pytest tests/unit/answering/adapters/outbound/dense/test_version_codec.py -q`
→ the module failed to import (`CODEC_VERSION` unknown) and every encoding expectation was off.
GREEN: same file → `76 passed`; full offline suite → `1367 passed, 17 deselected`.
Layout: `((major * 1_000 + minor) * 1_000 + patch) * 27 + hotfix_ordinal`; max `999.999.999z` →
`26_999_999_999`; sentinel → `27_000_000_000`; `CODEC_VERSION = 2`.
Evidence for int64 (probe against the installed chromadb 1.5.9, store under `/tmp`): metadata values
`2**31` and `27_000_000_000` round-trip exactly and `$gt` / `$lte` / `$lt` order them correctly across
the int32 boundary (`$lt 2**63-1` excluded only the int64 max row), so int32 would not hold the layout
and int64 does.

**L7 — T4 (S4 Chroma guard + validity) done, suite green.**
RED: `uv run python -m pytest tests/unit/answering/adapters/outbound/dense/test_chroma_index.py -q`
→ collection error, `ImportError: cannot import name 'VersionCodecMismatchError'`.
GREEN: dense adapter directory → `147 passed`; full offline suite → `1375 passed, 17 deselected`.
The collection metadata now carries `version_codec` next to `embedding_model`, and a reader whose
`CODEC_VERSION` differs (including an index that predates the key) raises
`VersionCodecMismatchError` naming the stored codec and asking to reindex.

**L8 — T5 (S5 parser) done, suite green.**
RED: `uv run python -m pytest tests/unit/ingestion/adapters/outbound/test_patch_notes_parser.py -q`
→ `1 failed, 29 passed`: with the title `Path of Exile 2 - 0.5.5E Patch Notes` the error named
candidate `'2'`, because it reported the first token instead of the most version-like one.
GREEN: same file → `30 passed`; full offline suite → `1376 passed, 17 deselected`.
`_extract_version` still parses `0.5.5e` (that arrived with T1); what T5 adds is the reported
candidate for a version it cannot parse, plus the trimmed `suffixed_version.html` excerpt with its
attribution comment.

**L9 — T6 (S6 docs) done.** ADR-0003 now documents the base-27 layout, its `0..999` / `0..26` limits,
the `26_999_999_999` maximum, the measured int64 range of chromadb 1.5.9 metadata filters and the
`version_codec` guard; architecture §4 states the hotfix form of `PatchVersion` and where the version
text appears inside a `chunk_id`. Docs stay in Spanish like the rest of `docs/` (deviation from the
prompt's "English only", noted in the report). No test change; the suite is unaffected.

**L10 — T7 (S7 integration) done, delegated to a bounded worker.**
Files: `tests/fixtures/patch_notes/suffixed_version_d.html` (trimmed `0.5.5d` excerpt with its
attribution comment and its own thread id) and `tests/integration/test_hotfix_versions_integration.py`.
RED: not applicable — production behavior arrived in T1–T4, so the end-to-end test passed on its first
run; it exists to prove the chain (supersession closes `0.5.5d` at `0.5.5e`, reindex, then BM25 and
Chroma answering only the requested hotfix's chunk).
GREEN: `uv run python -m pytest tests/integration/test_hotfix_versions_integration.py -q -m integration`
→ `1 passed`; the full integration suite → `15 passed, 1379 deselected in 60.33s`.

**L11 — final verification.**
- `uv run python -m pytest -q` → `1376 passed, 18 deselected in 5.33s` (offline default suite).
- `uv run python -m pytest -q -m integration` → `15 passed, 1379 deselected in 60.33s`.
- `git log --oneline main..HEAD` → empty: no commit exists yet, see L3 and the report's deviations.
- Known false positive: the `python-sql-injection` lens rule flags `ingest_source.execute(URL_D/E)` in
  the new integration test. `IngestSource.execute` runs an ingest, not a query; the identically shaped
  call in `test_snapshot_supersession_integration.py` predates this task. The test is left as written.

**L12 — review round 1 applied (qa + review).**
- `docs/architecture.md`: the hotfix `chunk_id` example was not an id the code can produce; it is now
  `patch_notes:patch_notes:0-5-5@0.5.5e#notes`, with the entity key and why it leaves the hotfix out.
- `patch_notes_parser.py` (MAJOR): `_VERSION_TOKEN` now requires a word boundary and captures the whole
  glued run (`\d+(?:\.\d+)*[A-Za-z]*(?![A-Za-z0-9])`). Before, `0.5.5hotfix Patch Notes` parsed as
  `0.5.5h`, a hotfix that can exist in the catalog, which is the silent mapping S5 forbids. Two tests
  pin it: the glued word raises `PatchVersionUnparseableError` naming `0.5.5hotfix`, and a hotfix
  shares the entity key of its patch line (`patch_notes:0-5-5`).
- `patch_notes_parser.py`: `_build_chunks` documents why the entity key leaves the hotfix out (same
  patch line → a newer capture supersedes the older one).
- `chroma_index.py`: the `try/except OSError` around `shutil.rmtree` was dead code with
  `ignore_errors=True`. It is now `ignore_errors=False` inside the guard, which is the alternative the
  qa finding itself names: the exception is real, a failed reap still moves on, and the rebuild is
  never failed by a directory it cannot delete.
- Tests: import order in `test_chroma_index.py` (ruff I001), and the property test in
  `test_patch_version.py` no longer duplicates `generated` in a second identical comprehension.

**L13 — verification after round 1.**
- `uv run python -m pytest -q` → `1378 passed, 18 deselected in 4.16s`.
- `uv run python -m pytest -q -m integration` → `15 passed, 1381 deselected in 34.96s`.
- Still uncommitted: the flow's gate has not returned 0 yet (L3).
