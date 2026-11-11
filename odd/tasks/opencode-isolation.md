# Feature: opencode-isolation

## Objective

Aislar el proceso hijo `opencode` de la configuración global del usuario (instrucciones, plugins, skills,
commands y servidores MCP) y callar el ruido de carga de modelos que `poe2-rag ask` imprime en cada corrida,
sin romper la autenticación ni el comportamiento ya verificado del adaptador.

- **Estado:** en curso
- **Rama de trabajo:** `fix/opencode-isolation` (worktree de Herdr `opencodee`)
- **Superficie:** `src/poe2_rag/answering/adapters/outbound/llm/opencode_language_model.py`,
  `src/poe2_rag/answering/adapters/inbound/cli/**`, `src/poe2_rag/bootstrap.py` y sus tests

## Specs

- **S1** — "The child must load NO user-global opencode config: no global instructions, plugins, skills,
  commands or MCP servers. Recommended mechanism: point `XDG_CONFIG_HOME` (and any other opencode config-dir
  override you find documented, for example `OPENCODE_CONFIG_DIR`) at a private, empty, per-adapter temporary
  directory that the adapter creates and cleans up. Verify against the opencode docs/CLI which variables apply
  in v2.2.4."
- **S2** — "Authentication must keep working: credentials live in opencode's data dir (find where: for example
  `~/.local/share/opencode/auth.json` or the `XDG_DATA_HOME` equivalent). Do not copy, read, log or move
  credential files; only make sure the data dir is not overridden. If isolating the config makes auth
  impossible, stop and report."
- **S3** — "Also explicitly disable any remaining global surface the config schema allows (for example
  `"mcp": {}`, `"plugin": []`, `"instructions": []`) as defence in depth, not as the primary mechanism."
- **S4** — "Silence noise for the CLI only, at the composition root or inbound CLI, not in domain code: disable
  transformers/huggingface_hub progress bars and the unauthenticated-HF warning (for example
  `HF_HUB_DISABLE_PROGRESS_BARS`, transformers verbosity set to error), without hiding real errors."
- **S5** — "Keep the existing behavior: timeout, process-group kill, citation validation and error mapping
  unchanged."
- **S6** — "TDD with RED/GREEN evidence. Unit tests with the fake runner: the child env sets the config-dir
  override to a directory that exists and is empty, does not override the data dir, and the config content
  carries the defence-in-depth keys; the temp dir is removed afterwards (also on timeout and error)."
- **S7** — "The live test (`tests/live/test_opencode_language_model_live.py`) still passes. Add a live assertion,
  skipped without `opencode` on PATH, that a run with isolation does not pick up a sentinel global instruction:
  create a throwaway config dir containing an `AGENTS.md` that says to answer with a fixed sentinel word, set it
  as the PARENT's config dir, and assert the answer does not contain the sentinel."
- **S8** — "A CLI test that `ask` output contains no "Loading weights" progress text."
- **S9** — "SOLID, dependency injection, specific exceptions, no silenced errors. English for code, tests and
  comments." / "Never touch, read or print the user's real `~/.config/opencode` contents or credentials in code or
  tests." / "Conventional Commits, one per task, no AI attribution or Co-Authored-By."
