# Feature: retrieval-eval

Add the retrieval half of `RunEvaluation` to PoE2-RAG at zero API cost: a versioned JSONL evaluation
dataset, a deterministic retrieval evaluation (Hit@K, MRR, stale-retrieval rate) and `poe2-rag eval`.
Generation evaluation is out of scope.

## Specs

- **S1 — RetrieveContext.** Retrieval becomes its own application use case, `RetrieveContext`:
  question + optional version → target version + delivered `RetrievedChunk`s, in the exact order and
  with the exact validation `AnswerQuestion` applies today. `AnswerQuestion` delegates to it and then
  generates; its behavior is unchanged, so every existing `AnswerQuestion` test stays green without
  edits (behavior-preserving refactor, done first). `RunEvaluation` depends on `RetrieveContext`
  only, never on `LanguageModel`.
- **S2 — Package layout.** New capability package
  `src/poe2_rag/evaluation/{domain,application,adapters/{inbound,outbound}}`: `domain` holds
  `EvalCase` and the metrics as pure functions; `application` holds `RunEvaluation` plus its ports
  (dataset reader, report writer); `adapters` hold the JSONL reader, the JSON report writer and the
  CLI. The evaluation package must not import answering adapters: it talks to `RetrieveContext`
  through its application interface.
- **S3 — Dataset format.** JSONL, one case per line, with a `schema_version` field. Fields: `id`
  (unique slug), `question`, `patch` (optional; the default patch policy applies when absent),
  `expected_chunk_ids` (list; empty means "the corpus does not answer this"), `expected_answer`
  (short free text, unused for now, kept for the generation stage) and `tags` (list).
  "Invalid lines fail loud with the line number."
- **S4 — Metrics, over answerable cases (non-empty expected ids).** Hit@K for K in a configurable
  set (default 1, 3, 5); MRR on the first expected id found; stale-retrieval rate: "the share of
  cases where any delivered chunk is not valid at the case's target version. It should be 0, because
  the filter runs before fusion; it exists as a guard for D1." Unanswerable cases are counted and
  listed separately, never scored as hits or misses. The report carries per-tag breakdowns too.
- **S5 — Dataset drift.** Before evaluating, every expected `chunk_id` must exist in the current
  corpus (`ChunkRepository.all` or an equivalent read port). "Unknown ids → a specific error listing
  them. Nothing is evaluated against a dataset that no longer matches the corpus."
- **S6 — Report.** A JSON file with run timestamp, git commit if available (passed in, never computed
  inside the domain), dataset path and hash, config snapshot (model ids, retrieval settings),
  aggregate metrics, per-tag metrics and per-case results (rank of first hit, delivered ids). It
  holds ids only, never chunk text. Written to a configurable runs directory, default
  `data/eval/runs/` (gitignored). The CLI prints a compact table.
- **S7 — CLI.** `poe2-rag eval [--dataset PATH] [--k 1,3,5] [--output-dir PATH]`. The default dataset
  path comes from config (`eval/dataset.jsonl`). Expected errors → stderr plus exit code 1, as for
  the other commands. `eval` must not build the Anthropic client nor require `ANTHROPIC_API_KEY`.
- **S8 — Example dataset and docs.** `eval/dataset.example.jsonl` with three or four cases written
  against the committed poe2db/patch-notes test fixtures, so they double as the integration dataset.
  The format is documented in `eval/README.md` (English). Writing the real dataset is the user's job,
  not this task's.
- **S9 — Testing.** Refactor: the existing `AnswerQuestion` suite passes unchanged, plus
  `RetrieveContext` unit tests with the same fakes. Metrics: hand-computed cases, including a hit at
  rank 1, a hit at rank K+1 (a miss for Hit@K), no hit, unanswerable cases excluded, and an empty
  dataset → a specific error. `RunEvaluation` with fakes: drift detection, per-tag aggregation, a
  deterministic report payload. CLI with a fake container: output, exit codes, no API key needed.
  Integration (marked `integration`): ingest the fixtures into `tmp_path`, run eval with the example
  dataset through the real BM25 + BGE + Chroma + reranker, assert a report is written with
  stale-retrieval rate 0, and report the metrics observed.
