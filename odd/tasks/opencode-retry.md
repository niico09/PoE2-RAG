# Feature: opencode-retry

## Objective

Reintentar **una sola vez** la generación con `OpenCodeLanguageModel` cuando la salida del modelo es
transitoria (sin evento de texto, texto vacío o texto que no es JSON válido), sin reintentar fallas
permanentes, con el mismo prompt y un timeout completo por intento. La cantidad de intentos es un parámetro
del constructor (default 2, entero >= 1), así que tests y composition root pueden fijarla sin configuración
nueva por entorno.

- **Estado:** en curso
- **Rama de trabajo:** `fix/opencode-retry`
- **Superficies:** `src/poe2_rag/answering/adapters/outbound/llm/opencode_language_model.py`,
  `src/poe2_rag/answering/adapters/outbound/llm/__init__.py`,
  `tests/unit/answering/adapters/outbound/llm/**`, `pyproject.toml`,
  `docs/adr/0007-generacion-via-opencode.md`, `odd/tasks/opencode-retry.md`

## Specs

- **S1** — Clasificar las fallas de respuesta en transitorias y permanentes.
  Transitorias: "no text event, empty or whitespace-only text, text that is not valid JSON".
  Permanentes: "valid JSON that violates the answer schema, citations that are malformed or point outside the
  delivered context, non-zero exit, timeout, configuration and authentication errors".
  La clasificación se expresa con una subclase dedicada:
  "`LanguageModelEmptyResponseError(LanguageModelResponseError)` raised at the transient sites, so callers
  that catch `LanguageModelResponseError` keep working and the retry decision is a type check, not string
  matching."
- **S2** — "`generate` retries exactly once on a transient failure, with the same prompt, and with a fresh
  full timeout for that attempt. It does not retry permanent failures or timeouts. If the retry also fails, it
  raises the retry's error, with a message saying it failed after 2 attempts (bounded and secret-free, no
  prompt or context)."
- **S3** — "Keep the attempt count a constructor parameter with default 2 (validated: integer >= 1), so tests
  and the composition root can set it; do not add new environment configuration."
- **S4** — "Fix the stale `live` marker description in `pyproject.toml`: it must say it exercises real model
  backends (the Claude API, which costs money and needs `ANTHROPIC_API_KEY`, or the local `opencode` CLI)."
- **S5** — Tests nuevos con el `FakeRunner` existente: salida vacía y luego respuesta válida devuelve la
  respuesta con el hijo corrido dos veces; texto no JSON y luego respuesta válida, igual; dos fallas
  transitorias levantan el error transitorio con el hijo corrido exactamente dos veces; JSON inválido por
  schema, cita fuera del contexto, exit distinto de cero y timeout levantan de inmediato con el hijo corrido
  una vez; `attempts=1` desactiva el reintento; valores inválidos de `attempts` levantan
  `LanguageModelConfigurationError`. Los tests live existentes siguen pasando.

## Tasks

- **T1** — Clasificación transitoria/permanente, reintento único y `attempts` validado, con los tests de
  `S5` (`S1`, `S2`, `S3`). Ruta: inline.
- **T2** — Documentación: marker `live` de `pyproject.toml` y decisión de reintento en ADR-0007 (`S4`).
  Ruta: inline.

El gate `herdr-flow` de este worktree revisa **un** diff y entrega con `git add -A && git commit` (nunca
`--no-verify`), así que T1 y T2 viajan en un único commit convencional:
`fix(llm): retry opencode generation once on transient output`.

## Log

