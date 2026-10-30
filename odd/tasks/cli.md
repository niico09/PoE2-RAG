# Feature: cli

## Objective
Wire PoE2-RAG end to end: a frozen `AppConfig`, the `bootstrap.py` composition root, the MVP CLI with
`ingest` and `ask` per ADR-0006, and the CLI error contract, test-first and offline by default.

## Specs
- **S1** — `src/poe2_rag/config.py`: frozen `AppConfig` holding paths (raw, processed, index, patch
  catalog), model ids (embedder, reranker, LLM) and LLM `max_tokens`/`effort`. Defaults are relative to
  a project root; every `POE2_RAG_*` override is read in one function that takes a `Mapping`, so it is
  testable without touching `os.environ`. Values are validated at construction and a bad value raises
  a specific config error. The API key is never read or stored here.
- **S2** — `src/poe2_rag/bootstrap.py`: the composition root. Builds `IngestSource` + `ReindexCorpus`
  and `AnswerQuestion` from an `AppConfig`; builds `anthropic.Anthropic()` lazily, only when `ask`
  needs it (the SDK reads `ANTHROPIC_API_KEY` itself); a missing key is a clear configuration error
  raised before any retrieval work; the key value is never printed, logged or copied into an error.
  Heavy models stay lazy: constructing the container loads no torch model and opens no network.
- **S3** — `ingestion/adapters/inbound/cli`: `poe2-rag ingest patch-notes <path>` runs `IngestSource`
  and then `ReindexCorpus` and prints both reports concisely. The source name is a closed choice with
  one member today, so poe2db and the wiki can be added without changing the command shape.
- **S4** — `answering/adapters/inbound/cli`: `poe2-rag ask "<question>" [--patch <version>]` prints
  the answer, the patch version used and numbered citations (chunk_id, quoted text, source URL). An
  unsupported/no-information answer prints the answer and states that no citation backs it. Without
  `--patch` the default of `AnswerQuestion`/`Query` applies; no new policy is added.
- **S5** — `src/poe2_rag/cli.py`: argparse (stdlib, no new dependency), `main(argv=None, *,
  container=None)`; parses and dispatches only; the container factory is injectable for tests;
  `[project.scripts] poe2-rag = "poe2_rag.cli:main"`.
- **S6** — Error contract at the CLI boundary: expected errors (config, ingestion, answering, LLM
  adapter refusal/truncation/API/connection) print one line on stderr and exit 1 with no traceback;
  argparse usage errors exit 2; unexpected exceptions propagate; success exits 0.
- **S7** — Default suite (offline, free): config parsing from a `Mapping`; bootstrap builds without
  loading models; CLI commands against a fake container (output format, exit codes, stderr messages,
  missing key, LLM refusal, unknown source choice, invalid `--patch`).
- **S8** — Integration (`integration`, not run by default): ingest a small hand-written patch-notes
  fixture into `tmp_path` dirs, then `ask` with the real BM25 + BGE + Chroma + reranker and a fake
  `LanguageModel` that cites the first delivered chunk; assert the exit code and that the printed
  citation names a real `chunk_id`. Run it once and report.
- **S9** — Live (`live`, excluded by default, costs money): the same flow through the real Claude
  adapter. Written, never run.
- **S10** — README "Usage" section (`uv run poe2-rag ingest ...`, `uv run poe2-rag ask ...`, the key
  via `uv run --env-file .env ...`), a `.env.example` with variable names only, and `.env` gitignored.
- **S11** — Domain and application layers stay untouched. A missing port or use-case gap is reported
  instead of patched. The CLI holds no business logic: no filtering, no citation validation, no retries.
- **S12** — No corpus, index or `.env` is committed; tests write only under `tmp_path`. The API key,
  request bodies and full chunk texts beyond the quoted citations are never logged or printed.
- **S13** — TDD: tests first, observe RED, implement GREEN, refactor while green.
- **S14** — Code, tests, docs and commit messages in English. About 400 authored changed lines per task
  is a planning heuristic, not a cap.
- **S15** — Out of scope: `eval` / `RunEvaluation`, an HTTP fetcher, the poe2db and wiki adapters,
  prompt caching, streaming output.

## Non-goals
- `RunEvaluation`, the `eval` command, an HTTP fetcher, the poe2db and wiki adapters.
- Prompt caching, streaming, retries, and any change to the domain, the application layers or the
  outbound adapters.
- Any configuration the prompt does not name: no new retrieval-budget policy, no new default patch.

## Acceptance criteria
- `AppConfig` resolves the documented `POE2_RAG_*` overrides from a `Mapping`, defaults the rest
  relative to the project root, rejects a bad value with a config error, and never touches the key.
- `Container(config)` builds every use case without importing torch, loading a model or opening a
  network connection, and raises the missing-key error before retrieval when the key is absent.
- `poe2-rag ingest patch-notes <path>` prints both reports; `poe2-rag ask "<q>"` prints the answer,
  the patch and numbered citations with the source URL; an unsupported answer says no citation backs it.