- **S10** — "Branch `fix/opencode-isolation`, ODD doc `odd/tasks/opencode-isolation.md`. Open a PR; do not merge."
  y "Verification: `uv run pytest -q` (report the count); `uv run pytest -q
  tests/live/test_opencode_language_model_live.py`; `uv run ruff check . && uv run ruff format --check .`; `uv run
  poe2-rag ask "What changed for Ritual in 0.5.5?"`: report latency, confirm there are no progress bars or HF
  warning, and confirm the answer is still cited."

## Non-goals

- Tocar `domain/` o `application/`: el aislamiento es del adaptador y del composition root.
- Cambiar el agente dedicado, el prompt, el timeout, el kill de grupo ni la validación de citas (S5).
- Aislar el directorio de **datos** (ahí viven las credenciales) o el de estado/caché.
- Modificar los adaptadores que cargan modelos (`bge_embedder.py`, `cross_encoder_reranker.py`): no están en la
  superficie permitida y el silenciado se resuelve antes, en el composition root.

## Acceptance criteria

- El entorno del hijo fija `OPENCODE_CONFIG_DIR` y `XDG_CONFIG_HOME` a un directorio privado, existente y vacío,
  y **no** fija `XDG_DATA_HOME`, `HOME` ni `OPENCODE_DB`, así que las credenciales siguen encontrándose.
- El directorio privado se borra en toda salida: éxito, timeout y error de respuesta.
- El contenido de `OPENCODE_CONFIG_CONTENT` lleva las claves de defensa en profundidad que el esquema de v2.2.7
  acepta de verdad (ver L4).
- `cli._default_container()` fija las tres variables del silenciado antes de construir el container, y
  `build_container` no muta `os.environ`: sigue siendo función pura del mapping que recibe.
- Una corrida real no recoge un `AGENTS.md` global centinela (S7).
- `uv run pytest -q` verde, `ruff check` y `ruff format --check` limpios.

## Tasks

| ID | Specs | Ruta | Estado |
|---|---|---|---|
| T1 | S10 | parent | ✅ rama `fix/opencode-isolation` + documento ODD |
| T2 | S6, S8 | parent | ✅ tests RED (7 fallos + ImportError) → GREEN |
| T3 | S1, S3, S5 | parent | ✅ aislamiento en `opencode_language_model.py` |
| T4 | S4 | parent | ✅ silenciado en `cli._default_container()`; `build_container` queda puro |
| T5 | S7 | parent | ✅ aserción live del centinela: **2 passed in 28.32 s** |
| T6 | S10 | parent | ✅ ADR-0007 (decisión 7 y 8) |
| T7 | S10 | parent | ✅ verificación completa (L12, L13) |

## Log

- **L1** — Pedido original (verbatim, resumido a su encabezado porque el cuerpo está citado en S1–S10):
  "# Task: isolate the opencode child from the user's global config and silence model-loading noise".
- **L2** — Evidencia de la fuga (dada por el humano): la memoria de Engram del usuario registró los prompts de
  fixture del test live (`<source chunk_id="patch_notes-0-2-0-spark">...`) como prompts de usuario, así que
  plugins/MCP globales corrieron dentro del agente "aislado".
- **L3** — Versión real instalada: `opencode v2.2.7` (el pedido decía v2.2.4). Variables presentes en el binario
  (`strings`): `OPENCODE_CONFIG_DIR`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `OPENCODE_CONFIG`,
  `OPENCODE_CONFIG_CONTENT`, `OPENCODE_DISABLE_PROJECT_CONFIG`, `OPENCODE_DB`.
- **L4** — **Mecanismo elegido y verificado conductualmente contra el binario real (v2.2.7).** `opencode debug
  paths` con el override muestra `config /tmp/<probe>/config/opencode` y deja `data
  /home/<user>/.local/share/opencode` intacto. `opencode debug config --standalone` lista **una sola** fuente,
  el directorio privado; el `opencode.json` global del usuario aparece **0** veces. Contraste importante: sin
  `--standalone` el comando habla con el servicio de fondo ya corriendo con el entorno del usuario y **sí**
  lista el config global — el adaptador ya usa `--standalone`, y por eso el aislamiento aplica.
- **L5** — **S2 resuelto sin copiar credenciales.** En v2 las credenciales viven en SQLite dentro del data dir
  (`~/.local/share/opencode/opencode.db`), no en el config dir; `XDG_DATA_HOME` y `OPENCODE_DB` son los únicos
  overrides documentados. Prueba: `opencode auth list` devuelve la misma cantidad de entradas (4) y exit 0 con
  `XDG_CONFIG_HOME`/`OPENCODE_CONFIG_DIR` apuntando a un directorio vacío. No se leyó ni imprimió ningún
  archivo de credenciales.
- **L6** — **S3 corregido por evidencia, dos veces.** Los ejemplos del pedido no son los que el esquema de
  v2.2.7 honra: `plugin` está deprecado y el esquema lo descarta (`"plugin": []` desaparece del config
  resuelto); `plugins` e `instructions` son **aditivos**, así que `[]` no desactiva nada de lo heredado; y
  `"mcp": {"servers": {}}` es descartado en 2.2.7 mientras `"mcp": {}` sí se conserva. La forma que sí
  desactiva plugins, `"plugins": ["-*"]`, **rompe el adaptador**: la corrida real falla con
  `Agent not found: "poe2-answer"`, porque ese comodín también apaga el plugin incorporado que registra los
  agentes inyectados por `OPENCODE_CONFIG_CONTENT`. Se probó con las cuatro combinaciones de variables
  (`content` sola, `+OPENCODE_CONFIG_DIR`, `+XDG_CONFIG_HOME`, ambas) y con tres contenidos: solo `["-*"]`
  falla, `mcp: {}` e `instructions: []` no. Claves implementadas: `"mcp": {}` e `"instructions": []`, ambas
  verificadas como no rompientes; `plugins` queda **ausente a propósito**. El aislamiento real lo da el
  directorio vacío, y la superficie de plugins queda cubierta solo por él.
- **L7** — **S4: el knob correcto y por qué el composition root alcanza.** `transformers/utils/logging.py:55`
  calcula `_tqdm_active = not hf_hub_utils.are_progress_bars_disabled()` **en tiempo de import**, y
  `huggingface_hub/constants.py:273` lee `HF_HUB_DISABLE_PROGRESS_BARS` también al importar. La barra "Loading
  weights" sale de `transformers/core_model_loading.py:1789` (`tqdm(..., desc="Loading weights")`), es decir,
  del wrapper de transformers gateado por esa misma bandera. `bge_embedder.py` y `cross_encoder_reranker.py` no
  importan nada pesado a nivel de módulo (verificado), así que fijar el entorno en `cli._default_container()`
  llega antes de que `transformers` se importe. Aviso de HF sin autenticar: lo emite el logger
  `huggingface_hub`, que `HF_HUB_VERBOSITY` controla (`huggingface_hub/utils/logging.py:56`); en `error` se
  calla el aviso y los errores siguen visibles.
- **L8** — El worktree no tiene `data/`, así que el `poe2-rag ask` real del bloque Verification se corre
  apuntando `POE2_RAG_RAW_DIR`/`POE2_RAG_PROCESSED_DIR`/`POE2_RAG_INDEX_DIR` al worktree principal del mismo
  clon (`/home/niico09/Desarrollo/Personales/PoE2-RAG/data`), que está en el mismo commit base. No se copia
  nada dentro del worktree: los subdirectorios de `data/` están en `.gitignore` pero copiarlos ensuciaría el
  diff que revisa el gate, y borrarlos después exigiría un `rm -rf` que el flujo prohíbe.
- **L9** — **RED/GREEN del adaptador.** RED: 7 fallos (`test_the_child_reads_its_config_from_a_private_empty_
  directory`, `test_the_private_directory_is_a_fresh_temporary_directory`,
  `test_each_run_gets_its_own_config_directory`, `test_the_config_content_carries_only_the_harmless_defence_
  in_depth_keys`, y los tres de limpieza) más el `ImportError: cannot import name
  'silence_model_loading_noise'` del test de CLI. GREEN: 41 passed en los dos módulos; suite completa **1579
  passed, 31 deselected** (antes 1552/30).
- **L10** — **Defecto propio encontrado por el test live, no por los unitarios.** La primera versión incluía
  `"plugins": ["-*"]` como defensa en profundidad y el test live falló en 1 s con `Agent not found:
  "poe2-answer"` y stderr vacío. Causa: ese comodín también apaga el plugin incorporado que registra los
  agentes inyectados. Se probó con `debug config --standalone` (el agente aparece en las cuatro combinaciones
  de variables, así que el diagnóstico no era el directorio) y después con corridas reales: contenido mínimo
  → funciona; `+plugins: ["-*"]` → `Agent not found`; `+mcp: {}` → funciona. Arreglo: se quita `plugins` del
  contenido y el test unitario pasa a **prohibir** esa clave, para que nadie la reponga creyendo que es
  defensa. GREEN tras el arreglo: **2 passed in 28.32 s** en el live.
- **L11** — **Formato.** `ruff check .` no está limpio en el repo base (10 errores en archivos ajenos a este
  cambio) y `ruff format --check .` tampoco (107 archivos). Diferencial contra `git archive HEAD`: los mismos
  dos archivos tocados (`bootstrap.py` y `test_opencode_language_model.py`) ya estaban sin formatear en HEAD, y
  el informe de `ruff format --check` es idéntico salvo los números de línea que corrieron por las líneas
  agregadas. El cambio no agrega deuda de formato; los archivos nuevos están limpios.
- **L12** — **Corrida real de `ask` con aislamiento.**
  `uv run poe2-rag ask "What changed for Ritual in 0.5.5?"` → **exit 0, 32.7 s de pared, stderr de 0 bytes**,
  respuesta citada con 5 citas verbatim contra `patch_notes:patch_notes:0-5-5@0.5.5#ritual-changes`, parche
  resuelto `0.5.5e`. Sin barras de progreso y sin aviso de HF en la salida.