- **L1** — Pedido original del humano, verbatim:

  ```text
  # Task: retry once on empty or malformed opencode output

  ## Context
  `OpenCodeLanguageModel` (`src/poe2_rag/answering/adapters/outbound/llm/opencode_language_model.py`) runs the
  `opencode` CLI per question. `generate` (line ~202) runs the child once and calls `_parse` (line ~350) and
  `_parse_answer_json` (line ~420), which raise `LanguageModelResponseError`. In a 20-question baseline run twice,
  2 of 38 model calls failed with `LanguageModelResponseError: model output is not valid JSON: ` with an empty
  preview, i.e. the model returned no text. The failures hit different questions each run (`ice-nova-critical-chance`,
  then `arc-lightning-infused`), and the same questions succeeded in the other run, so the failure is transient
  rather than tied to the input. Every other answer was correct and cited.

  ## Required behavior / Design
  1. Classify response failures as transient or permanent:
     - transient: no text event, empty or whitespace-only text, text that is not valid JSON;
     - permanent: valid JSON that violates the answer schema, citations that are malformed or point outside the
       delivered context, non-zero exit, timeout, configuration and authentication errors.
     Prefer expressing this with a dedicated subclass (for example
     `LanguageModelEmptyResponseError(LanguageModelResponseError)`) raised at the transient sites, so callers that
     catch `LanguageModelResponseError` keep working and the retry decision is a type check, not string matching.
  2. `generate` retries exactly once on a transient failure, with the same prompt, and with a fresh full timeout
     for that attempt. It does not retry permanent failures or timeouts. If the retry also fails, it raises the
     retry's error, with a message saying it failed after 2 attempts (bounded and secret-free, no prompt or context).
  3. Keep the attempt count a constructor parameter with default 2 (validated: integer >= 1), so tests and the
     composition root can set it; do not add new environment configuration.
  4. Fix the stale `live` marker description in `pyproject.toml`: it must say it exercises real model backends
     (the Claude API, which costs money and needs `ANTHROPIC_API_KEY`, or the local `opencode` CLI).

  ## Testing
  - TDD with RED/GREEN evidence, using the existing fake runner:
    - empty output then a valid answer: returns the answer and ran the child twice;
    - non-JSON then a valid answer: same;
    - two transient failures: raises the transient error, child ran exactly twice;
    - schema-invalid JSON, citation outside context, non-zero exit and timeout: raise immediately, child ran once;
    - attempts=1 disables retry; invalid attempts values raise `LanguageModelConfigurationError`.
  - The existing live tests (`uv run pytest -q -m live tests/live/test_opencode_language_model_live.py`) still pass.

  ## Rules
  - SOLID, dependency injection, specific exceptions, no silenced errors; English for code, tests and comments.
  - No new logging framework; if the adapter needs to report a retry, keep it out of stdout.
  - Conventional Commits, one per task, no AI attribution or Co-Authored-By.

  ## Allowed edit surfaces
  src/poe2_rag/answering/adapters/outbound/llm/opencode_language_model.py
  src/poe2_rag/answering/adapters/outbound/llm/__init__.py
  tests/unit/answering/adapters/outbound/llm/**
  pyproject.toml
  docs/adr/0007-generacion-via-opencode.md
  odd/tasks/opencode-retry.md

  ## Delivery
  Branch `fix/opencode-retry`, ODD doc `odd/tasks/opencode-retry.md`. Open a PR; do not merge.

  ## Verification
  - `uv run pytest -q` (report the count)
  - `uv run pytest -q -m live tests/live/test_opencode_language_model_live.py`
  - `uv run ruff check . && uv run ruff format --check .`

  ## Return
  Summary, the transient/permanent classification as implemented, RED/GREEN evidence, verification results,
  PR URL, then a `## Key Learnings` block of 1–5 numbered standalone sentences.
  ```
- **L2** — **T1 implementado.** RED: `uv run pytest -q tests/unit/answering/adapters/outbound/llm/test_opencode_language_model.py`
  → `ImportError: cannot import name 'LanguageModelEmptyResponseError'` (1 error de colección). GREEN tras
  definir `LanguageModelEmptyResponseError` en el adaptador, levantarla en los dos sitios transitorios
  (`_parse` sin evento de texto y `_parse_answer_json` con JSONDecodeError) y exportarla desde
  `llm/__init__.py`: **102 passed**.
- **L3** — **Reintento implementado.** RED: 4 tests del reintento fallando con
  `TypeError: OpenCodeLanguageModel.__init__() got an unexpected keyword argument 'attempts'` y los 8 tests de
  clasificación/permanencia ya en verde. GREEN tras agregar `DEFAULT_ATTEMPTS = 2`, el parámetro `attempts`
  validado, el bucle de reintento en `generate` y el mensaje `failed after N attempts`:
  **115 passed**. El test de kill de grupo (`test_a_timeout_kills_the_process_group_and_drains_pipes`) falló
  una vez por carrera real de procesos ajenos al cambio y pasó al re-ejecutarlo solo y en la suite completa.
- **L4** — **T2 (documentación) implementado.** `pyproject.toml` describe el marker `live` como backends reales (API de Claude
  con `ANTHROPIC_API_KEY` o CLI local de `opencode`) y ADR-0007 suma la decisión 9 (clasificación y reintento)
  más su consecuencia de latencia. Sin cambios de configuración por entorno.

- **L5** — **Entrega en un solo commit.** El gate `herdr-flow` de este worktree bloquea cada commit hasta que
  qa y review aprueban el diff exacto y pide `git add -A && git commit` (nunca `--no-verify`); con tres
  commits, cada uno dejaría un diff nuevo sin revisar. Por eso T1 y T2 van en un único commit convencional y
  el PR lo declara. Además, `test_a_timeout_kills_the_process_group_and_drains_pipes` resultó flaky (falló en
  2 de 4 corridas de la suite): comprobaba `killpg` de inmediato y un nieto muerto puede seguir como zombi un
  instante; ahora espera hasta 5 s a que el sistema lo recoja. Sin cambio de producción.

- **L6** — **Verificación final.** `uv run pytest -q` → **1595 passed, 32 deselected**; live
  `uv run pytest -q -m live tests/live/test_opencode_language_model_live.py` → **3 passed**. Ruff:
  `ruff check` y `ruff format --check` sobre los 3 archivos Python tocados → limpio. Repo completo:
  `ruff check .` → 10 errores preexistentes en archivos fuera de las superficies y
  `ruff format --check .` → 106 archivos, también desde el commit base (verificado con
  `git show HEAD:<archivo>` sobre el test del adaptador): el repo está formateado con una versión anterior
  de ruff (0.17.3 instalada). No se reformatea fuera de las superficies permitidas.

- **L7** — **Ronda de corrección 1 (gate herdr-flow: qa=pass, review=fail, 7 hallazgos).** Aplicados: (a) se
  revirtió el re-export en `llm/__init__.py` y `LanguageModelEmptyResponseError` queda interna al adaptador
  (la jerarquía pública vive en `errors.py`, fuera de las superficies permitidas); (b) el error transitorio de
  JSON inválido ya no incluye el preview de la salida del modelo, así que el mensaje no puede filtrar contexto;
  (c) el reintento se expresa con un `for` sobre `attempts` y el último error; (d) `FakeRunner` normaliza su
  forma por defecto en `__post_init__`, con `script` como única fuente de verdad; (e) dos nombres de test se
  acortaron para que la firma entre en una línea; (f) se mantiene el arreglo de flakiness del test de timeout y
  se nombra en el cuerpo del commit y del PR. Verificación: 115 unit tests del adaptador en verde, ruff limpio
  en los archivos tocados.