- Expected failures print one line on stderr and exit 1; usage errors exit 2; unexpected exceptions
  escape; success exits 0.
- `uv run pytest -q` green and offline; `uv run pytest -q -m integration` reported separately; the
  `live` suite exists and is never run; `uv run poe2-rag --help` and `uv run poe2-rag ask --help` exit 0.

## Checks
- `uv run pytest -q`
- `uv run pytest -q -m integration`
- `uv run poe2-rag --help`; `uv run poe2-rag ask --help`
- `uvx ruff check --select I src tests`; `uvx pyright` (repo config)
- `git log main..HEAD --format=%B | grep -ci 'co-authored-by\|generated with'` → 0
- `grep -rn "ANTHROPIC_API_KEY\|api_key" src` → only the presence check, never a value

## Tasks
- [ ] T1 — `config.py` + `tests/unit/test_config.py`. Route: inline. S1.
- [ ] T2 — `bootstrap.py` + `tests/unit/test_bootstrap.py`. Route: inline. S2.
- [ ] T3 — `ingestion/adapters/inbound/cli` + tests. Route: inline. S3.
- [ ] T4 — `answering/adapters/inbound/cli` + tests. Route: inline. S4.
- [ ] T5 — `cli.py`, `__main__.py`, `[project.scripts]` + dispatch/error-contract tests. Route: inline.
  S5, S6.
- [ ] T6 — integration fixture, integration run and the `live` suite. Route: inline. S7, S8, S9.
- [ ] T7 — README Usage, `.env.example`, `.gitignore`, `docs/architecture.md`. Route: inline. S10.
- [ ] T8 — Full verification, independent verifier, close the record. Route: inline + verify. S11–S15.