- **S10 — Rules and delivery.** Domain stays stdlib-only; immutable value objects; inject the clock
  and the commit id. TDD: tests first, observe RED, implement GREEN, refactor while green. Add
  `data/eval/` to `.gitignore`. Never commit run reports or corpus text. Update `docs/architecture.md`
  (`RetrieveContext` in §5, the evaluation layout, the metric definitions) and the README usage
  section. Code, tests, docs and commit messages in English. Out of scope: generation metrics,
  LLM-as-judge, dataset authoring, retrieval tuning.

## Tasks

| # | Task | Specs | Route | Commit |
|---|---|---|---|---|
| T1 | Extract `RetrieveContext`; `AnswerQuestion` delegates; bootstrap updated | S1 | inline | pending (flow gate) |
| T2 | Evaluation domain: `EvalCase` validation and metric functions | S2 S4 | inline | pending (flow gate) |
| T3 | `RunEvaluation`, ports and the report value objects | S2 S5 S6 | inline | pending (flow gate) |
| T4 | Adapters: JSONL reader, atomic JSON report writer, clock, git commit | S3 S6 | inline | pending (flow gate) |
| T5 | CLI `eval`, config defaults, container wiring, example dataset | S7 S8 | inline | pending (flow gate) |
| T6 | Integration suite, `.gitignore`, architecture and README docs | S9 S10 | inline | pending (flow gate) |

## Evidence

| Task | RED | GREEN |
|---|---|---|
| T1 | `test_retrieve_context.py` against the pre-refactor `src` (`git archive HEAD`): `ImportError: cannot import name 'RetrieveContext'`, collection error | `uv run pytest tests/unit/answering -q` → **485 passed**; full suite **1198 passed, 16 deselected** |
| T2 | same file set against missing `poe2_rag.evaluation.domain`: unresolved imports (collection error) | `uv run pytest tests/unit/evaluation -q` → **42 passed** |
| T3 | `evaluation_fakes.py` could not import `poe2_rag.evaluation.application` (module absent) | `uv run pytest tests/unit/evaluation -q` → **58 passed** |
| T4 | delegated worker, per file: `NotImplementedError` from the stub adapter (reader 17, writer 7, clock 1, git 5 tests red) | `uv run pytest -q tests/unit/evaluation/adapters` → **35 passed** (34 from the worker + 1 guard added on review: a commit id that is not a single path segment) |
| T5 | `AttributeError: 'AppConfig' object has no attribute 'eval_dataset_path'` (5 failures); `AttributeError: 'Container' object has no attribute 'evaluation'` (3 failures) | `tests/unit/test_config.py` **47 passed**; `tests/unit/test_cli.py` **35 passed**; `tests/unit/test_bootstrap.py` **23 passed** |
| T6 | `MissingPageUrlError` (the poe2db parser needs an absolute http(s) locator, so the fixtures are served by a mocked `httpx2` transport) and, before the wiring, `NotImplementedError` from `git_commit` | `uv run pytest -q -m integration` → **14 passed, 1310 deselected in 26.93s**; `uv run pytest -q` → **1307 passed, 17 deselected** |

Observed metrics of the integration run (`eval/dataset.example.jsonl` over the four fixture
documents, 12 chunks):

- aggregate over 3 answerable cases: `Hit@1 = 1.0`, `Hit@3 = 1.0`, `Hit@5 = 1.0`, `MRR = 1.0`,
  `stale-retrieval rate = 0.0`
- `gems` (2 cases): the same five numbers; `patch-notes` (1 case): the same five numbers
- the unanswerable case delivered five chunks and was counted, not scored: `first_hit_rank = null`
- report file `20261104T030356Z-nogit.json` (`git_commit = null` because the test root is not a
  repository), dataset digest `1aaf38aa…63b1`, `cases = 4`, `answerable = 3`, `unanswerable = 1`

