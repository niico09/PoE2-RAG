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
- [ ] T3 — `Validity`: half-open range `[valid_from, valid_until)`, open-ended when `valid_until is None`, `is_valid_at(version)`, invariant `valid_from < valid_until`. Route: delegated.
- [ ] T4 — `EntityRef`, `SourceRef`, `Modifier` (`ModifierKind`: increased, reduced, more, less, added, flat). Route: delegated.
- [ ] T5 — `Chunk` entity composing the above; `chunk_id` format `<kind>:<slug>@<valid_from>#<section>`. Route: delegated.

## Acceptance criteria
- `uv run pytest` passes; every task observed RED before GREEN.
- Boundary tests for validity: `v == valid_from` (valid), `v == valid_until` (not valid), open range, `v < valid_from`.

## Checks
- `uv run pytest -q`

## Progress / Evidence
- T1: scaffold via `uv init --lib`, Python 3.12.16 pinned, pytest 9.2.0 dev dep, `testpaths = ["tests"]`. `uv run pytest -q` → "no tests ran" (exit 5, expected, no tests yet). No RED applicable (configuration only). Commit `1c0cda3`.
- T2: RED → collection ImportError (`PatchVersion` not defined in `poe2_rag.shared.domain`). GREEN → 23 passed. Leading zeros (`01.0.0`) also rejected so `str()` round-trips exactly.

## Next step
T3.
