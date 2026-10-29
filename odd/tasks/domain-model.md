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
- Chain strategy: stacked PRs to `main` (chosen by the user).
  - PR 1 (`feat/domain-model-01-core` → `main`): `1c0cda3^..dc434f6` — scaffold, `PatchVersion`, `Validity`. 369 lines (36 tests pass at `dc434f6`). The `^` is included because the recorded counts include the first commit of the range.
  - PR 2 (`feat/domain-model` → `feat/domain-model-01-core`): `976ce09^..HEAD` — `EntityRef`, `SourceRef`, `Modifier`, `Chunk`, doc alignment, T6 hardening. ~500 lines, over the 400 budget; kept as one slice because T6 spans both value objects and `Chunk`.

## Tasks
- [x] T1 — Project scaffold: `pyproject.toml`, package layout, pytest config, `.gitignore`. Route: delegated (writer, 2+ files).
- [x] T2 — `PatchVersion`: parse `major.minor.patch`, total ordering, reject invalid formats and negatives. Route: delegated.
- [x] T3 — `Validity`: half-open range `[valid_from, valid_until)`, open-ended when `valid_until is None`, `is_valid_at(version)`, invariant `valid_from < valid_until`. Route: delegated.
- [x] T4 — `EntityRef`, `SourceRef`, `Modifier` (`ModifierKind`: increased, reduced, more, less, added, flat). Route: delegated.
- [x] T5 — `Chunk` entity composing the above; `chunk_id` format `<kind>:<slug>@<valid_from>#<section>`. Route: delegated.
- [x] T6 — Fix review warnings (lineage review-b3722f7a7baa70e5, approved, non-blocking): constrain `section` and `EntityRef.kind` to a slug-like pattern so `chunk_id` cannot contain separators or surrounding whitespace; `PatchVersion.parse` raises `InvalidPatchVersionError` for non-str input; `SourceRef` maps `urlsplit` `ValueError` to `InvalidSourceRefError`. Deferred suggestions: type check in `is_valid_at`, `OverflowError` for huge ints in `Modifier`, `datetime` accepted as `captured_at`. Route: delegated (writer, 4+ files).

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
- T6: RED → 16 failed, 105 passed (non-slug `section`/`kind`, non-str `PatchVersion.parse`, `http://[::1` URL). GREEN → 121 passed (16 new). Single shared `_text.is_slug` (`[a-z0-9_-]+`) now validates `EntityRef.kind`, `EntityRef.slug` and `Chunk.section`; deferred suggestions left untouched. Commit `2915f29` (`fix(domain): harden chunk_id parts and input validation`).

## Round 1 — QA findings

Eight findings from the round-1 QA pass over `feat/domain` (`b8e19af`), all applied test-first on that
branch:

- Q1 `SourceRef` stored the URL with its surrounding whitespace (`urlsplit` ignores it), so two
  references to the same page were unequal and any dedup keyed by URL would split. The URL is
  stripped before it is stored.
- Q2 Host validation only checked `bool(netloc)`, so it accepted `https://exa mple.com`, `https://.a`
  and `https://@:80`. The hostname must now be non-empty, contain no whitespace and have no empty
  dot-separated label; IPv6 literals and `localhost` still pass.
- Q3 `captured_at` accepted `datetime` (a `date` subclass), which breaks ordering and equality
  against plain dates. It is rejected explicitly.
- Q4 `math.isfinite` raised `OverflowError` for ints such as `10**400`. Finiteness is now checked for
  floats only, since ints are always finite.
- Q5 `is_valid_at` leaked `TypeError` for a non-`PatchVersion` argument. It raises
  `InvalidValidityError`, the same fix that landed on `feat/patch-catalog` (`f3eaf70`).
- Q6 `chunk_id` omitted the source, so the poe2db and poe2wiki copies of one section collided and a
  `Citation`, which carries only a `chunk_id`, could not name the source backing the answer. The id
  is now `<source>:<kind>:<slug>@<version>#<section>`.
- Q7 `kind` accepted any slug, so `active-gem` and `active_gem` split one entity's identity on a
  typo. The vocabulary is now the closed set `ENTITY_KINDS` (`active_gem`, `support_gem`, `mechanic`,
  `item`).
- Q8 `is_slug` accepted separator-only values (`-`, `_`, `---`), producing ids like
  `active_gem:spark@0.1.2#-`. A slug now needs at least one alphanumeric character.
- Q9 `ModifierKind.ADDED` and `FLAT` were indistinguishable in the docs. The enum now states which
  pool each one feeds; both stay additive.
- Q10-Q11 The README status line and roadmap item, and the `docs/architecture.md` header, still said
  nothing was implemented. Both now state that §4 is implemented and the rest is not.
- Q12 These delivery ranges now use inclusive notation (`^`), T6 records its commit, and this
  section replaces the stale "merge PR 1" next step.

Commits `4577496`, `f418dcb`, `e30edcc`, `9d14550` and the documentation commit that carries this
record. Suite: 121 → 159 passed.

## Next step
Both PRs are merged (`2bc767b` for PR 1, `b8e19af` for PR 2) and the round-1 QA findings are
committed on `feat/domain`. Push and open the PR for this round: the user's decision, not taken here.