## Log

**L1 — user request (verbatim).**

Add the retrieval half of RunEvaluation to PoE2-RAG: a versioned evaluation dataset format, a
deterministic retrieval evaluation (Hit@K, MRR, stale-retrieval rate) and `poe2-rag eval`,
test-first, with zero API cost. Generation evaluation is out of scope (a later feature).

### Context (read first)
- docs/architecture.md: D4 (every pipeline change is measured against a fixed dataset), §5
  (RunEvaluation: dataset → metrics report), the evaluation row of the testing strategy (Hit@K,
  MRR, faithfulness, stale-answer rate) and the planned evaluation/ package layout.
- docs/adr/0005: retrieval is evaluated separately from generation, because retrieval is
  deterministic.
- docs/adr/0006: `poe2-rag eval [--dataset <path>]`.
- answering/application/answer_question.py: AnswerQuestion.execute(text, version) runs retrieval
  (lexical + dense, fusion, rerank, validation) and generation in one use case. AnswerResult
  carries target_version and the delivered chunks.
- bootstrap.py, config.py, cli.py and the existing inbound CLI adapters are the wiring reference.

### Design decisions (already taken)
1. Extract retrieval into its own application use case, `RetrieveContext`:
   - question + optional version → target version + delivered RetrievedChunks, in the exact order
     and with the exact validation AnswerQuestion applies today;
   - AnswerQuestion delegates to it and then generates. Its behavior is unchanged: every existing
     AnswerQuestion test stays green without edits (behavior-preserving refactor, done first);
   - RunEvaluation depends on RetrieveContext only, never on LanguageModel.
2. New capability package src/poe2_rag/evaluation/{domain,application,adapters/{inbound,outbound}}:
   - domain: EvalCase, metrics as pure functions;
   - application: RunEvaluation plus its ports (dataset reader, report writer);
   - adapters: JSONL reader, JSON report writer, CLI.
3. Dataset format: JSONL, one case per line, with a `schema_version` field. Fields:
   - id (unique slug);
   - question;
   - patch (optional; the default patch policy applies when absent);
   - expected_chunk_ids (list; empty means "the corpus does not answer this");
   - expected_answer (short free text, unused for now, kept for the generation stage);
   - tags (list).
   Invalid lines fail loud with the line number.
4. Metrics, over answerable cases (non-empty expected ids):
   - Hit@K for K in a configurable set (default 1, 3, 5);
   - MRR on the first expected id found;
   - stale-retrieval rate: the share of cases where any delivered chunk is not valid at the
     case's target version. It should be 0, because the filter runs before fusion; it exists as
     a guard for D1.
   Unanswerable cases are counted and listed separately, never scored as hits or misses.
   Report per-tag breakdowns too.
5. Dataset drift: before evaluating, every expected chunk_id must exist in the current corpus
   (ChunkRepository.all or an equivalent read port). Unknown ids → a specific error listing them.
   Nothing is evaluated against a dataset that no longer matches the corpus.
6. Report: a JSON file with run timestamp, git commit if available (passed in, never computed
   inside the domain), dataset path and hash, config snapshot (model ids, retrieval settings),
   aggregate metrics, per-tag metrics and per-case results (rank of first hit, delivered ids). It
   holds ids only, never chunk text. It is written to a configurable runs directory, default
   data/eval/runs/ (gitignored). The CLI prints a compact table.

### Scope
1. Refactor: RetrieveContext extracted, AnswerQuestion delegating, bootstrap updated.
2. Evaluation domain: EvalCase validation and metric functions, with exact arithmetic tests.
3. RunEvaluation plus ports; JSONL reader; JSON report writer (atomic write).
4. CLI: `poe2-rag eval [--dataset PATH] [--k 1,3,5] [--output-dir PATH]`.
   - The default dataset path comes from config (eval/dataset.jsonl).
   - Expected errors → stderr plus exit code 1, as for the other commands.
   - eval must not build the Anthropic client nor require ANTHROPIC_API_KEY.
