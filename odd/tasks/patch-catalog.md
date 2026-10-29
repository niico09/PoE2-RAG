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
- [ ] T3 — Shared contract suite + `TomlPatchCatalog` adapter + `config/patches.toml` sample, with adapter unit tests. Route: inline. Commit: `feat(adapters): load patch catalog from a TOML file`.
- [ ] T4 — S4: `Validity.is_valid_at` and `Chunk.is_valid_at` reject non-`PatchVersion` args. Route: inline. Commit: `fix(domain): reject non-PatchVersion in is_valid_at`.
- [ ] T5 — Close: full suite, evidence per task, RED/GREEN report. Route: inline.

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
- T1: baseline `uv run pytest -q` → 121 passed. Branch `feat/patch-catalog` created from `main` at `b8e19af` (the `feat/ingest` worktree was parked; it sat on the same commit). No RED applicable (documentation only). Commit `docs(odd): record patch-catalog plan`.
- T2: RED → collection `ImportError: cannot import name 'InMemoryPatchCatalog' from 'poe2_rag.shared.domain'`, 0 tests run. GREEN → 141 passed (20 new). Two first-run failures were bugs in the test helper (default `current="0.1.1"` absent from a single-version catalog), not in the code: the invariant rejected them as designed, the helper was fixed. `PatchCatalog` is `runtime_checkable` so the contract test can assert conformance; `InMemoryPatchCatalog` is a plain slotted class rather than a dataclass because a frozen dataclass cannot hold a `current` field and a `current()` method at once. It copies and sorts its input, so a later mutation of the caller's list cannot change it. Commit `feat(domain): add PatchCatalog port and in-memory catalog`.
