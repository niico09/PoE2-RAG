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

## Alternativas descartadas

- **Seguir con la API de Anthropic y facturar:** descartada por decisión del operador, no por una medición
  de calidad. El adaptador de Claude se conserva detrás del mismo puerto: volver a él es cambiar una
  variable de entorno, sin tocar código.
- **Invocar el CLI sin timeout** (confiando en su propio corte): descartada por el cuelgue observado de más
  de 5 minutos. Sin timeout propio, un cuelgue del CLI es un cuelgue de `ask`.
- **Reusar el agente global del usuario:** descartada. Arrastra un prompt largo, herramientas y permisos que
  el caso de uso no necesita, y deja abierta la posibilidad de que el modelo toque archivos al responder
  una pregunta.
- **Escribir el JSON del agente en un archivo del proyecto** (`OPENCODE_CONFIG` o `OPENCODE_CONFIG_DIR`, que
  también funcionan): descartada a favor de `OPENCODE_CONFIG_CONTENT` porque no deja un archivo que haya que
  versionar, limpiar ni sincronizar, y el aislamiento queda contenido en una sola invocación.
- **Pasar `--auto` para que no pregunte permisos:** descartada explícitamente. Con el agente dedicado sin
  herramientas no hay permisos que aprobar, y `--auto` ampliaría el radio de acción justo donde queremos lo
  contrario.

## Consecuencias

- **Latencia de subproceso.** Cada respuesta paga el arranque del CLI: medido entre 4 y 10 segundos en
  corridas reales, contra una llamada directa por API. Para `ask` interactivo es aceptable; para `eval` con
  muchas corridas hay que tenerlo en cuenta al leer los tiempos.
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
