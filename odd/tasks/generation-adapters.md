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
- [ ] T1 — Branch `feat/generation-adapters`, feature doc, `uv add anthropic`, verify the installed
  SDK shapes and the documented custom-http-client injection. Route: inline. S2, S5, S8, S9.
- [ ] T2 — `CrossEncoderReranker` unit tests then the adapter. Route: inline. S1, S10.
- [ ] T3 — `ClaudeLanguageModel` unit tests then the adapter. Route: inline. S2–S7, S9.
- [ ] T4 — Integration reranker test and the `live` Claude test (written, never run). Route: inline.
  S11, S12.
- [ ] T5 — Docs: `docs/adr/0005-llm-por-api.md`, `docs/architecture.md` §5.1 steps 5–8, pytest
  markers. Route: inline. S14.
- [ ] T6 — Full suite, integration run, close the record. Route: inline. S13, S15, S16.

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
- **L4 (decision, parent):** the adapter raises its own errors for a refused, truncated or malformed
  response before `GeneratedAnswer` is constructed, so an application-layer
  `InvalidGeneratedAnswerError` never leaks out of an adapter.
