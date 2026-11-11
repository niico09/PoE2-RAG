# ADR-0007: Generación vía OpenCode CLI

- **Estado:** aceptada
- **Fecha:** 2026-11-09
- **Decisión relacionada:** A5 en [architecture.md](../architecture.md#9-decisiones-abiertas)
- **Modifica:** [ADR-0005](0005-llm-por-api.md)

## Contexto

[ADR-0005](0005-llm-por-api.md) eligió la API de Anthropic con citas nativas para la generación: es la
opción con mejor fidelidad, porque el proveedor devuelve qué fragmento de qué documento respalda cada
afirmación (D2, D3). El costo es por consulta y requiere una credencial con facturación.

El operador del proyecto no va a pagar facturación por API. Tiene una suscripción **OpenCode Go** y el CLI
`opencode` (v2.2.4, `/usr/bin/opencode`), que ya usa para otras tareas. Un spike manual con
`opencode run -m opencode-go/deepseek-v4.1-flash` devolvió JSON correcto con 5 de 5 citas verbatim, pero
también mostró dos problemas que esta decisión tiene que resolver, no ignorar:

1. **Sin timeout propio.** Una segunda corrida idéntica quedó colgada más de 5 minutos sin salida y sin
   cortarse sola. `ask` no puede heredar ese comportamiento.
2. **Agente global.** El CLI corría con el agente global del usuario (`gentle-orchestrator`), con su prompt
   completo y todas sus herramientas: lento e inseguro para responder una pregunta del dominio.

## Decisión

1. **La generación usa el CLI `opencode` por defecto**, a través de `OpenCodeLanguageModel`, un tercer
   adaptador detrás del mismo puerto `LanguageModel`. El dominio y la aplicación no cambian: el puerto ya
   alcanzaba.
2. **Se invoca como subproceso con timeout duro.** La ejecución pasa por un `ProcessRunner` inyectable
   (Protocol). La implementación por defecto arranca el proceso en su propio grupo
   (`start_new_session=True`) y, al vencer `POE2_RAG_LLM_TIMEOUT`, mata **todo el grupo** (no solo el
   proceso padre, porque `opencode` puede tener hijos) y levanta `LanguageModelTimeoutError`. Esa es la
   mitigación directa del cuelgue del punto 1 del contexto.
3. **Se corre con un agente dedicado sin herramientas, inyectado por entorno.** La CLI toma su
   configuración de `OPENCODE_CONFIG_CONTENT`, así que el adaptador arma un agente `poe2-answer` con
   `mode: primary`, un prompt de sistema corto, `tools: {"*": false}` y `steps: 1`, y lo selecciona con
   `--agent`. Se agrega `OPENCODE_DISABLE_PROJECT_CONFIG=1` para no levantar la configuración del
   repositorio, y `--standalone` para usar un servidor privado en vez del servicio de fondo compartido.
   Nunca se pasa `--auto`. Esto resuelve el punto 2 del contexto.
4. **El entorno del hijo se filtra, no se hereda entero.** Se pasan solo las variables que el CLI necesita
   para encontrarse a sí mismo y a su propia sesión (`PATH`, `HOME`, `XDG_*`, locale y temporales) más las
   dos de configuración. Un proceso de terceros no tiene por qué leer `ANTHROPIC_API_KEY` ni ninguna otra
   credencial exportada.
5. **Las citas se validan en el adaptador contra el contexto entregado.** La API de OpenCode no tiene citas
   nativas: el modelo devuelve `quoted_text` copiado y el adaptador verifica que sea una subcadena del chunk
   citado. La pertenencia del `chunk_id` al contexto la sigue validando el dominio en `Answer.build`, que ya
   rechazaba citas a chunks no entregados; no se duplica esa regla.
6. **El proveedor es configurable y el default no necesita credencial de Anthropic.**
   `POE2_RAG_LLM_PROVIDER` acepta `opencode` (default) o `claude`. El modelo por defecto depende del
   proveedor (`opencode-go/deepseek-v4.1-flash` y `claude-sonnet-5-5`), y `POE2_RAG_LLM_MODEL` sigue
   ganando. `ANTHROPIC_API_KEY` se exige **solo** cuando el proveedor es `claude`.
7. **El hijo no lee la configuración global del usuario.** `OPENCODE_CONFIG_CONTENT` se **fusiona** con lo
   que la CLI encuentre en disco, y el `AGENTS.md` global, los `plugins/`, `skills/`, `commands/` y los
   servidores MCP del usuario son parte de esa fusión: corrían dentro de un agente que debía tener las
   herramientas apagadas. Por eso cada corrida crea un directorio temporal privado y vacío y apunta ahí
   `OPENCODE_CONFIG_DIR` — que en OpenCode v2 **reemplaza** la raíz global de configuración — y también
   `XDG_CONFIG_HOME`, la raíz que reemplaza. El directorio se borra en toda salida, timeout y error
   incluidos. El directorio de **datos** no se toca: ahí viven las credenciales (SQLite en
   `$XDG_DATA_HOME/opencode`), así que `HOME`, `XDG_DATA_HOME` y `OPENCODE_DB` se heredan y el login sigue
   funcionando. Como segunda capa se envían `mcp: {}` e `instructions: []`, que son aditivos y no pueden
   romper nada. La superficie de plugins **no** tiene forma explícita segura: `plugins: ["-*"]` apaga también
   el plugin incorporado que registra los agentes inyectados, y la corrida falla con `Agent not found`. Esa
   superficie queda cubierta solo por el reemplazo de la raíz de configuración.
8. **El ruido de carga de modelos se calla en el composition root.** `ask` carga un embedder y un
   reranker, y ambos imprimen barras de progreso y un aviso de Hub sin autenticar.
   `cli._default_container` fija `HF_HUB_DISABLE_PROGRESS_BARS=1`, `HF_HUB_VERBOSITY=error` y
   `TRANSFORMERS_VERBOSITY=error` antes de construir el container, porque `transformers` y
   `huggingface_hub` leen esas banderas al importarse. No va dentro de `build_container`: esa función es un
   valor de su mapping y no tiene por qué mutar el proceso. El nivel es `error`: una barra y un aviso son
   ruido, una descarga fallida no.
9. **Una salida vacía o no JSON se reintenta una vez; el resto no.** La corrida base de 20 preguntas falló
   en 2 de 38 llamadas con `model output is not valid JSON` y preview vacío, en preguntas distintas cada
   vez, y las mismas preguntas respondieron bien en la otra corrida: la falla es transitoria y no depende
   del insumo. Por eso el adaptador clasifica las respuestas con una subclase propia,
   `LanguageModelEmptyResponseError(LanguageModelResponseError)`: sin evento de texto, texto vacío o en
   blanco y texto que no es JSON válido son transitorios y se reintentan una vez con el mismo prompt y un
   timeout completo nuevo. Un JSON válido que viola el schema, una cita malformada o fuera del contexto
   entregado, un exit distinto de cero, un timeout y los errores de configuración o autenticación son
   permanentes y no se reintentan. La cantidad de intentos es `attempts` en el constructor (`2` por
   defecto, entero >= 1); no se agrega configuración por entorno. Si el reintento también falla, el error
   dice cuántos intentos se gastaron, sin prompt ni contexto en el mensaje: el error transitorio
   nombra la falla (sin evento de texto o JSON inválido) y no echa la salida cruda del modelo, que
   podría citar el contexto entregado.

## Alternativas descartadas

- **Seguir con la API de Anthropic y facturar:** descartada por decisión del operador, no por una medición
  de calidad. El adaptador de Claude se conserva detrás del mismo puerto: volver a él es cambiar una
  variable de entorno, sin tocar código.
- **Invocar el CLI sin timeout** (confiando en su propio corte): descartada por el cuelgue observado de más
  de 5 minutos. Sin timeout propio, un cuelgue del CLI es un cuelgue de `ask`.
- **Reusar el agente global del usuario:** descartada. Arrastra un prompt largo, herramientas y permisos que
  el caso de uso no necesita, y deja abierta la posibilidad de que el modelo toque archivos al responder
  una pregunta.
- **Escribir el JSON del agente en un archivo del proyecto** (`OPENCODE_CONFIG`): descartada a favor de
  `OPENCODE_CONFIG_CONTENT` porque no deja un archivo que haya que versionar, limpiar ni sincronizar. Se usa
  `OPENCODE_CONFIG_DIR`, en cambio, para algo distinto: apuntarlo a un directorio temporal vacío es el único
  mecanismo que **quita** la raíz global del usuario, y por eso es parte de la decisión 7 y no del transporte
  del agente.
- **Confiar en `OPENCODE_DISABLE_PROJECT_CONFIG=1` para el aislamiento:** descartada. Esa bandera saltea la
  configuración **del proyecto**, no la global: el `AGENTS.md` del usuario se sigue leyendo. No existe una
  bandera documentada que lo desactive; la única forma documentada de no leerlo es apuntar la raíz de
  configuración a un directorio que no lo tenga.
- **Pasar `--auto` para que no pregunte permisos:** descartada explícitamente. Con el agente dedicado sin
  herramientas no hay permisos que aprobar, y `--auto` ampliaría el radio de acción justo donde queremos lo
  contrario.

## Consecuencias

- **Latencia de subproceso.** Cada respuesta paga el arranque del CLI: medido entre 4 y 10 segundos en
  corridas reales, contra una llamada directa por API. Para `ask` interactivo es aceptable; para `eval` con
  muchas corridas hay que tenerlo en cuenta al leer los tiempos. Un reintento paga otro arranque completo.
- **El reintento acota una falla observada, no la elimina.** Si las respuestas vacías fueran independientes,
  un 5 % por llamada baja a ~0,25 % con dos intentos, pero el timeout es por intento: cada uno recibe el
  `POE2_RAG_LLM_TIMEOUT` completo, así que el peor caso de `ask` duplica ese plazo.
- **Sin citas nativas.** La fidelidad de las citas ya no la garantiza el proveedor sino el prompt más la
  validación del adaptador: una cita no verbatim o de otro chunk se rechaza, pero eso **detecta** el error,
  no lo evita. El riesgo de que el modelo invente valores numéricos (D2, D3) es mayor que con la API de
  Anthropic y debe mirarse en la evaluación.
- **Riesgo de cuelgue mitigado, no eliminado.** El timeout duro corta el proceso y levanta un error
  específico, así que `ask` nunca queda colgado; lo que no se puede garantizar es que el CLI responda
  siempre dentro del plazo. `POE2_RAG_LLM_TIMEOUT` existe para ajustarlo por entorno.
- **Dependencia de una suscripción y de un binario externo.** `opencode` tiene que estar en el `PATH` y
  logueado. No es una dependencia de `pyproject.toml` (no hay SDK), así que la detección de que falta es un
  error de ejecución, no un fallo de instalación.
- **Sin determinismo.** No hay control de `temperature` desde el adaptador; igual que con Claude, la
  evaluación de generación se promedia sobre varias corridas (D4).
- **El adaptador de Claude queda como segunda opción**, y **Ollama sigue siendo el plan B** detrás del mismo
  puerto: es el candidato natural si la latencia del subproceso o el costo de la suscripción dejan de
  cerrar, porque no agrega facturación por consulta y ya está discutido en ADR-0005.
- **Lo que se conserva de ADR-0005:** el puerto `LanguageModel`, la validación de citas en el dominio
  (`Answer.build`), la separación entre recuperación y generación en la evaluación, y el aislamiento del
  dominio. Lo que cambia es el proveedor por defecto y el mecanismo de citas.
- **Aislamiento con un costo de arranque despreciable.** Cada respuesta crea y borra un directorio temporal
  privado (un `mkdtemp` y un `rmtree`): nada frente al arranque del CLI. Lo que **no** se aísla son los
  directorios de estado y de caché, que siguen siendo los del usuario, ni el de datos, que es donde viven
  las credenciales.
- **El aviso de Hub sin autenticar deja de aparecer.** `HF_HUB_VERBOSITY=error` lo calla y deja visibles los
  errores de descarga y de pesos faltantes, porque ese es el nivel que se conserva. Las variables se fijan de
  forma incondicional en la entrada del CLI, así que **no hay escotilla desde afuera**: exportar
  `HF_HUB_DISABLE_PROGRESS_BARS=0` antes de invocar `poe2-rag` no reactiva las barras, y recuperarlas exige
  cambiar el código. Es deliberado: el contrato es que `ask` imprima la respuesta, no cómo va cargando.
