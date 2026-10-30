# ADR-0004: Embeddings locales en el MVP

- **Estado:** aceptada
- **Fecha:** 2026-10-29
- **Decisión relacionada:** A4 en [architecture.md](../architecture.md#9-decisiones-abiertas)

## Contexto

La evaluación (D4) solo sirve si dos corridas sobre el mismo corpus dan el mismo resultado. Un modelo de embeddings servido por API puede cambiar sin aviso. El MVP es local y está todo en inglés ([ADR-0001](0001-idioma-mvp-ingles.md)).

## Decisión

El adaptador de `Embedder` usa un modelo local. Modelo inicial: **`BAAI/bge-base-en-v1.5`** (768 dimensiones, entrada máxima de 512 tokens), de la misma familia que el reranker `bge-reranker-base`.

Reglas que el adaptador implementa:

1. **Un solo adaptador para los dos puertos del embedder.** `Embedder.embed(text)` responde del lado de la
   consulta e `ingestion` expone `DocumentEmbedder.embed_documents(texts)` del lado del corpus. El modelo se
   carga la primera vez que se usa, no al construir el adaptador: la carga es un descargable de cientos de
   megabytes y no puede ocurrir al importar el módulo.
2. **La instrucción de recuperación se aplica solo a las consultas.** El adaptador antepone
   `Represent this sentence for searching relevant passages: ` al texto de la consulta y nunca al de un
   documento. Es la instrucción con la que se entrenó la familia `bge` v1.5 para recuperación; aplicarla a
   ambos lados, o a ninguno, deja consulta y documentos en regiones distintas del espacio. La instrucción
   queda como parámetro del constructor, así el dataset de evaluación todavía puede comparar con y sin ella.
3. **Vectores normalizados L2 y espacio coseno.** La normalización la hace el modelo
   (`normalize_embeddings=True`) y la colección de Chroma usa `space=cosine`, de modo que el puntaje denso es
   `1 - distancia` y no depende de la magnitud del vector.
4. **Sin truncado silencioso.** El modelo corta su entrada a 512 tokens sin avisar, así que el adaptador
   cuenta tokens con el truncado apagado y expone `overflowing_indices(texts)`. `ReindexCorpus` convierte esas
   posiciones en `truncated_chunk_ids` del informe: la corrida no falla, porque el índice léxico sigue leyendo
   el texto completo y perder la vía densa de un chunk largo sería peor que un punto ciego informado.
5. **Identidad del modelo persistida.** La colección guarda `embedding_model` y `dimension` en sus metadatos.
   Consultar con un embedder de otro modelo u otra dimensión falla con un error explícito en vez de comparar
   vectores incompatibles. La dimensión también se verifica contra el modelo cargado, así un `model_id`
   distinto con la dimensión por defecto no puede escribir un índice con el ancho equivocado.

## Consecuencias

- Sin costo por consulta ni dependencia de red para indexar o consultar.
- La primera ejecución descarga el modelo; las siguientes funcionan sin conexión.
- **Nada garantiza que un chunk entre en los 512 tokens del modelo.** No existe un chunker por tokens: cada
  chunk es una sección de primer nivel del documento, y una sección de notas de parche de PoE2 lo supera con
  facilidad. El modelo corta en silencio, así que el embedder reporta las posiciones que desbordan y el
  reindexado las expone en `truncated_chunk_ids`; el índice léxico sigue leyendo el texto completo.
- **El índice guarda el identificador del modelo.** Si al consultar no coincide con el `Embedder` configurado,
  el sistema falla con un error explícito en lugar de comparar vectores incompatibles. Cambiar de modelo
  implica reindexar con `ReindexCorpus`.
- La licencia del modelo quedó verificada al implementar: `bge-base-en-v1.5` se publica bajo licencia MIT, sin
  costo para uso comercial. El rendimiento en CPU de un corpus local chico es aceptable; los lotes los decide
  el adaptador (`batch_size` inyectable), no el caso de uso.
