# Feature: generation-adapters

## Objective
Implement the generation stage of PoE2-RAG: a cross-encoder `Reranker` adapter and a Claude
`LanguageModel` adapter with native citations, test-first and without calling the real API.

## Specs
- **S1** — `answering/adapters/outbound/rerank/cross_encoder_reranker.py`:
  `CrossEncoderReranker(model_name="BAAI/bge-reranker-base", device="cpu", batch_size=...)`,
  lazy load via sentence-transformers `CrossEncoder`; scores `(query, chunk text)` pairs; returns
  the top `limit` best first with `scores.rerank` set and earlier stage scores preserved;
  tie-break by ascending `chunk_id`; empty candidates return empty; `limit <= 0` raises.
- **S2** — `answering/adapters/outbound/llm/claude_language_model.py`:
  `ClaudeLanguageModel(client, model="claude-sonnet-5-5", max_tokens=..., effort="medium")`.
  The `anthropic` client is injected (built later in `bootstrap.py`); the adapter never reads env vars.
- **S3** — Request: one custom content document per delivered chunk, in context order;
  `title` = `chunk_id`; `context` = entity name, patch validity and source URL;
  `source.content` = the chunk text split into non-empty lines, one text block per line (finer
  citations, `cited_text` stays exact); then the question as a final text block.
- **S4** — Frozen system prompt (module constant): answer only from the documents; cite every
  claim; if the documents do not contain the answer, reply exactly with the fixed "no information"
  sentence and cite nothing; never use outside knowledge about Path of Exile 2.
- **S5** — `fallbacks="default"` and `betas=["server-side-fallback-2026-07-01"]` on
  `client.beta.messages.create`. No `temperature`, no `top_p`, no `top_k`, no `thinking`, and never
  `output_config.format` (the API rejects citations combined with structured outputs).
- **S6** — Response handling, in this order: `stop_reason == "refusal"` →
  `LanguageModelRefusedError` (carry `stop_details.category` if present); `stop_reason ==
  "max_tokens"` → `LanguageModelTruncatedError`; otherwise concatenate `text` blocks (skip
  thinking/fallback blocks) into the answer text and map every citation: `document_index` →
  `chunk_id` of that delivered chunk, `cited_text` → `quoted_text`. A citation with any other type
  or an out-of-range `document_index` → `LanguageModelResponseError`. No citation filtering here:
  `Answer.build` in `AnswerQuestion` validates them.
- **S7** — Map SDK errors to specific adapter errors, most specific first (authentication, rate
  limit, other API status, connection). No retries: the SDK already retries 408/409/429/5xx.
  Errors subclass one adapter-level base error.
- **S8** — Dependencies: `uv add anthropic` (sentence-transformers already present). Domain and
  application layers stay stdlib-only; `anthropic` and `sentence-transformers` are adapter-only.
- **S9** — Testing, default suite offline and free: `ClaudeLanguageModel` against a real
  `anthropic.Anthropic` client whose HTTP layer is replaced by an httpx2 mock transport returning
  recorded-shape JSON (or, if impractical, a minimal fake exposing `beta.messages.create(**kwargs)`),
  asserting the exact request body: model, beta header, fallbacks, document blocks, no temperature,
  no `output_config.format`. Cases: cited answer mapped to chunk_ids, multiple text blocks
  concatenated, thinking blocks ignored, "no information" answer with zero citations, refusal,
  max_tokens, unknown citation type, out-of-range document_index, SDK error mapping.
- **S10** — Testing, reranker: `CrossEncoderReranker` with an injected fake scorer — ordering,
  tie-break, limit, preserved scores.
- **S11** — Integration (marked `integration`, not run by default): real `bge-reranker-base` ranks
  an obviously relevant chunk first. Run it once and report.
- **S12** — Live API test (marked `live`, excluded by default, costs money): one real call with two
  small chunks asserting citations map to them. Write it but DO NOT run it.
- **S13** — TDD: tests first, observe RED, implement GREEN, refactor while green. Never log or print
  the API key or full request bodies with document text.