- **L13** — **Prueba de sensibilidad del silenciado** (para que el stderr vacío de L12 no sea vacuo: tqdm se
  apaga solo cuando no hay TTY, así que había que descartar esa explicación). Cargando
  `BAAI/bge-base-en-v1.5` con `sentence_transformers`: **sin** las tres variables → 286 bytes de stderr y 2
  líneas de ruido, con `Loading weights: 100%|...` y `Warning: You are sending unauthenticated requests to the
  HF Hub. Please set a HF_TOKEN...`; **con** las tres variables → 0 bytes, 0 líneas. El aviso que reportó el
  humano es exactamente ese, y `HF_HUB_VERBOSITY=error` lo calla.
- **L14** — **Ronda 1 de review: nueve hallazgos (3 qa + 6 review), todos aplicados.**
  (a) **mayor** — el silenciado descansaba en una premisa sin test: que el entorno se fija antes de que se
  importen las librerías. Ahora hay dos tests en subproceso que importan `poe2_rag.cli` y afirman que
  `transformers` y `huggingface_hub` **no** están en `sys.modules` ni antes ni después de
  `_default_container()`, y que las tres variables quedan fijas con las librerías todavía sin importar. Un
  revert del silenciado o una dependencia nueva que las arrastre ahora rompe la suite.
  (b) **mayor** — el test live del centinela podía pasar en vacío, porque depende de que el modelo obedezca una
  instrucción. Se agregó `test_the_child_config_resolves_to_the_private_directory_only`, determinista: corre
  `opencode debug config --standalone` con el entorno real de `_child_environment(...)` y afirma que la única
  fuente es el directorio privado, que la raíz global no aparece y que el `default_agent` del
  `opencode.json` global tampoco. El centinela queda como segunda capa.
  (c) **mayor** — `test_ask_output_carries_no_model_loading_noise` era tautológico (FakeRunner no carga nada).
  Se reemplazó por `test_ask_prints_exactly_the_answer_patch_and_citations`, que fija la salida **completa** del
  comando, y la garantía del silenciado quedó en los dos tests de cadena de importación.
  (d) **mayor** — `build_container(environ)` mutaba `os.environ`, un efecto de proceso que ningún argumento
  declaraba. Se sacó de ahí: `build_container` volvió a ser función pura de su mapping y el silenciado se llama
  en `cli._default_container`, que es la entrada real del proceso. Esto tocó `src/poe2_rag/cli.py`, fuera de la
  lista original de superficies, porque es el único lugar donde el efecto es legítimo.
  (e) **menor** — `test_silencing_keeps_errors_visible` era tautológico; se borró.
  (f) **menor** — la ADR documentaba una escotilla inexistente (exportar la variable antes del CLI); ahora dice
  que no hay reactivación desde afuera.
  (g) **menor** — el test nuevo rompía la convención de espejo del repo: se movió a
  `tests/unit/answering/adapters/inbound/test_ask_cli_output.py` (plano, junto a `test_ask_cli_command.py`).
  (h) **menor** — la justificación de la decisión 7 estaba escrita cuatro veces en el módulo; quedó en el
  docstring de `_child_environment` y una línea de referencia en el comentario del config.
  (i) **menor (qa)** — `shutil.rmtree` dejó de estar envuelto en `contextlib.suppress(OSError)`: ahora emite un
  `RuntimeWarning` con la ruta que quedó, sin reemplazar el error propio de la corrida, y un test lo pina
  monkeypatcheando `rmtree` para que falle.
  Verificación de la ronda: suite **1580 passed, 32 deselected**; live **3 passed in 18.65 s**; `ruff check`
  limpio en los archivos tocados; `ruff format --check` marca los mismos tres archivos que ya marcaba en HEAD,
  así que el cambio no agrega deuda.
