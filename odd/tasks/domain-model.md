# Feature: domain-model

## Objective
Implement the shared domain model described in `docs/architecture.md` §4, test-first, with no external dependencies.

## Problem / Why
Patch validity and modifier semantics are the rules that, if wrong, no retriever can fix later (drivers D1, D2). They must exist and be tested before any adapter.

## Scope
- In: project scaffold (uv, Python 3.12, pytest), `PatchVersion`, `Validity`, `EntityRef`, `SourceRef`, `Modifier`, `Chunk` in `src/poe2_rag/shared/domain/`.
- Out: `PatchCatalog` adapters, integer version encoding (adapter detail per ADR-0003), ingestion, retrieval, CLI.

## Constraints
- `domain` imports only the standard library.
- Immutable value objects (frozen dataclasses); specific exceptions; invariants validated at construction.
- Code and comments in English.

## Delivery
- Branch: `feat/domain-model`. Strategy: `ask-on-risk`. Forecast: ~350–450 authored changed lines.
- One work-unit commit per task (Conventional Commits).

## Tasks
- [x] T1 — Project scaffold: `pyproject.toml`, package layout, pytest config, `.gitignore`. Route: delegated (writer, 2+ files).
- [x] T2 — `PatchVersion`: parse `major.minor.patch`, total ordering, reject invalid formats and negatives. Route: delegated.
- [x] T3 — `Validity`: half-open range `[valid_from, valid_until)`, open-ended when `valid_until is None`, `is_valid_at(version)`, invariant `valid_from < valid_until`. Route: delegated.
- [x] T4 — `EntityRef`, `SourceRef`, `Modifier` (`ModifierKind`: increased, reduced, more, less, added, flat). Route: delegated.
- [x] T5 — `Chunk` entity composing the above; `chunk_id` format `<kind>:<slug>@<valid_from>#<section>`. Route: delegated.

## Acceptance criteria
- `uv run pytest` passes; every task observed RED before GREEN.
- Boundary tests for validity: `v == valid_from` (valid), `v == valid_until` (not valid), open range, `v < valid_from`.

## Checks
- `uv run pytest -q`

## Progress / Evidence
- T1: scaffold via `uv init --lib`, Python 3.12.16 pinned, pytest 9.2.0 dev dep, `testpaths = ["tests"]`. `uv run pytest -q` → "no tests ran" (exit 5, expected, no tests yet). No RED applicable (configuration only). Commit `1c0cda3`.
- T2: RED → collection ImportError (`PatchVersion` not defined in `poe2_rag.shared.domain`). GREEN → 23 passed. Leading zeros (`01.0.0`) also rejected so `str()` round-trips exactly. Commit `773555c`.
- T3: RED → ImportError (`InvalidValidityError` not exported). GREEN → 36 passed (13 new; boundary tests from==valid, until==invalid, v<from, open range far future). Commit `dc434f6`.
- T4: RED → ImportError (`EntityRef`, `InvalidModifierError`, `InvalidSourceRefError` not exported). GREEN → 81 passed (45 new). `Modifier.is_multiplicative` encodes more/less vs increased (D2); values must be finite int/float (bool rejected); URLs need http(s) scheme and host. Commit `976ce09`.
- T5: RED → ImportError (`Chunk` not exported). GREEN → 99 passed (18 new). `chunk_id` uses `EntityRef.key`, so it reads `active_gem:spark@0.1.2#description` (architecture §4.3 example shows `gem:spark`, which does not match `entity.kind`; flagged for a doc fix). `dataclasses.replace` re-runs invariants. Commit `cf4162b`.

## Next step
All tasks done. Fix the `chunk_id` example in `docs/architecture.md` §4.3 to `active_gem:spark@0.1.2#description`; then decide on delivery (push / PR) under repository policy.