## Log
- **L1 (user request, verbatim):**

  Wire PoE2-RAG end to end: a composition root (bootstrap.py) and the MVP CLI with the
  `ingest` and `ask` commands (ADR-0006), test-first, with no real API calls in the default suite.

  ## Context (read first)
  - docs/adr/0006-cli-como-interfaz.md (the CLI is an inbound adapter with no business logic;
    bootstrap builds dependencies; commands are testable with fakes), docs/architecture.md §5,
    docs/adr/0002 (data/raw, data/processed and data/index are gitignored; the corpus is never committed),
    docs/adr/0005 (the API key comes only from the environment and only the adapter knows it).
  - Existing code to wire, not to redefine. Read the constructors and `execute` signatures before
    writing anything:
    - ingestion/application: IngestSource.execute(locator) -> IngestReport,
      ReindexCorpus.execute() -> ReindexReport, plus their errors.
    - ingestion/adapters/outbound: LocalFileFetcher, PatchNotesParser, JsonChunkRepository.
    - answering/application: AnswerQuestion (+ AnswerResult), RetrievalSettings, errors.
    - answering/adapters/outbound: Bm25Index, BgeEmbedder, ChromaIndex, CrossEncoderReranker,
      ClaudeLanguageModel and its adapter error hierarchy.
    - shared/adapters/outbound/toml_patch_catalog.py and config/patches.toml.
  - Find out how the BM25 and Chroma indexes are persisted and reloaded between processes
    (ReindexCorpus writes them, so `ask` must read the same artifacts). If a read path is missing,
    report it as a gap and stop. Do not invent persistence.

  ## Scope
  1. src/poe2_rag/config.py: frozen `AppConfig` with paths (raw, processed, index, patch catalog),
     model ids (embedder, reranker, LLM), and LLM max_tokens/effort.
     - Defaults are relative to a project root.
     - Overrides come only from documented `POE2_RAG_*` environment variables, read in one function
       that takes a Mapping (testable without touching os.environ).
     - Validate values at construction and raise a specific config error.
     - Never read or store the API key here.
  2. src/poe2_rag/bootstrap.py: the composition root.
     - Builds the IngestSource + ReindexCorpus pair and AnswerQuestion from an AppConfig.
     - Builds `anthropic.Anthropic()` lazily, only when `ask` needs it. The SDK reads
       ANTHROPIC_API_KEY from the environment.
     - A missing key is a clear configuration error raised before any retrieval work. Never print
       the key.
     - Heavy models stay lazy. Constructing the container must not load torch models or open the
       network.
  3. Inbound CLI adapters, per ADR-0006:
     - ingestion/adapters/inbound/cli: `poe2-rag ingest patch-notes <path>` runs IngestSource and
       then ReindexCorpus, and prints both reports concisely. The source name is a closed choice,
       currently only `patch-notes`, so poe2db and the wiki can be added later without changing the
       command shape.
     - answering/adapters/inbound/cli: `poe2-rag ask "<question>" [--patch <version>]`.
       - Prints the answer, the patch version used, and numbered citations. Each citation shows the
         chunk_id, the quoted text and the source URL.
       - An unsupported or no-information answer prints the answer and states that no citation backs
         it.
       - Without --patch, use whatever default AnswerQuestion/Query already define. Do not add a new
         policy.
  4. src/poe2_rag/cli.py: argparse (stdlib, no new dependency), `main(argv=None, *, container=None)`.
     - It only parses and dispatches. The container factory is injectable for tests.
     - Register `[project.scripts] poe2-rag = "poe2_rag.cli:main"`.
  5. Error contract at the CLI boundary:
     - expected errors (config, ingestion, answering, LLM adapter refusal/truncation/API/connection)
       → a one-line message on stderr and exit code 1, with no traceback;
     - argparse usage errors → exit code 2;
     - unexpected exceptions propagate (never swallow them);
     - success → exit code 0.
  6. README: a "Usage" section with `uv run poe2-rag ingest ...` and `uv run poe2-rag ask ...`, and
     the key via `uv run --env-file .env ...`. Add `.env.example` with the variable names only, and
     check that `.env` is gitignored.

  ## Testing
  - Default suite (offline, free):
    - config parsing from a Mapping;
    - bootstrap builds without loading models (assert via lazy fakes or monkeypatched factories);
    - CLI commands against a fake container: output format, exit codes, stderr messages, missing key,
      LLM refusal, unknown source choice, invalid --patch.
  - Integration (marked `integration`, not run by default):
    - ingest a small hand-written patch-notes fixture into tmp_path dirs, then `ask` with the real
      BM25 + BGE + Chroma + reranker and a fake LanguageModel that cites the first delivered chunk;
    - assert the exit code and that the printed citation names a real chunk_id.
    - Run it once and report.
  - Live (marked `live`, excluded by default, costs money): the same flow with the real Claude
    adapter. Write it but DO NOT run it.

  ## Rules
  - Domain and application layers stay untouched. If wiring reveals a missing port or a use case
    gap, stop and report it instead of patching the application layer.
  - The CLI holds no business logic: no filtering, no citation validation, no retries.
  - Never log or print the API key, request bodies or full chunk texts beyond the quoted citations.
  - No corpus, index or .env file may be committed. Tests write only under tmp_path.
  - TDD: tests first, observe RED, implement GREEN, refactor while green.
  - Code, tests, docs and commit messages in English.
  - About 400 authored changed lines per task is a planning heuristic, not a cap.
  - Out of scope: `eval` / RunEvaluation, an HTTP fetcher, the poe2db and wiki adapters, prompt
    caching, streaming output.

  ## Allowed edit surfaces
  src/poe2_rag/config.py
  src/poe2_rag/bootstrap.py
  src/poe2_rag/cli.py
  src/poe2_rag/__main__.py
  src/poe2_rag/ingestion/adapters/inbound/**
  src/poe2_rag/answering/adapters/inbound/**
  tests/unit/**
  tests/integration/**
  tests/live/**
  tests/fixtures/**
  pyproject.toml
  uv.lock
  README.md
  .env.example
  .gitignore
  odd/tasks/cli.md
  docs/architecture.md

  ## Delivery
  - Branch feat/cli from main. Feature doc odd/tasks/cli.md with task IDs, the route per task and
    RED/GREEN evidence.
  - One Conventional Commit per task. No Co-Authored-By or any AI attribution.
  - Do not push or open a PR.

  ## Verification
  - `uv run pytest -q` passes (offline default suite).
  - `uv run pytest -q -m integration` result reported separately. Live tests not run.
  - `uv run poe2-rag --help` and `uv run poe2-rag ask --help` exit 0.
  - Report `<command>: <observed result>` and RED/GREEN counts per task.

  ## Return
  Concise report: tasks done, commits, test counts, any wiring gap found (especially index
  reload between processes), deviations from this prompt and why.
  Close with a `## Key Learnings` block of 1–5 numbered standalone facts.

- **L2 (decision, parent): every task runs inline.** The tasks are one sequential wiring chain (each
  task consumes the previous one's module) and the sibling pair T3/T4 is two small adapter files each,
  so the Writer rule's independence test does not hold; the parent already holds the constructor map.
- **L3 (decision, parent): the BM25 and Chroma read paths exist, so this is not a wiring gap.**
  `Bm25Index(directory)` writes and reads `<directory>/bm25.json` with file-identity revalidation on
  every search, and `ChromaVectorIndex(directory, name, embedder=...)` writes a pointer
  `<directory>/<name>.current.json` that `search` reads before opening anything. `ReindexCorpus` writes
  through those two adapters and `AnswerQuestion` reads through the same two classes, so `ask` reads
  exactly what `ingest` wrote as long as the composition root hands both halves the same `index_dir`.
  The integration suite proves it by answering from a second, freshly built container.
- **L4 (decision, parent): the container accepts an optional `language_model` port.** The integration
  suite must exercise the real CLI with a real BM25 + BGE + Chroma + reranker and a fake
  `LanguageModel` (S8), which is only reachable through the CLI if the composition root can be handed
  one; it mirrors the existing `CrossEncoderReranker(scorer=...)` seam and is not used by production.