- **L15** — **Ronda 2 de review: seis hallazgos (3 qa + 3 review), todos aplicados.**
  (a) **mayor** — `test_ask_prints_exactly_the_answer_patch_and_citations` duplicaba el contrato que
  `test_ask_cli_command.py` ya fija (mismo bloque de salida, mismos builders). Se borró junto con sus
  `make_chunk`/`FakeRunner`/`make_runner`: el módulo nuevo quedó con lo único que aporta de propio.
  (b) **menor** — `warnings.warn` puede levantar bajo `-W error` y reemplazar el `LanguageModelTimeoutError` en
  vuelo. Ahora va dentro de `contextlib.suppress(Exception)` y con `stacklevel=1` explícito (el default; 2
  apuntaría al `__exit__` de contextlib). Se agregó el caso que faltaba:
  `test_a_cleanup_warning_never_replaces_the_runs_own_failure` corre con `simplefilter("error")` y un runner
  en timeout, y afirma que lo que llega es el timeout.
  (c) **menor** — `_agent_config()` del test live era una copia a mano que ya había divergido (le faltaba el
  bloque `agent`). Se extrajo `_agent_configuration()` en el adaptador y el test live la importa, así que no
  puede divergir.
  (d) **menor** — `run_probe()` armaba el container real una vez por test. Ahora un fixture `scope="module"` lo
  cachea y el subproceso recibe un entorno explícito sin las tres variables, así que el `monkeypatch` de
  función ya no hace falta.
  (e) **menor (qa)** — el test de limpieza fallida dejaba un directorio 0700 real por corrida en `$TMPDIR`.
  Ahora redirige `tempfile.tempdir` a `tmp_path`. Verificado: después de la suite completa quedan **0**
  directorios `poe2-rag-opencode-*` en `/tmp`.
  (f) **menor (qa)** — el documento ODD seguía diciendo que el silenciado vive en `build_container`; el
  criterio de aceptación y la fila T4 ahora nombran `cli._default_container()` y la pureza de
  `build_container`. Se agregó además `test_build_container_does_not_touch_the_process_environment`, que pina
  esa pureza: era exactamente la clase de defecto que esta ronda corrigió.
  Verificación de la ronda: suite **1581 passed, 32 deselected**; live **3 passed in 40.75 s**; `ruff check`
  limpio; `ruff format --check` marca los mismos tres archivos que en HEAD; **0** directorios privados
  sobrantes en `/tmp` después de correr la suite.