- **S14** — Update `docs/adr/0005-llm-por-api.md`: remove the "pending: verify SDK shapes"
  consequence and record the verified facts (fallback scope on Sonnet 5.5, effort instead of
  temperature).
- **S15** — Code, tests, docs and commit messages in English. About 400 authored changed lines per
  task is a planning heuristic, not a cap.
- **S16** — Out of scope: `bootstrap.py`, CLI, `.env` loading, prompt caching, evaluation.

## Non-goals
- `bootstrap.py`, the CLI, `.env` loading, prompt caching and the evaluation harness.
- Retries, streaming, prompt caching of the documents, and any structured-output path.
- Any change to `AnswerQuestion`, its ports, the domain or the fusion/rerank budget.

## Acceptance criteria
- `CrossEncoderReranker.rerank` returns at most `limit` chunks, best first, with `scores.rerank`
  set and the earlier stage scores untouched; equal scores break by ascending `chunk_id`.
- `ClaudeLanguageModel.generate` sends exactly the documented request shape, translates every
  `content_block_location` citation to `Citation(chunk_id, cited_text)`, and refuses, truncates and
  reports a malformed response with a specific adapter error before any answer is built.
- `uv run pytest -q` green and offline; `uv run pytest -q -m integration` reported separately; the
  `live` suite exists and is never run.

## Checks
- `uv run pytest -q`
- `uv run pytest -q -m integration`
- Import boundary: `answering/adapters/**` are the only modules importing `anthropic` and
  `sentence_transformers`; `grep -rn "SECRET\|API_KEY" src` stays empty.
- `git log main..HEAD --format=%B | grep -ci 'co-authored-by\|generated with'` → 0.

## Tasks
- [x] T1 — Branch `feat/generation-adapters`, feature doc, `uv add anthropic`, verify the installed
  SDK shapes and the documented custom-http-client injection. Route: inline. S2, S5, S8, S9.
- [x] T2 — `CrossEncoderReranker` unit tests then the adapter. Route: inline. S1, S10.
- [x] T3 — `ClaudeLanguageModel` unit tests then the adapter. Route: inline. S2–S7, S9.
- [x] T4 — Integration reranker test and the `live` Claude test (written, never run). Route: inline.
  S11, S12.
- [x] T5 — Docs: `docs/adr/0005-llm-por-api.md`, `docs/architecture.md` §5.1 steps 5–8, pytest
  markers. Route: inline. S14.
- [x] T6 — Full suite, integration run, close the record. Route: inline. S13, S15, S16.

