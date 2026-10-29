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
- [x] T1 — Branch `feat/patch-catalog` from `main`, feature doc, Engram mirror. Route: inline. Commit: `docs(odd): record patch-catalog plan`.
- [x] T2 — `InvalidPatchCatalogError` + `PatchCatalog` port + `InMemoryPatchCatalog` with unit tests. Route: inline. Commit: `feat(domain): add PatchCatalog port and in-memory catalog`.
- [x] T3 — Shared contract suite + `TomlPatchCatalog` adapter + `config/patches.toml` sample, with adapter unit tests. Route: inline. Commit: `feat(adapters): load patch catalog from a TOML file`.
- [x] T4 — S4: `Validity.is_valid_at` and `Chunk.is_valid_at` reject non-`PatchVersion` args. Route: inline. Commit: `fix(domain): reject non-PatchVersion in is_valid_at`.
- [x] T5 — Close: full suite, evidence per task, RED/GREEN report. Route: inline. Commit: `docs(odd): close patch-catalog feature`.

## Acceptance criteria
- Every port method behaves identically in both implementations, asserted by one contract suite.
- No implementation can return a partial or invariant-violating catalog.
- Boundary behaviour of the port: unknown version is not contained; single-patch catalog is valid.
- `uv run pytest -q` green; each task's RED and GREEN observed and recorded below.

## Checks
- `uv run pytest -q`

## Log
### L1 — original request (verbatim)
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

## Delivery slices
- Slice 1 (`b543588`..`eded729`, 702 authored lines): T1-T3. Over the ~400-line review slice on its own, and kept as one slice because the user's delivery rule is one commit per task and the chain is a single dependent feature (port → adapter → contract). Whether to split it into chained PRs is the user's decision.
- Slice 2 (T4): the S4 hardening, 19 lines, small enough to review alone.

## Close
- T5: `uv run pytest -q` → **201 passed** (baseline before the feature: 121). The sample config was also loaded through the public adapter outside pytest: `current: 0.1.1`, `known: 0.1.0, 0.1.1`, `contains(0.1.0) = True`, `contains(0.2.0) = False`.
- S5 checked by reading the imports: `domain/patch_catalog.py` imports only `collections.abc`, `itertools`, `typing` and sibling domain modules; `tomllib`, `pathlib` and `datetime` appear only in the adapter.
- S7 checked: `git log main..HEAD --format=%B | grep -ci 'co-authored-by\|generated with\|claude'` → 0.
- No check was skipped; nothing is pending.

## Next step
Push and open the PR: the user's decision, not taken here.
