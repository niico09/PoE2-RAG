# Feature: opencode-language-model

## Objective

Generar respuestas de `ask` con el CLI `opencode` (suscripción OpenCode Go, sin facturación de la API de
Anthropic) detrás del mismo puerto `LanguageModel`, sin cambiar dominio ni aplicación, con timeout duro y un
agente dedicado sin herramientas.

- **Estado:** en curso
- **Rama de trabajo:** `languagemodel` (worktree de Herdr); el commit de cierre lo dicta el flujo tras el gate
- **Puerto:** `src/poe2_rag/answering/application/ports.py:107` — `LanguageModel.generate(question, context) -> GeneratedAnswer`

## Specs

- **S1** — Nuevo adaptador `OpenCodeLanguageModel` en
  `src/poe2_rag/answering/adapters/outbound/llm/opencode_language_model.py` que satisface
  `LanguageModel.generate(question, context) -> GeneratedAnswer`.
  Texto del pedido: "New adapter `OpenCodeLanguageModel` in
  `answering/adapters/outbound/llm/opencode_language_model.py` that satisfies `LanguageModel.generate(question,
  context) -> GeneratedAnswer`."
- **S2** — Prompt: "answer ONLY from the delivered chunks, wrapped as `<source chunk_id="...">text</source>`;
  reply with JSON only, `{"answer": str, "citations": [{"chunk_id": str, "quoted_text": str}]}`, with
  `quoted_text` copied verbatim; when the sources do not answer, say so and return no citations."
- **S3** — "Hard timeout: run `opencode` through an injectable process-runner abstraction (a small Protocol); on
  timeout kill the whole process group (opencode may spawn children) and raise a specific
  `LanguageModelTimeoutError(LanguageModelRuntimeError)`. Never hang `ask`."
- **S4** — "Minimal agent: do NOT use the user's global agent. Investigate the CLI (`opencode run --help`,
  `opencode agent --help`, its config docs) and run with a dedicated agent that has no tools and a short system
  prompt, using the most isolated mechanism available (for example a project-scoped config passed by env var or
  `--agent`, and `--standalone` if it isolates the run). Never pass `--auto`. Document the chosen mechanism and
  why. If no tool-free isolation is possible, stop and report instead of shipping."
- **S5** — "Robust parsing: prefer `--format json` if it yields the final assistant text reliably; otherwise
  extract the first top-level JSON object from stdout. Non-zero exit, empty output, or malformed/schema-invalid
  JSON raise `LanguageModelResponseError` (or a sibling specific error) with a bounded, secret-free message."
- **S6** — "Citation validation: a citation whose `chunk_id` is not in `context`, or whose `quoted_text` is not a
  verbatim substring of that chunk's text, must not pass. First check what `Answer.build` already validates and
  reuse it; only add adapter-side checks that are missing (no duplicated logic)."
- **S7** — "Config (`config.py`): `POE2_RAG_LLM_PROVIDER` in {`opencode`, `claude`}, default `opencode`;
  `POE2_RAG_LLM_TIMEOUT` (seconds, default 120, positive). The model default depends on the provider
  (`opencode-go/deepseek-v4.1-flash` for opencode, unchanged for claude); `POE2_RAG_LLM_MODEL` still overrides.
  `ANTHROPIC_API_KEY` is required only when the provider is `claude`. Validate at the config boundary."
- **S8** — "Wiring: `Container.language_model` picks the adapter by provider; nothing else in the domain or
  application changes. `ask` maps the new errors to the existing CLI error contract."
- **S9** — "Docs: new `docs/adr/0007-generacion-via-opencode.md` (Spanish, same format as the others) that amends
  ADR-0005 and records the trade-offs (subprocess latency, no native citations, hang risk mitigated by tim[eout],
  Ollama as plan B behind the same port). Update the README env-var section."
- **S10** — "TDD with RED/GREEN evidence. Unit tests use a fake runner, never the real binary: happy path;
  citations outside context; non-verbatim quote; timeout raises `LanguageModelTimeoutError` and kills the group;
  non-zero exit; empty and malformed output; the unanswerable case returns no citations; the command line never
  contains `--auto` and does select the minimal agent."
- **S11** — "Config and bootstrap tests: provider selection, defaults, timeout validation, API key needed only for
  cla[ude]" y "One live test in `tests/live/` (same marker/skip pattern as
  `test_claude_language_model_live.py`), skipped when `opencode` is not on PATH."
- **S12** — "SOLID; dependency injection; specific exceptions; no silenced errors; English for code, tests and
  comments." / "Never log or embed credentials; error messages must not echo the full prompt or context." /
  "Do not commit `data/`, `eval/dataset.jsonl` or `.env`." / "Conventional Commits, one per task, no AI
  attribution or Co-Authored-By."

## Non-goals

- Reemplazar `ClaudeLanguageModel`: queda como proveedor alternativo detrás del mismo puerto.
- Tocar `domain/` o `application/`: el puerto ya alcanza (S8).
- Implementar Ollama u otro proveedor: ADR-0007 lo deja como plan B explícito.
- Streaming de respuestas, reintentos automáticos o telemetría de costos del CLI.

