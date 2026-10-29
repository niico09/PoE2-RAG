# Feature: patch-catalog

## Objective
Give the system one authoritative answer to "which patches exist and which one is current",
behind a domain port with an in-memory fake and a TOML adapter, proved by a shared contract suite.

## Problem / Why
`Validity` already knows *when* a chunk applies, but nothing knows *which* patch is current.
Architecture §4.2 makes `PatchCatalog` the single source of truth for "current" so no chunk has
to store a derived `is_current_patch` flag (driver D1). The MVP loads it from a versioned
configuration file (§6), which is an adapter concern, never a domain one.

## Specs
- **S1** — Port `PatchCatalog` (`typing.Protocol`) in `src/poe2_rag/shared/domain/patch_catalog.py`:
  `current() -> PatchVersion`, `known() -> tuple[PatchVersion, ...]` (ascending, no duplicates),
  `contains(version: PatchVersion) -> bool`.
- **S2** — `InMemoryPatchCatalog(versions, current)` in the same module, used as the test fake and
  by future adapters. Invariants: non-empty, no duplicates, `current` must be one of the known
  versions. Violations raise the new `InvalidPatchCatalogError(DomainError)`.
- **S3** — Outbound adapter `TomlPatchCatalog` in
  `src/poe2_rag/shared/adapters/outbound/toml_patch_catalog.py`, loading a versioned file with
  stdlib `tomllib`:

  ```toml
  current = "0.1.2"
  [[patches]]
  version = "0.1.0"
  released_on = 2024-12-06
  ```

  Wrap I/O and parse errors in a specific error; never return a partial catalog.
  Add a sample file at `config/patches.toml`.
- **S4** — `Validity.is_valid_at` and `Chunk.is_valid_at` raise `InvalidValidityError` when given a
  non-`PatchVersion` argument, instead of an opaque `TypeError`.
- **S5** — Domain imports only the standard library. Only the adapter touches the filesystem.
- **S6** — TDD: tests first, observe RED, implement GREEN, refactor while green. Contract tests:
  one shared suite that runs against both `InMemoryPatchCatalog` and `TomlPatchCatalog`
  (`tests/contract/test_patch_catalog_contract.py`) to prove both honour the port.
- **S7** — Branch `feat/patch-catalog` from `main`. Feature doc `odd/tasks/patch-catalog.md`.
  One Conventional Commit per task. No `Co-Authored-By` or AI attribution.
- **S8** — `uv run pytest -q` passes. Report the RED/GREEN counts per task.

## Non-goals
- Integer version encoding (ADR-0003, an adapter detail), Chroma, CLI, ingestion.

## Tasks
- [x] T1 — Branch `feat/patch-catalog` from `main`, feature doc, Engram mirror. Route: inline. Commit: `docs(odd): record patch-catalog plan` (`b543588`).
- [x] T2 — `InvalidPatchCatalogError` + `PatchCatalog` port + `InMemoryPatchCatalog` with unit tests. Route: inline. Commit: `feat(domain): add PatchCatalog port and in-memory catalog` (`3eceef3`).
- [x] T3 — Shared contract suite + `TomlPatchCatalog` adapter + `config/patches.toml` sample, with adapter unit tests. Route: inline. Commit: `feat(adapters): load patch catalog from a TOML file` (`eded729`).
- [x] T4 — S4: `Validity.is_valid_at` and `Chunk.is_valid_at` reject non-`PatchVersion` args. Route: inline. Commit: `fix(domain): reject non-PatchVersion in is_valid_at` (`f3eaf70`).
- [x] T5 — Close: full suite, evidence per task, RED/GREEN report. Route: inline. Commit: `docs(odd): close patch-catalog feature` (`afbedcc`).
- [x] T6 — Round 1: move the Log to the end of this document and record the QA/review findings. Commit: `docs(odd): move the patch-catalog log last and record round 1`.
- [x] T7 — Round 1: adapter input hardening (invalid UTF-8, TOML datetime as `released_on`, file path in invariant errors, load-error hierarchy pinned). Commit: `fix(adapters): harden patch catalog file loading`.
- [x] T8 — Round 1: declare `contains` rejection and the error triggers in the port and error docstrings, and pin `contains` in the contract suite. Commit: `docs(domain): declare the PatchCatalog failure modes`.
- [x] T9 — Round 1: align `docs/architecture.md` §7 and the README tree with `shared/adapters`, resolve the `0.1.2` example contradiction, refresh the README status line, and record how `config/patches.toml` is resolved. Commit: `docs: align structure and sample versions with the implementation`.

## Acceptance criteria
- Every port method behaves identically in both implementations, asserted by one contract suite.
- No implementation can return a partial or invariant-violating catalog.
- Boundary behaviour of the port: unknown version is not contained; single-patch catalog is valid.
- `uv run pytest -q` green; each task's RED and GREEN observed and recorded below.

