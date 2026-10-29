# ADR-0004: Embeddings locales en el MVP

- **Estado:** aceptada
- **Fecha:** 2026-10-29
- **Decisión relacionada:** A4 en [architecture.md](../architecture.md#9-decisiones-abiertas)

## Contexto

La evaluación (D4) solo sirve si dos corridas sobre el mismo corpus dan el mismo resultado. Un modelo de embeddings servido por API puede cambiar sin aviso. El MVP es local y está todo en inglés ([ADR-0001](0001-idioma-mvp-ingles.md)).

## Decisión

El adaptador de `Embedder` usa un modelo local. Modelo inicial: **`BAAI/bge-base-en-v1.5`** (768 dimensiones, entrada máxima de 512 tokens), de la misma familia que el reranker `bge-reranker-base`.

## Consecuencias

- Sin costo por consulta ni dependencia de red para indexar o consultar.
- La primera ejecución descarga el modelo; las siguientes funcionan sin conexión.
- **El chunker respeta el límite de 512 tokens** del modelo; un chunk más largo se trunca en silencio y pierde información. Los tests del chunker validan este límite.
- **El índice guarda el identificador del modelo.** Si al consultar no coincide con el `Embedder` configurado, el sistema falla con un error explícito en lugar de comparar vectores incompatibles. Cambiar de modelo implica reindexar con `ReindexCorpus`.
- Para la familia bge v1.5, la instrucción de consulta para recuperación es opcional; si se usa, el adaptador la aplica solo a las consultas y nunca a los documentos. Se decide con el dataset de evaluación, no a priori.
- Pendiente de verificar al implementar: la licencia del modelo y el rendimiento en CPU.