5. eval/dataset.example.jsonl: three or four cases written against the committed poe2db/patch-notes
   test fixtures, so they double as the integration dataset. Document the format in eval/README.md
   (English). Writing the real dataset is the user's job, not this task's.

### Testing
- Refactor: the existing AnswerQuestion suite passes unchanged, plus RetrieveContext unit tests
  with the same fakes.
- Metrics: hand-computed cases, including a hit at rank 1, a hit at rank K+1 (a miss for Hit@K),
  no hit, unanswerable cases excluded, and an empty dataset → a specific error.
- RunEvaluation with fakes: drift detection, per-tag aggregation, a deterministic report payload.
- CLI with a fake container: output, exit codes, and no API key needed.
- Integration (marked `integration`): ingest the fixtures into tmp_path, run eval with the example
  dataset through the real BM25 + BGE + Chroma + reranker, and assert a report is written with
  stale-retrieval rate 0. Run it once and report the metrics observed.

### Rules
- Domain stays stdlib-only. Immutable value objects. Inject the clock and the commit id.
- The evaluation package must not import answering adapters. It talks to RetrieveContext through
  its application interface.
- TDD: tests first, observe RED, implement GREEN, refactor while green.
- Add data/eval/ to .gitignore. Never commit run reports or corpus text.
- Update docs: architecture.md (RetrieveContext in §5, the evaluation layout, the metric
  definitions) and the README usage section.
- Code, tests, docs and commit messages in English.
- About 400 authored changed lines per task is a planning heuristic, not a cap.
- Out of scope: generation metrics (faithfulness, multi-run averaging), LLM-as-judge, dataset
  authoring, any retrieval tuning.

### Delivery
- Branch feat/retrieval-eval from main. Feature doc odd/tasks/retrieval-eval.md with task IDs, the
  route per task and RED/GREEN evidence.
- One Conventional Commit per task, with the refactor commit first. No Co-Authored-By or any AI
  attribution.
- Do not push or open a PR.

### Verification
- `uv run pytest -q` passes (offline default suite).
- `uv run pytest -q -m integration` result reported separately, with the observed metrics.
- `uv run poe2-rag eval --help` exits 0. `env -u ANTHROPIC_API_KEY uv run poe2-rag eval --dataset
  <tmp dataset>` does not fail for a missing key (use the integration setup or a fake container).

**L2 — Herdr flow rule (verbatim, overrides the Delivery commit plan).**

[Herdr flow] Regla del flow: NO commitees hasta que `herdr-flow gate poe2-rag@runevaluation`
devuelva 0. Cuando termines un cambio, terminá el turno sin commitear: la review (qa + review) corre
sola sobre tu diff sin commitear y te llegan los hallazgos o el OK con las instrucciones de commit,
push y PR. Nunca mergees ni hagas force push. No uses comandos destructivos (rm -rf, git clean,
reset --hard) ni para limpiar temporales: dejalos en /tmp, así no quedás esperando una confirmación
humana.

Consequence: the per-task commits of the Delivery section are deferred until the flow gate returns 0;
the work stays uncommitted on the worktree branch `runevaluation` (which is at `main`'s tip, so
switching to `feat/retrieval-eval` later is a rename, not a rebase).

**L3 — baseline evidence (before T1).** `uv run pytest -q` → `1183 passed, 16 deselected`, exit 0.

**L4 — ronda 1 de qa/review: hallazgos aplicados.** Un solo lote, sin cambios de alcance:

- `.gitignore` ahora ignora `eval/dataset.jsonl` (el dataset real); `eval/dataset.example.jsonl` sigue
  versionado, así que las tres frases de los docs ("no se commitea") son ciertas.
- `_k_values` ya no reimplementa las reglas de `require_ks`: exige dígitos (`1_0` y `+5` son error de
  uso) y traduce `InvalidMetricsError` a un error de argparse, como `_patch_version`.