## Checks
- `uv run pytest -q`
- `uvx pyright --pythonpath .venv/bin/python src tests` (the harness runner cannot bind the venv;
  see T3 evidence)

## Progress / Evidence
- T1: baseline `uv run pytest -q` → 121 passed. Branch `feat/patch-catalog` created from `main` at `b8e19af` (the `feat/ingest` worktree was parked; it sat on the same commit). No RED applicable (documentation only). Commit `docs(odd): record patch-catalog plan` (`b543588`).
- T2: RED → collection `ImportError: cannot import name 'InMemoryPatchCatalog' from 'poe2_rag.shared.domain'`, 0 tests run. GREEN → 141 passed (20 new). Two first-run failures were bugs in the test helper (default `current="0.1.1"` absent from a single-version catalog), not in the code: the invariant rejected them as designed, the helper was fixed. `PatchCatalog` is `runtime_checkable` so the contract test can assert conformance; `InMemoryPatchCatalog` is a plain slotted class rather than a dataclass because a frozen dataclass cannot hold a `current` field and a `current()` method at once. It copies and sorts its input, so a later mutation of the caller's list cannot change it. Commit `feat(domain): add PatchCatalog port and in-memory catalog` (`3eceef3`).
- T3: RED → `ModuleNotFoundError: No module named 'poe2_rag.shared.adapters'` for both new suites (collection error, 0 tests run). GREEN → 193 passed (52 new: 32 contract cases — 16 scenarios run against each implementation — and 20 adapter unit tests).
  - The adapter *composes* `InMemoryPatchCatalog` instead of re-checking invariants, so "never a partial catalog" is structural: the only way to build a `TomlPatchCatalog` is to hand validated versions to the domain, and any decode, shape or invariant failure aborts before an instance exists.
  - `PatchCatalogLoadError(RuntimeError)` is deliberately not a `DomainError`: a broken file is an environment failure, and keeping it out of the `ValueError`/`DomainError` branch means callers can never confuse it with an invariant violation. Invariants raised by decoded files stay `InvalidPatchCatalogError`, so the contract suite asserts one error type for both implementations.
  - An absent `patches` key decodes to an empty catalog and is rejected by the invariant ("must not be empty"), not by the loader. That keeps the empty-catalog contract identical in both implementations; without it, the TOML path would need a separate error for the same rule.
  - Unknown keys (top level and per table) are rejected: this file is the single source of truth for "current", so a misspelled key must fail closed instead of being silently ignored.
  - `released_on` is validated as a TOML date but not exposed: the port promises versions, and the dates are for humans reading the config.
  - Sample data uses real patches (0.1.0 = 2024-12-06, 0.1.1 = 2025-01-17, from poe2wiki's version history) and keeps the S3 format exactly. `README.md` gains `config/` in the repository tree and one sentence pointing at the adapter.
  - Tooling note: the harness's Pyright runner reports `reportMissingImports` for `pytest` and `poe2_rag` on these files (and on the untouched `test_validity.py`). `uvx pyright --pythonpath .venv/bin/python` on the same three files reports **0 errors**, and adding a `pyrightconfig.json` did not change the runner's outcome, so it is an interpreter-binding defect in the runner, not in the code. The file was not committed. Commit `feat(adapters): load patch catalog from a TOML file` (`eded729`).
- T4: RED → 8 failed, 193 passed; every failure was `TypeError: '<' not supported between instances of 'str' and 'PatchVersion'` raised at `validity.py:32`, the opaque behaviour S4 replaces. GREEN → 201 passed (8 new: 5 in `test_validity.py`, 3 in `test_chunk.py`).
  - `Chunk.is_valid_at` needed no separate check: it delegates to `Validity.is_valid_at`, so both now raise `InvalidValidityError` with the message `version must be a PatchVersion, got ...`. Verified with `grep` that those are the only two `is_valid_at` sites in `src/`, so no unnamed consumer exists and no differential check applies; the pre-existing valid-argument tests in both files stayed green, which is the regression guard for the shared path. Commit `fix(domain): reject non-PatchVersion in is_valid_at` (`f3eaf70`).
- T5: closed the first round. Commit `docs(odd): close patch-catalog feature` (`afbedcc`).
- T6: documentation only, no RED applicable. L1 is now quoted inside a four-backtick fence, so the headings of the original request no longer break the document structure, and L1/L2/L3 sit together in the final `## Log`. Commits `docs(odd): move the patch-catalog log last and record round 1`.
- T7: RED → 4 failures out of 205, all in `TestLoadErrors`/`TestCatalogInvariants`: invalid UTF-8 surfaced as `builtins.UnicodeDecodeError`, a TOML `datetime` was accepted as `released_on`, and the two invariant messages carried no file path. GREEN → 205 passed (4 new, 1 updated). Each finding was reproduced against the shipped adapter before the fix, not taken on trust. Commit `fix(adapters): harden patch catalog file loading`.
- T8: no RED applicable — both implementations already rejected a non-`PatchVersion` in `contains`, so the added contract scenario and the docstrings pin shipped behaviour instead of driving new behaviour. GREEN → 207 passed (2 new contract cases: one scenario × two implementations). Commit `docs(domain): declare the PatchCatalog failure modes`.
- T9: documentation and configuration only. `uv build --wheel` confirms the wheel carries `poe2_rag/shared/adapters/outbound/toml_patch_catalog.py` and no `config/`, which is why the config-path decision is now recorded in the adapter docstring: the TOML file is a repository artifact for operators, not package data. Commit `docs: align structure and sample versions with the implementation`.

## Delivery slices
- Slice 1 (`b543588`..`eded729`, 702 authored lines): T1-T3. Over the ~400-line review slice on its own, and kept as one slice because the user's delivery rule is one commit per task and the chain is a single dependent feature (port → adapter → contract). Whether to split it into chained PRs is the user's decision.
- Slice 2 (T4): the S4 hardening, 19 lines, small enough to review alone.
- Slice 3 (T6-T9): the round-1 findings, split into four commits by subject (process doc, adapter correctness, domain contract, repository docs).

## Close
- Final `uv run pytest -q` → 207 passed (baseline before the feature: 121).
- `uvx pyright --pythonpath .venv/bin/python src tests` → 0 errors.
- S5 checked by reading the imports: `domain/patch_catalog.py` imports only `collections.abc`, `itertools`, `typing` and sibling domain modules; `tomllib`, `pathlib` and `datetime` appear only in the adapter.
- S7 checked: `git log main..HEAD --format=%B | grep -ci 'co-authored-by\|generated with\|claude'` → 0.
- No check was skipped; nothing is pending.

## Next step
Push and open the PR: the user's decision, not taken here. The flow channel still reads `poe2-rag@feat/ingest`
while this worktree hosts `feat/patch-catalog` (see L3, finding 11): renaming the channel is a Herdr
configuration change only the human can make.

## Log

### L1 — original request (verbatim)

````text
Implement the PatchCatalog port and its first adapter in PoE2-RAG, test-first.

## Context (read first)
- docs/architecture.md §4.2 (PatchCatalog: single source of truth for "current patch"),
  §6 (ports), §7 (layout), §8 (testing).
- docs/adr/0003-vector-store-chroma.md (integer version encoding is an adapter detail; not part of this task).
- Existing domain: src/poe2_rag/shared/domain/ (PatchVersion, Validity, DomainError hierarchy, _text helpers).
  Match its style: frozen slotted dataclasses, specific DomainError subclasses, short English docstrings.

## Goal
Give the system one authoritative answer to "which patches exist and which one is current".

## Scope
1. Port `PatchCatalog` (typing.Protocol) in src/poe2_rag/shared/domain/patch_catalog.py:
   - `current() -> PatchVersion`
   - `known() -> tuple[PatchVersion, ...]` (ascending, no duplicates)
   - `contains(version: PatchVersion) -> bool`
2. In-memory implementation `InMemoryPatchCatalog(versions, current)` in the same module, used as the test fake
   and by future adapters. Invariants: non-empty, no duplicates, `current` must be one of the known versions.
   Raise a new `InvalidPatchCatalogError(DomainError)`.
3. Outbound adapter `TomlPatchCatalog` in src/poe2_rag/shared/adapters/outbound/toml_patch_catalog.py,
   loading a versioned file with stdlib `tomllib`:
       current = "0.1.2"
       [[patches]]
       version = "0.1.0"
       released_on = 2024-12-06
   Wrap I/O and parse errors in a specific error; never return a partial catalog.
   Add a sample file at config/patches.toml.
4. Deferred review suggestion (in scope here): `Validity.is_valid_at` and `Chunk.is_valid_at` raise
   `InvalidValidityError` when given a non-PatchVersion argument, instead of an opaque TypeError.

## Rules
- Domain imports only the standard library. Only the adapter touches the filesystem.
- TDD: write tests first, observe RED, implement GREEN, refactor while green.
- Contract tests: one shared test suite that runs against both InMemoryPatchCatalog and TomlPatchCatalog
  (tests/contract/test_patch_catalog_contract.py) to prove both honour the port.
- Out of scope: integer encoding, Chroma, CLI, ingestion.

## Delivery
- Branch feat/patch-catalog from main. Feature doc odd/tasks/patch-catalog.md.
- One Conventional Commit per task. No Co-Authored-By or AI attribution.

## Verification
- `uv run pytest -q` passes. Report the RED/GREEN counts per task.
````

### L2 — decisions taken while planning
- Baseline: branch `feat/patch-catalog` created from `main` at `b8e19af`; `uv run pytest -q` → 121 passed.
- S3 sample data: the snippet's `current = "0.1.2"` is illustrative (the architecture doc uses it in
  examples), but poe2wiki's version history has no 0.1.2: the real sequence is 0.1.0 (2024-12-06),
  0.1.1 (2025-01-17), then 0.2.0. `config/patches.toml` keeps the exact S3 *format* and uses only
  verifiable entries (`current = "0.1.1"`), so the shipped config never claims a patch that does not exist.