## Acceptance criteria

- `Container.language_model` devuelve `OpenCodeLanguageModel` con `POE2_RAG_LLM_PROVIDER=opencode` (default) y
  `ClaudeLanguageModel` con `claude`, sin pedir `ANTHROPIC_API_KEY` en el primer caso.
- Un `opencode` colgado muere por timeout de grupo de procesos y `ask` sale con error reportado, nunca colgado.
- Ninguna invocación contiene `--auto` y siempre se fija el agente dedicado sin herramientas.
- Citas fuera del contexto o no verbatim se rechazan con el error ya existente de `Answer.build`.
- `uv run pytest -q` verde, `ruff check` y `ruff format --check` limpios.

## Tasks

| ID | Specs | Ruta | Estado |
|---|---|---|---|
| T1 | S7 | worker | ✅ config: provider, timeout, defaults por proveedor, validación |
| T2 | S1–S6, S10, S12 | worker + parent | ✅ adaptador `OpenCodeLanguageModel` + tests unitarios (fake runner); 4 defectos corregidos por el parent |
| T3 | S8, S11 | worker | ✅ wiring en `Container.language_model` + tests de bootstrap |
| T4 | S9 | parent | ✅ ADR-0007 (español) + sección de env vars del README |
| T5 | S11 | worker | ✅ test live con skip por PATH; **verificado contra el binario real** |
| T6 | Verification | parent | ✅ pytest, ruff, diferencial de config; ⛔ las dos corridas de `ask` no son ejecutables (sin índice) |

## Log

- **L1** — Pedido original (verbatim, resumido a su encabezado porque el cuerpo está citado en S1–S12):
  "# Task: OpenCode generation adapter behind the `LanguageModel` port".
- **L2** — Correcciones del humano antes del worker: "Paths de tests. Si tests/unit/test_config.py o
  tests/unit/test_bootstrap.py tienen otro nombre en el repo, el worker te lo va a frenar. Lo resolvés pasándole
  los paths reales." y "Proveedor por defecto. El prompt deja opencode como default, así ask anda sin la key de
  Anthropic, y Clau[de] [con] `POE2_RAG_LLM_PROVIDER=claude`."
- **L3** — Verificado: `tests/unit/test_config.py`, `tests/unit/test_bootstrap.py` y
  `tests/unit/answering/adapters/outbound/llm/test_claude_language_model.py` existen con esos nombres exactos.
  No hace falta renombrar nada (cierra L2, primera parte).
- **L4** — Evidencia de exploración (explorer + probes): el puerto es `ports.py:107`; `GeneratedAnswer` vive en
  `ports.py:79`; el validador reutilizable es `Answer.build(text, citations, delivered)` en
  `answering/domain/answer.py:60-75`, que rechaza `chunk_id` no entregado con
  `InvalidCitationError(f"citation to {chunk_id!r}, which was not delivered")` y **no** valida verbatim (falta
  ese chequeo, va en el adaptador — S6). La jerarquía `LanguageModelError(...)`/`LanguageModelRuntimeError`
  vive en `claude_language_model.py:59-108`; `bootstrap.EXPECTED_ERRORS` (`:152-193`) es el único registro
  cross-adapter. Config: dataclass frozen + `load_config(environ)`, patrones `_choice` y
  `_require_positive_seconds` (`config.py:218`, `:249`). `Container.language_model` está en
  `bootstrap.py:331-341` y hoy exige la API key incondicionalmente (`_require_api_key`).
- **L5** — Restricción real del CLI (probes propios, `opencode v2.2.4`): `opencode run` acepta `--agent`,
  `--format {default,json}`, `-m provider/model`, `--standalone`; **no** existe subcomando `opencode agent` en
  esta versión. `--format json` emite **JSONL de eventos** (`step_start` / `text` / `step_finish`), no un objeto
  único: el texto final es el `part.text` del último evento `type == "text"` (S5 lo usa).
- **L6** — `opencode debug agents` **no** refleja config inyectada (ni por env var ni por archivo): listar agentes no
  sirve como prueba. El discriminador válido es `--agent <name>`, que valida y falla con
  `Error: Agent not found: "..."` (exit 1) cuando el agente no existe.
- **L7** — **S4 resuelto y verificado (conductual, `opencode v2.2.4`).** Mecanismo elegido: variable de entorno
  `OPENCODE_CONFIG_CONTENT` con el JSON completo del agente dedicado (nada escrito a disco), más `--agent
  poe2-answer` (validado por L6) y `--standalone` (servidor privado, no el servicio de fondo compartido). El
  agente va bajo la clave `agent` (singular) con `mode: primary`, `prompt` corto, `tools: {"*": false}` y
  `steps: 1`. Se agrega `OPENCODE_DISABLE_PROJECT_CONFIG=1` como guarda ante un `opencode.json` del repo (hoy no
  existe). Evidencia: (a) con el agente dedicado y un pedido explícito de usar una herramienta de archivo, el
  stream no trae **ningún** evento `tool` y el modelo responde "I have no tools available in this session";
  (b) los tokens de entrada bajan de **11.190** (agente global `gentle-orchestrator`) a **4.709** con el agente
  dedicado, que es la prueba de que el prompt ambiental dejó de viajar; (c) latencia medida 4–10 s por corrida;
  (d) `--auto` no se pasa en ninguna invocación. Descartados: `OPENCODE_CONFIG=<archivo>` y
  `OPENCODE_CONFIG_DIR` también funcionan, pero escriben o exigen un archivo en disco sin ventaja de aislamiento.