- Un `--k` mayor que `final_top_k` se rechaza en `RunEvaluation.execute` con
  `InvalidEvaluationRequestError`, antes de recuperar: Hit@10 sobre un top-5 solo repetía Hit@5.
- El guardia de recuperación obsoleta cambió de denominador: `Metrics.evaluated_cases` (todos los
  casos evaluados) en vez de solo los respondibles, así un caso no respondible con un chunk obsoleto no
  puede quedar enmascarado por un 0.000. `cases` sigue siendo el denominador de Hit@K y MRR; el
  reporte publica ambos.
- `AnswerQuestion` ahora recibe `retrieve: RetrieveContext` (un colaborador en vez de seis) y expone
  `retrieval`; `bootstrap.answer_question` pasa `self.retrieve_context`, así `ask` y `eval` comparten la
  misma recuperación y la docstring de `Container.evaluation` deja de ser falsa.
- La suite de `AnswerQuestion` quedó con lo que solo ese caso de uso hace (delegación, generación,
  citas): 24 pruebas de contrato de recuperación se borraron porque ya viven en `test_retrieve_context.py`.
- `_k_values`, `ConfigSnapshot.as_dict` (deriva los campos con `dataclasses.fields`),
  `evaluation/adapters/__init__.py` (solo docstring, como el resto del repo), el nombre del reporte
  (agrega 8 hex del digest del dataset: dos corridas en el mismo segundo ya no se pisan) y
  `RunMetadata` (valida `git_commit` como sha hex de 7-64) siguen los hallazgos del review.
- Los tests dejaron de duplicar fixtures: `tests/cli_fakes.py` arma el reporte de las dos suites de
  CLI, y `tests/cli_e2e.py` expone `integration_config()` y `offline_client(pages)`, que usan los dos
  suites de integración.
- El test de integración ya no afirma la calidad de la recuperación (se borró
  `all(first_hit_rank is not None)`) y su docstring aclara que el 0.0 de stale es una guarda: el
  pipeline real no puede entregar un chunk obsoleto porque `RetrieveContext` lanza `StaleChunkError`;
  la métrica que sí puede dispararse la cubre el retriever falso de `test_run_evaluation.py`.

Verificación de la ronda: `uv run pytest -q` → **1293 passed, 17 deselected**;
`uv run pytest -q -m integration` → **14 passed**; `uv run poe2-rag eval --help` → exit 0.

**L5 — ronda 2 de qa/review: hallazgos aplicados.** Sin cambios de alcance:

- Las diez reglas de recuperación que la ronda 1 dio por cubiertas volvieron a su archivo: fusión de un
  ranking con su score, doble stage score + `fused`, corte `rerank_top_m` antes del reranker, chunk
  ajeno más allá de `final_top_k`, chunk obsoleto más allá de `final_top_k`, duplicado del reranker,
  score denso faltante, score de rerank faltante, `ChunkMismatchError` y `rrf_k` configurado. Están en
  `test_retrieve_context.py` (25 casos) con el helper `retrieval(...)`.
- El patrón del commit id vive una sola vez (`report.COMMIT_ID_PATTERN`); el writer lo importa.
- El tope de K se le pide al recuperador (`RetrieveContext.final_top_k`, en el puerto
  `ContextRetriever`) en vez de a la foto de configuración del reporte: la autoridad es quien entrega.
- `Metrics.cases` pasó a `Metrics.answerable_cases`, para que en un mismo JSON `dataset.cases` no
  signifique otra cosa que lo que significa en `metrics`.
- Se borró la property `AnswerQuestion.retrieval`: no tenía consumidor de producción y la suite ya
  afirmaba slots privados en el test de bootstrap.

Verificación de la ronda: `uv run pytest -q` → **1303 passed, 17 deselected**;
`uv run pytest -q -m integration` → **14 passed**; `uv run ruff check --select E4,E7,E9,F,I ...` →
all checks passed.