- Error boundary: `InvalidPatchCatalogError(DomainError)` covers catalog invariants in every
  implementation (so the contract suite can assert one error type); `PatchCatalogLoadError(RuntimeError)`
  in the adapter module covers unreadable/undecodable files. The adapter validates through
  `InMemoryPatchCatalog`, which is what makes "never a partial catalog" structural rather than careful.
- `contains` rejects a non-`PatchVersion` argument with `InvalidPatchCatalogError`: a silent `False`
  for a string is the same opaque failure S4 is fixing in `is_valid_at`.
- Route: inline. The five tasks form one dependency chain (port → adapter → contract → hardening), so
  the Writer rule's "2+ independent units" does not hold.

### L3 — round 1 QA/review findings and how each was answered
Every finding was reproduced against the shipped code before changing anything.

1. QA MEDIO — `UnicodeDecodeError` escaped `_load` instead of becoming `PatchCatalogLoadError`.
   Reproduced (`builtins.UnicodeDecodeError` for a latin-1 byte). Fixed: `_load` catches it and reports
   `"<path> is not valid UTF-8: ..."`, so the class stays outside the `ValueError`/`DomainError` branch.
   Pinned by a raw-bytes test.
2. QA BAJO — `released_on = 2024-12-06T00:00:00` was accepted because `datetime` is a subclass of `date`.
   Reproduced. Fixed with `type(released_on) is date` and pinned by a TOML-datetime test.
