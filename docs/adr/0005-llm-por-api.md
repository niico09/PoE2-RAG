# ADR-0005: Generación con Claude por API en el MVP

- **Estado:** aceptada
- **Fecha:** 2026-10-29
- **Decisión relacionada:** A5 en [architecture.md](../architecture.md#9-decisiones-abiertas)

## Contexto

La generación es la etapa donde se decide la fidelidad: el modelo debe usar solo el contexto recuperado, citar cada afirmación y negarse cuando el contexto no alcanza. Los modelos locales chicos citan peor y tienden a inventar valores numéricos, el riesgo principal del dominio (D2, D3).

## Decisión

1. El adaptador de `LanguageModel` usa la API de Anthropic con el SDK oficial. Modelo inicial: **Claude Sonnet 5.5** (`claude-sonnet-5-5`).
2. **Las citas usan la función nativa de citas de la API**: cada chunk recuperado se envía como un bloque `document` con contenido propio y `citations: {enabled: true}`. La API devuelve qué fragmento de qué documento respalda cada parte de la respuesta, y el adaptador lo traduce a `Citation(chunk_id)` del dominio.
3. No se combina con salida estructurada (`output_config.format`): la API rechaza ambas funciones juntas. Las citas nativas tienen prioridad porque sostienen D3.
4. Se habilita el *fallback* del servidor ante rechazos del modelo (`fallbacks: "default"`), y el adaptador revisa `stop_reason` antes de leer el contenido.

El fine-tuning queda fuera del MVP. Si se hace más adelante, apunta al embedder o al reranker, nunca a cargar datos del juego en los pesos del generador (ver la discusión en [architecture.md](../architecture.md)).

## Alternativas descartadas

- **Nemotron 3.5 Lightning (NVIDIA, 30B con 3B activos, pesos abiertos, local con Ollama):** **candidato a segundo adaptador.** Ventajas: sin costo por consulta, `temperature` configurable (evaluación reproducible) y ajustable con LoRA. Desventajas: las citas dependen del prompt, no de la API, y como modelo chico tiene mayor riesgo de inventar valores. Se compara contra Claude con el mismo dataset de evaluación una vez que el MVP funcione.
- **Nemotron 3 Ultra (NVIDIA, 550B con 55B activos):** descartado. Por su tamaño también se usaría por API, no ofrece citas nativas y no es multimodal; la familia multimodal es Nano Omni, irrelevante para un corpus de texto.
- **Claude Opus 5.5:** más capaz, a un costo de alrededor del doble por token. Se reevalúa si la evaluación muestra problemas de fidelidad que Sonnet no resuelve.

## Consecuencias

- **Costo por consulta** y dependencia de red en la generación (la indexación sigue siendo local).
- **La API key se lee de la configuración del entorno** (`.env`, fuera del control de versiones) y solo la conoce el adaptador.
- **La generación no es determinista:** `claude-sonnet-5-5` rechaza valores no predeterminados de `temperature`. Para que la evaluación sea útil (D4):
  - la recuperación (determinista) se evalúa por separado de la generación;
  - las métricas de generación se promedian sobre varias corridas;
  - las respuestas de cada corrida se guardan para comparar entre versiones.
- El dominio valida igual las citas recibidas: una cita a un chunk que no se entregó invalida la respuesta, aunque venga de la API.
- **Formas verificadas** contra la documentación oficial (2026-10-30) y contra el SDK instalado (`anthropic` 2.3.0, sobre httpx2) al implementar el adaptador:
  - **Documento con contenido propio**, sin chunking: `{"type": "document", "source": {"type": "content", "content": [{"type": "text", "text": ...}, ...]}, "title": ..., "context": ..., "citations": {"enabled": true}}`. Cada bloque `text` es la unidad citable mínima, así que el adaptador parte el chunk en un bloque por línea no vacía y `cited_text` llega exacto; `context` se le muestra al modelo y nunca se cita.
  - **Citas en todos los documentos o en ninguno**: no se puede habilitar en un subconjunto de la petición.
  - **Respuesta citada:** un bloque citado es un bloque `text` con `citations`, cada una con `type: "content_block_location"`, `cited_text`, `document_index` (0-indexado sobre los documentos de la petición, en orden), `document_title`, `start_block_index` y `end_block_index` (exclusivo). El adaptador traduce `document_index` a `chunk_id`; un tipo de cita distinto o un índice fuera de rango es un error del adaptador, nunca una cita descartada.
  - **Fallback del servidor:** `fallbacks: "default"` con el encabezado beta `server-side-fallback-2026-07-01`, por `client.beta.messages.create(...)`, solo en la API de Claude. En Sonnet 5.5 el servidor reintenta únicamente los rechazos `cyber` y `frontier_llm`; cualquier otro rechazo vuelve igual con `stop_reason: "refusal"`. Por eso el adaptador revisa `stop_reason` antes de leer el contenido y trata el rechazo como error propio: el fallback mejora la disponibilidad, no reemplaza el manejo del rechazo.
  - **Sonnet 5.5 no acepta `temperature`, `top_p` ni `top_k`** (valores no predeterminados son `400`), ni prefill del asistente, y `thinking: {"type": "disabled"}` también es `400`. Se omite `thinking` y la profundidad se controla con `output_config: {"effort": ...}` (`low`, `medium`, `high`, `xhigh`, `max`): el ajuste de esfuerzo reemplaza a la temperatura como perilla de la corrida. Los bloques de razonamiento pueden aparecer en `content` y el adaptador los ignora al armar la respuesta.
  - **Citas y salida estructurada siguen siendo incompatibles:** el adaptador nunca envía `output_config.format` junto con `citations`.