- **L8** — `--format json` emite JSONL de eventos; el texto final es el `part.text` del último evento
  `type == "text"`. Un stream sin evento `text` es respuesta inválida, no una respuesta vacía válida.
- **L9** — El repo no tiene `data/` (ni trackeado ni en disco), así que no hay índice construido: las dos corridas
  reales de `ask` del bloque Verification **no son ejecutables** en este entorno y se reportan como prueba
  faltante, no como verdes. El test live del adaptador no depende del índice porque arma sus propios chunks
  (igual que el de Claude).
- **L10** — Worker (T1, T2, T3, T5): `errors.py` con la jerarquía compartida y `claude_language_model.py`
  re-exportándola (verificado: mismos objetos, `LanguageModelTimeoutError` cae en `EXPECTED_ERRORS`);
  `opencode_language_model.py` con `ProcessRunner`/`SubprocessRunner`; config y wiring por proveedor. RED
  capturado por el worker en adaptador, config y bootstrap; `pytest -q` 1547 passed.
- **L11** — **Cuatro defectos que el worker no pudo ver** (no tenía el binario) y corrigió el parent, con RED
  propio antes de cada arreglo:
  (a) **crítico** — el entorno del hijo era solo las dos variables de configuración, sin `PATH` ni `HOME`: un
  `opencode` real no habría encontrado su binario ni su sesión guardada. Ahora se hereda una lista blanca
  (`PATH`, `HOME`, `XDG_*`, locale, temporales) y **nunca** `ANTHROPIC_API_KEY` u otro secreto exportado;
  (b) `timeout` aceptaba `inf`/`nan`, que anulaba el corte duro; (c) el `stderr` del CLI entraba crudo al
  mensaje de error, con saltos de línea y >200 caracteres; (d) los pipes no se drenaban tras el kill del grupo.
  RED: 3 tests fallando (`test_the_child_environment_carries_the_variables_the_cli_needs`,
  `test_a_non_finite_timeout_is_rejected_at_construction`,
  `test_a_multi_line_stderr_is_reported_on_one_bounded_line`); GREEN: 21 passed.
- **L12** — **Verificación final**: `uv run pytest -q` → **1552 passed, 30 deselected**; `ruff check` sobre los
  archivos tocados → limpio (los 10 errores del repo están en archivos que no tocamos: `bm25_index.py`,
  `chroma_index.py`, `application/__init__.py`, `json_chunk_repository.py`, `shared/domain/__init__.py`,
  `test_poe2db_gem_page_parser.py`, `test_polite_http_fetcher.py`); `ruff format --check` sobre los 8 archivos
  propios → limpio; test live contra `opencode` real → **1 passed in 8.06 s**, con citas verbatim válidas.
  Diferencial de config contra el commit base (`git archive` a `/tmp`): único campo que cambia es `llm_model`
  por proveedor, y solo cuando `POE2_RAG_LLM_MODEL` no está seteado — el resto idéntico. Las dos corridas de
  `ask` fallan con `error: lexical index not found: .../data/index/bm25.json` porque el worktree no tiene
  corpus ni índice: prueba no ejecutada, no fallo del cambio.
- **L13** — Pendiente fuera de las superficies permitidas: `.env.example` no lista `POE2_RAG_LLM_PROVIDER` ni
  `POE2_RAG_LLM_TIMEOUT` (el `README.md` sí). Lo agrega el humano o una tarea aparte.
- **L14** — **Quinto defecto, el más serio, encontrado por el parent en la revisión final:** un `chunk_id`
  inventado por el modelo levanta `InvalidCitationError` en `Answer.build`, y esa excepción **no** estaba en
  `bootstrap.EXPECTED_ERRORS` (deriva de `DomainError`, no de `AnswerQuestionError`), así que `ask` habría
  salido con un traceback en vez del contrato `error: …`. Con Claude el caso era inalcanzable (las citas
  llegan como índices de documento y siempre mapean a un chunk entregado); con opencode el modelo escribe el
  JSON y puede inventar el id, así que es un modo de falla real. Arreglo: una entrada en `EXPECTED_ERRORS`
  con la razón documentada, sin duplicar la validación en el adaptador (S6 se mantiene). RED:
  `test_a_citation_the_model_invented_is_reported_instead_of_escaping` →
  `AssertionError: assert False`; GREEN: 1 passed, suite 1552 passed.