3. QA MEDIO-BAJO — `shared/adapters/outbound/` was not described by `docs/architecture.md` §7 nor the
   README tree. Answered by documenting it rather than moving the adapter: `PatchCatalog` is consumed by
   both flows (§3 diagram, §6), so its adapter is shared. §7 and the README now state which ports are
   shared and where their adapters live.
4. QA BAJO — README and §4.3 example chunks claimed `0.1.2` validity while the config declares `current = "0.1.1"`.
   Answered by aligning both examples to `0.1.1` and stating that the config is authoritative.
5. QA BAJO — invariant errors from the adapter lost the file path. Reproduced. Answered by re-raising
   `InvalidPatchCatalogError(f"{path}: {exc}") from exc` in `_build`, so the type the contract asserts is
   preserved, the path is always present, and the cause chain survives.
6. QA BAJO (future risk) — `config/patches.toml` is not shipped inside the wheel. Confirmed with
   `uv build --wheel`. Answered as a recorded decision instead of new machinery: the file is a repository
   artifact edited by operators (add a patch without releasing code), the adapter takes the path, and
   `bootstrap.py` owns how that path is resolved. Recorded in the adapter module docstring.
7. QA MINOR — the Log was split and not last, because L1's own headings promoted the quoted request into
   document structure. Answered by fencing L1 in four backticks and making `## Log` the last section.
8. Review MEDIO-BAJO — `contains` rejecting a non-`PatchVersion` was implemented and shared but absent
   from the port contract, so a third adapter could return `False` and pass. Answered by declaring the
   failure mode in the port docstring and adding the scenario to the shared contract suite (both
   implementations), which closes the silent-divergence hole the `isinstance` check alone left open.
9. Review BAJO — error docstrings did not name the triggers this feature added. Answered in
   `errors.py`, `validity.py` and `chunk.py`: each now states what raises it and when.
10. Review BAJO — README status line said "todavía no hay código implementado". Answered by replacing it
    with the current state (domain and patch catalog implemented, 207 tests).
11. Review MEDIO (process) — this worktree is on `feat/patch-catalog` while the flow channel is
    `poe2-rag@feat/ingest`, and `feat/ingest` has no worktree. Not answerable from inside the worktree:
    moving the branch out mid-round would leave the round reviewing an empty diff, and renaming the
    channel is Herdr configuration. `feat/ingest` is untouched at `b8e19af` (identical to `main`, no work
    lost). Recorded here and in "Next step" as a human action: rename the channel to `feat/patch-catalog`,
    or move this work into its own worktree before the next round starts.
12. Review BAJO — the `PatchCatalogLoadError` hierarchy was the decision nobody tested. Answered by
    pinning `not issubclass(PatchCatalogLoadError, DomainError)` in the adapter tests.