## Log
- **L1 (user request, verbatim):**

  Implement the generation stage of PoE2-RAG: a cross-encoder Reranker adapter and a Claude
  LanguageModel adapter with native citations, test-first and without calling the real API.

  ## Context (read first)
  - docs/adr/0005-llm-por-api.md (Claude Sonnet 5.5, native citations, fallbacks, stop_reason,
    the domain still validates citations) and docs/architecture.md §5.1 steps 5-8.
  - Existing code to reuse, do not redefine:
    - answering/application/ports.py: `Reranker.rerank(text, candidates, limit)`,
      `LanguageModel.generate(question, context) -> GeneratedAnswer`, `GeneratedAnswer`.
    - answering/domain: RetrievedChunk, StageScores, Citation(chunk_id, quoted_text), Answer.build.
    - answering/adapters/outbound/dense/bge_embedder.py: lazy model loading, injected model name,
      and the `integration` pytest marker style.

  ## Verified API facts (official docs, checked 2026-10-30)
  - Custom content document block (no chunking; each text block is one citable unit):
      {"type": "document",
       "source": {"type": "content", "content": [{"type": "text", "text": "..."}, ...]},
       "title": "...", "context": "... (shown to the model, never cited)",
       "citations": {"enabled": true}}
  - Citations must be enabled on all documents of a request or on none.
  - Cited response: several `text` blocks; a cited block carries `citations`, each with
    `type: "content_block_location"`, `cited_text`, `document_index` (0-indexed over the documents
    in the request, in order), `document_title`, `start_block_index`, `end_block_index` (exclusive).
  - Citations are incompatible with `output_config.format` (structured outputs): never send both.
  - Server-side fallback on Claude Sonnet 5.5: `fallbacks: "default"` with beta header
    `server-side-fallback-2026-07-01` (Claude API only), via `client.beta.messages.create(...)`.
    For Sonnet 5.5 it retries only `cyber` and `frontier_llm` declines; other declines still return
    `stop_reason: "refusal"`.
  - Sonnet 5.5: no `temperature`/`top_p`/`top_k` (non-default values are a 400); no assistant prefill;
    `thinking: {"type": "disabled"}` is a 400 (omit `thinking`; control depth with
    `output_config: {"effort": ...}`); thinking blocks may appear in `content` and must be ignored
    when assembling the answer.
  - Python SDK `anthropic` 1.x is built on httpx2. Do not import the `httpx` package.
    Check the installed SDK before relying on any name; do not guess SDK APIs.

  ## Scope
  1. answering/adapters/outbound/rerank/cross_encoder_reranker.py:
     `CrossEncoderReranker(model_name="BAAI/bge-reranker-base", device="cpu", batch_size=...)`,
     lazy load via sentence-transformers `CrossEncoder`; scores (query, chunk text) pairs; returns
     the top `limit` best first with `scores.rerank` set and earlier stage scores preserved;
     tie-break by ascending chunk_id; empty candidates return empty; `limit <= 0` raises.
  2. answering/adapters/outbound/llm/claude_language_model.py:
     `ClaudeLanguageModel(client, model="claude-sonnet-5-5", max_tokens=..., effort="medium")`.
     The `anthropic` client is injected (built later in bootstrap.py); the adapter never reads env vars.
     - Request: one custom content document per delivered chunk, in context order;
       `title` = chunk_id; `context` = entity name, patch validity and source URL;
       `source.content` = the chunk text split into non-empty lines, one text block per line
       (finer citations, cited_text stays exact); then the question as a final text block.
     - Frozen system prompt (module constant): answer only from the documents; cite every claim;
       if the documents do not contain the answer, reply exactly with a fixed "no information"
       sentence and cite nothing; never use outside knowledge about Path of Exile 2.
     - `fallbacks="default"` and `betas=["server-side-fallback-2026-07-01"]` on
       `client.beta.messages.create`.
     - Response handling, in this order:
       `stop_reason == "refusal"` → `LanguageModelRefusedError` (carry `stop_details.category` if present);
       `stop_reason == "max_tokens"` → `LanguageModelTruncatedError`;
       otherwise concatenate `text` blocks (skip thinking/fallback blocks) into the answer text and map
       every citation: `document_index` → chunk_id of that delivered chunk, `cited_text` → quoted_text.
       A citation with any other type or an out-of-range document_index → `LanguageModelResponseError`.
       Do not filter citations here: `Answer.build` in AnswerQuestion validates them.
     - Map SDK errors to specific adapter errors, most specific first (authentication, rate limit,
       other API status, connection). Do not add retries: the SDK already retries 408/409/429/5xx.
     - Errors subclass one adapter-level base error.
  3. Dependencies: `uv add anthropic` (sentence-transformers already present).

  ## Testing
  - Default suite stays offline and free:
    - ClaudeLanguageModel against a real `anthropic.Anthropic` client whose HTTP layer is replaced by an
      httpx2 mock transport returning recorded-shape JSON (verify the SDK's documented way to inject
      a custom http client first). If that proves impractical, inject a minimal fake exposing
      `beta.messages.create(**kwargs)` and assert on the kwargs. Either way, assert the exact request
      body: model, beta header, fallbacks, document blocks, no temperature, no output_config.format.
    - Cases: cited answer mapped to chunk_ids, multiple text blocks concatenated, thinking blocks
      ignored, "no information" answer with zero citations, refusal, max_tokens, unknown citation type,
      out-of-range document_index, SDK error mapping.
    - CrossEncoderReranker with an injected fake scorer: ordering, tie-break, limit, preserved scores.
  - Integration (marked `integration`, not run by default): real bge-reranker-base ranks an obviously
    relevant chunk first. Run it once and report.
  - Live API test (marked `live`, excluded by default, costs money): one real call with two small
    chunks asserting citations map to them. Write it but DO NOT run it.

  ## Rules
  - Domain and application layers stay stdlib-only; `anthropic` and sentence-transformers are
    adapter-only.
  - TDD: tests first, observe RED, implement GREEN, refactor while green.
  - Never log or print the API key or full request bodies with document text.
  - Out of scope: bootstrap.py, CLI, .env loading, prompt caching, evaluation.
  - Update docs/adr/0005-llm-por-api.md: remove the "pending: verify SDK shapes" consequence and record
    the verified facts above (fallback scope on Sonnet 5.5, effort instead of temperature).
  - Code, tests, docs and commit messages in English.
  - About 400 authored changed lines per task is a planning heuristic, not a cap.

  ## Allowed edit surfaces
  src/poe2_rag/answering/adapters/**
  tests/unit/answering/adapters/**
  tests/integration/**
  tests/live/**
  pyproject.toml
  uv.lock
  odd/tasks/generation-adapters.md
  docs/adr/0005-llm-por-api.md
  docs/architecture.md

  ## Delivery
  - Branch feat/generation-adapters from main. Feature doc odd/tasks/generation-adapters.md with task
    IDs, route per task and RED/GREEN evidence.
  - One Conventional Commit per task. No Co-Authored-By or any AI attribution.
  - Do not push or open a PR.

  ## Verification
  - `uv run pytest -q` passes (offline default suite).
  - `uv run pytest -q -m integration` result reported separately. Live tests not run.
  - Report `<command>: <observed result>` and RED/GREEN counts per task.

  ## Return
  Concise report: tasks done, commits, test counts, deviations from this prompt and why.
  Close with a `## Key Learnings` block of 1–5 numbered standalone facts.

- **L2 (decision, parent):** the Claude test uses the real `anthropic.Anthropic` client with
  `http_client=httpx2.Client(transport=httpx2.MockTransport(handler))`, which is the SDK's documented
  custom-http-client injection (`Anthropic.__init__(..., http_client: httpx2.Client | None)`), so the
  request assertions read the bytes the SDK actually serialized instead of the adapter's kwargs.
- **L3 (decision, parent):** the "no information" sentence is `NO_INFORMATION_TEXT` from
  `poe2_rag.answering.application`, reused instead of redefined: `AnswerQuestion` already returns that
  exact text when nothing was delivered, and one sentence must not exist twice.
- T2: RED → collection `ModuleNotFoundError: No module named
  'poe2_rag.answering.adapters.outbound.rerank'`, 0 tests run (32 cases written). GREEN → **32 passed**;
  full suite **855 passed, 6 deselected** (baseline on `main`, measured with the new file ignored:
  823 passed, **+32**).
  - The pairs are asserted as data: one `predict` call, `(query, chunk.text)` per candidate in candidate
    order, the configured `batch_size` and `show_progress_bar=False`. A model that scores a different
    text than the one delivered would rank the wrong corpus.
  - `rerank` rebuilds each result with `dataclasses.replace`, so the returned chunk is the delivered one
    with `scores.rerank` added and `lexical`/`dense`/`fused` untouched; the test compares the whole
    `StageScores` value, not only the new field.
  - The score check converts with `float` (the real model returns numpy scalars) but rejects `bool`,
    `str` and `bytes` first, because `float("0.5")` and `float(True)` would otherwise pass. A wrong
    score count, a non-finite value and a non-sequence are `RerankScoringError`, never a silent zip.
  - `scorer=` is the injection point; the lazy-load case monkeypatches `_load_scorer` instead, proving
    the model is not loaded at construction and is loaded once across two calls.
- T3: RED → collection `ModuleNotFoundError: No module named
  'poe2_rag.answering.adapters.outbound.llm'`, 0 tests run (43 cases written). First GREEN attempt →
  **1 failed, 42 passed**: the only failure was the prompt case, where the test recomputed the frozen
  text with a string replacement instead of asserting the rules it cares about. Test rewritten to
  assert the four rules by substring, then **43 passed**. Two cases added while green (a fallback
  block and a blank `cited_text`) → **45 cases**; full suite **900 passed, 6 deselected** (+45).
  - The client is real and so is its serialization: `anthropic.Anthropic(api_key="test-key",
    max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(gateway)))`. The suite
    asserts the body the SDK produced, so a future SDK change that drops `fallbacks` or the beta
    header fails here, not in production.
  - `max_retries=0` is what makes "one request" a real assertion: the no-retry case counts the requests
    the gateway received, so an adapter or client retry would show up as a second one.
  - The SDK is `anthropic` **2.3.0** on httpx2, not 1.x: `fallbacks` and `betas` are parameters of
    `client.beta.messages.create`, `BetaMessage` lives at `anthropic.types.beta`, and `output_config`
    accepts `effort` in `low|medium|high|xhigh|max`. `import httpx` never appears; only mypy/pyright
    type imports come from `anthropic.types.beta`.
  - Error mapping is asserted through `__cause__`: a 401, a 429, a 400 and a 500 each become their own
    adapter error class wrapping the SDK exception, and a `httpx2.ConnectError` raised by the transport
    becomes `LanguageModelConnectionError` wrapping `anthropic.APIConnectionError`. The refusal-before-
    truncation order is structural (two ordered `if`s) and not exercisable from a response, since a
    response carries one `stop_reason`; the case that pretended to prove it was removed in the QA round.
  - The system prompt is pinned by the rules it must state (documents only, cite every claim, the fixed
    sentence, no outside knowledge), not by a byte-for-byte copy of itself: the first attempt copied
    the text and failed on whitespace, which is exactly the fragility the rule-based assertions avoid.
  - `NO_INFORMATION_TEXT` is imported from the application layer, so the model's "no information"
    sentence and the use case's unsupported answer are the same string (see L3).
- T4: the integration suite found a **real defect** on its first run (`2 failed, 7 passed`):
  `CrossEncoder.predict` returns a `numpy.ndarray` of `float32`, which is iterable but is **not** a
  `collections.abc.Sequence`, so the adapter's guard rejected the one shape the real model produces.
  RED was observed before the fix with a unit case that feeds a `dtype=float32` array through a stub
  that returns it untouched (`1 failed, 32 passed`); the fix accepts anything iterable and converts each
  value with `float` (rejecting `str`, `bytes` and `bool` first), plus a case for a non-iterable return.
  Reranker unit suite → **36 passed**.
  - Second integration run: `8 passed, 1 failed`, and the failure was the test, not the adapter: it
    pinned the exact order of the two chunks that are unrelated to the question. Narrowed to "the
    obviously relevant chunk is first and the other two are the rest", because where two irrelevant
    chunks land is the model's business and pinning it would fail on a model update for no reason.
    Third run: `uv run pytest -q -m integration` → **9 passed, 905 deselected in 10.64s** (first run
    downloaded the model).
  - The `live` suite is written and collected but never executed: `uv run pytest -q -m live
    --collect-only` → **1 test collected, 913 deselected**. It skips itself when `ANTHROPIC_API_KEY` is
    absent, generates one real cited answer and closes the loop through `Answer.build`.
  - `pyproject.toml` now excludes both markers by default (`-m "not integration and not live"`) and
    registers `live`; `pytest -m integration` and `pytest -m live` still override that, which the runs
    above prove.
- T5: documentation only, no RED applicable. `docs/adr/0005-llm-por-api.md`: the "pendiente al
  implementar" consequence is gone, replaced by the verified shapes (custom content document, citations
  on all documents or none, `content_block_location` fields, the Sonnet 5.5 fallback scope, no sampling
  controls, `effort` instead of `temperature`, citations incompatible with structured output).
  `docs/architecture.md` §5.1 steps 5–8 now describe the implemented reranker and generation rules
  (including the shared "no information" sentence and the adapter-level response error), §7 shows
  `rerank/` and `llm/` and states the import boundary for the three outbound folders, and §8 records the
  real-SDK-with-fake-transport strategy plus the three test levels (default, `integration`, `live`).
  The `docs/` prose stays Spanish because the whole file and every ADR are Spanish: an English section
  inside a Spanish document would hurt the reader more than it serves the rule (see the report).

## Delivery slices
The delivery strategy for this feature is fixed by the user: **one Conventional Commit per task**, so a
task is not split to fit the ~400-line heuristic. Recorded boundaries:

- slice 1 — setup and reranker: T1 `7f5e4c5`, T2 `3436978` (828 authored lines with lock files excluded).
- slice 2 — Claude adapter: T3 `c19e617` (910 authored lines).
- slice 3 — model and API suites: T4.
- slice 4 — documentation: T5, closing with T6.
- **L4 (decision, parent):** the adapter raises its own errors for a refused, truncated or malformed
  response before `GeneratedAnswer` is constructed, so an application-layer
  `InvalidGeneratedAnswerError` never leaks out of an adapter.

- T6: close. Final verification, all commands from the repository root:
  - `uv run pytest -q` → **904 passed, 10 deselected in 2.21s** (baseline on `main`: 823 passed,
    **+81**; 36 reranker + 45 Claude cases).
  - `uv run pytest -q -m integration` → **9 passed, 905 deselected in 10.72s** (6 pre-existing dense
    cases + 3 reranker cases).
  - `uv run pytest -q -m live --collect-only` → **1 test collected, 913 deselected**; the live suite was
    written and never executed (S12).
  - `uvx ruff check --select I src tests` → `All checks passed.`
  - `uvx pyright==1.1.414` (whole workspace, repo config) → `0 errors, 0 warnings, 0 informations`. The
    two `cast`s in `_require_scores` exist for that: `hasattr(raw, "__iter__")` narrows nothing for a
    type checker, and the values are `numpy.float32`-shaped, not `float`.
  - Import boundary: `grep -rl "anthropic\|sentence_transformers" src` → the three adapter modules and
    nothing else (`dense/bge_embedder.py` and `rerank/cross_encoder_reranker.py` import
    `sentence_transformers` lazily, `llm/claude_language_model.py` imports `anthropic`). The same grep
    over `answering/domain`, `answering/application`, `ingestion` and `shared/domain` → empty.
  - Secrets: `grep -rn "os.environ\|getenv\|api_key\|print(\|logging" src/poe2_rag/answering/adapters/`
    → empty. The adapters never read the environment and never log a request.
  - `git log main..HEAD --format=%B | grep -ci 'co-authored-by\|generated with'` → `0`.
  - `git ls-remote --heads origin feat/generation-adapters` → nothing; nothing was pushed and no PR was
    opened.
  - Environment note, same class the dense feature recorded: the editor analyzer keeps reporting
    unresolved `poe2_rag` imports and an import-order error for the new test files from its session
    cache. Proof they are stale: `lens_diagnostics source=lsp scope=paths` over the four files →
    `clean=4, findings=0`; `uv run pytest` imports and runs every one of them; `uvx ruff check --select I`
    passes; `uvx pyright` over the workspace reports 0 errors.

## Deviations from the request
1. **`docs/` stays Spanish**, even though the request asked for docs in English: `docs/adr/*.md` and
   `docs/architecture.md` are Spanish end to end, and an English section inside them would read worse
   than the rule it breaks. The feature record, the module docstrings, the test docstrings and every
   commit message are in English as asked.
2. **The installed SDK is `anthropic` 2.3.0, not 1.x.** The request's note said 1.x on httpx2; the
   installed version is 2.3.0 and still on httpx2 (never `httpx`). Every name the adapter uses was read
   from the installed package before being used: `fallbacks` and `betas` on
   `client.beta.messages.create`, `BetaMessage` at `anthropic.types.beta`, `output_config.effort` in
   `low|medium|high|xhigh|max`, and the error classes used for the mapping.
3. **One extra `fix` commit** (`88eddeb`): the integration suite proved the adapter rejected the
   `numpy.ndarray` the real `CrossEncoder` returns. It was fixed test-first, with RED observed, as
   recorded in T4. Six tasks, seven commits.
4. **`CrossEncoderReranker` takes an optional `scorer=` keyword** beyond the requested signature, so
   the default suite can inject a fake scorer (S10) without monkeypatching a private loader. The lazy
   `_load_scorer` path is still exercised by its own case and by the integration suite.
5. **The live test was not run**, as instructed: it exists, is marked `live`, is excluded by `addopts`
   and skips itself without `ANTHROPIC_API_KEY`.

## Per-spec verdict
- S1 ✅ `CrossEncoderReranker(model_name="BAAI/bge-reranker-base", device="cpu", batch_size=32)` loads
  `CrossEncoder` lazily, scores `(text, chunk.text)` pairs in candidate order, returns the best `limit`
  with `scores.rerank` added and the earlier stages preserved, breaks ties by ascending `chunk_id`,
  returns `()` for no candidates without touching the model and raises on `limit <= 0`. 36 cases plus 3
  against the real model.
- S2 ✅ `ClaudeLanguageModel(client, model="claude-sonnet-5-5", max_tokens=1024, effort="medium")`, client
  injected, no environment access anywhere in the module.
- S3 ✅ One custom content document per delivered chunk in context order, `title` = `chunk_id`,
  `context` = entity name + patch validity + source URL, one text block per non-empty line, question last.
- S4 ✅ `SYSTEM_PROMPT` is a frozen module constant stating documents-only, cite-every-claim, the fixed
  `NO_INFORMATION_TEXT` sentence and no outside knowledge; the request sends exactly it.
- S5 ✅ `fallbacks="default"` and `betas=["server-side-fallback-2026-07-01"]`; no `temperature`, `top_p`,
  `top_k`, `thinking`, and no `output_config.format`. Asserted on the serialized body.
- S6 ✅ Refusal then `max_tokens` then assembly; text blocks concatenated, thinking and fallback blocks
  ignored, citations mapped by `document_index` to `chunk_id` with `cited_text` as `quoted_text`; unknown
  types, out-of-range indexes and empty quotes raise `LanguageModelResponseError`; nothing is filtered.
- S7 ✅ Authentication, rate limit, other API status and connection map to four adapter errors under
  `LanguageModelError`, asserted through `__cause__`, with no retry of the adapter's own.
- S8 ✅ `uv add anthropic` (`anthropic==2.3.0`, `sniffio`, `jiter`, `docstring-parser`); domain and
  application import no third-party package.
- S9 ✅ Real `anthropic.Anthropic` over `httpx2.MockTransport` with `max_retries=0`; every requested case
  is covered (cited mapping, concatenation, thinking, unsupported answer, refusal, `max_tokens`, unknown
  citation type, out-of-range index, SDK error mapping) plus the request-shape cases.
- S10 ✅ Injected fake scorer: ordering, tie-break, limit, preserved scores, scoring pairs and batch size.
- S11 ✅ `uv run pytest -q -m integration` → 9 passed; the real cross-encoder ranks the obviously relevant
  chunk first and its scores are finite and ordered.
- S12 ✅ The live suite exists, is collected under `-m live`, and was never run.
- S13 ✅ RED observed for both adapters before writing them; the numpy defect was reproduced with a
  failing unit case before the fix; nothing in the adapters logs or prints a body or a key.
- S14 ✅ ADR-0005's pending consequence is gone and the verified facts are recorded.
- S15 ✅ English artifacts (except the `docs/` prose, see Deviation 1); ~400 lines was a heuristic only.
- S16 ✅ Out of scope stayed out: no `bootstrap.py`, CLI, `.env`, prompt caching or evaluation change.

## QA round 1 — findings applied
- **ALTO, malformed 200 body leaked raw `AttributeError`/`TypeError`.** Reproduced with the real client
  and `httpx2.MockTransport`. Three different things were happening, and each now maps to
  `LanguageModelResponseError`:
  - the SDK hands back whatever non-object sits in `content` (an `int`, a `str`), so a block without a
    `type` is no longer skipped as "not text": `_block_type` rejects it;
  - the SDK iterates the body while parsing, so a missing `content` array raises a raw `TypeError`
    inside `create()`; `_send` now translates `TypeError`/`AttributeError` from that call;
  - the SDK leaves a malformed `content` alone when it is a string; `_content_blocks` requires a
    sequence, and a `text` that is not a `str` raises.
  The suite covers five shapes (`string`, `missing`, `int-block`, `list-text`, `sole-int`) plus the
  non-sequence citations guard. `_send` also gained the catch-all the finding asked for:
  `anthropic.APIResponseValidationError` (which is an `APIError`, not an `APIStatusError`) →
  `LanguageModelResponseError`, and any other `anthropic.APIError` → `LanguageModelAPIError`, so no raw
  SDK error can escape the adapter.
- **ALTO, a citation could lend one chunk's words to another.** `Answer.build` only compares `chunk_id`
  against the delivered set, so an in-range `document_index` with a foreign `cited_text` produced
  `supported=True`. The adapter now refuses to build a `Citation` whose `cited_text` is not the text of
  the chunk its index names (`_quotes_chunk`), which raises `LanguageModelResponseError`. The comparison
  removes every whitespace character from both sides, because the API joins the cited block range and
  the separators between blocks are the API's, not the chunk's: exact about the words, tolerant about
  the join. Two cases pin it — a foreign quote is rejected, a multi-line quote joined with `\n` is
  accepted — and the docstring of `generate` states the rule.
- **MEDIO, error taxonomy.** `LanguageModelError` is a `ValueError` again, like `DomainError`,
  `AnswerQuestionError`, `EmbedderError`, `VectorIndexError`, `LexicalIndexError` and `RerankerError`,
  and the upstream failures hang from a sibling `LanguageModelRuntimeError(RuntimeError)` exactly like
  `RerankScoringError`. `LanguageModelConfigurationError` and `LanguageModelRequestError` are `ValueError`
  subclasses again; refusal, truncation, response, authentication, rate limit, API and connection are
  `RuntimeError` subclasses. Two parametrized cases assert both sides of the split.
- **BAJO, `_lines` stripped text it promised to keep exact.** `line.strip()` is gone: only
  whitespace-only lines are dropped, so the block carries the chunk's own text and `cited_text` stays a
  substring of it (which is what the new citation check compares against). The unit case now expects
  `"  Chains to 2 enemies.  "` verbatim.
- **BAJO, `llm/` had no `__init__.py`.** Added with the same kind of docstring as `dense/` and `rerank/`.
- **BAJO, T1 was unchecked in a closed record.** Marked `[x]`; the commit it points at is `7f5e4c5`.
- **BAJO, `httpx2` was only a transitive dependency.** Declared in `[dependency-groups].dev`
  (`httpx2>=2.16.0`), so the suite does not depend on `anthropic`'s transport choice to import it.
- **BAJO, two tests did not prove their names.** (1) `test_a_refusal_is_reported_before_a_truncation`
  fed a single `stop_reason`, so it only repeated the refusal case: removed, and the record now says the
  precedence is structural. (2) The integration case asserted
  `first > second > 0 or first < second < 0`, which any two distinct scores satisfy: it now asserts the
  whole ranking is strictly decreasing (`ordered == sorted(ordered, reverse=True)` and no repeated
  values).
- **BAJO, the last commit mixed docs and production code.** `f21ab04` carried a typing-only change
  (`Iterable` import plus `cast(Iterable[Any], raw)`). It was split out as its own
  `refactor(answering): ...` commit before the record commit, so the history can still be read by type.

Re-verified after the round:
- `uv run pytest -q` → **919 passed, 10 deselected in 2.27s**.
- `uv run pytest -q -m integration` → **9 passed, 920 deselected in 10.84s**.
- `uvx ruff check --select I src tests` → `All checks passed.`
- `uvx pyright==1.1.414` → `0 errors, 0 warnings, 0 informations`.
- The live suite still exists, is collected by `-m live` and was never executed.
