# ADR-0001: El MVP opera íntegramente en inglés

- **Estado:** aceptada
- **Fecha:** 2026-10-29
- **Decisión relacionada:** A2 en [architecture.md](../architecture.md#9-decisiones-abiertas)

## Contexto

Las fuentes de datos de *Path of Exile 2* (poe2db, wiki, notas de parche oficiales) están en inglés. Admitir consultas en otro idioma exigiría embeddings multilingües y un mapeo de términos para BM25, porque la búsqueda léxica no encuentra `more` si la consulta dice `más`.

## Decisión

En el MVP, el corpus, las consultas, las respuestas y el dataset de evaluación están en inglés.

## Consecuencias

- Se puede usar un modelo de embeddings monolingüe, más chico y con mejor rendimiento en inglés.
- BM25 trabaja sobre los términos originales del juego, sin diccionario de traducción.
- El dataset de evaluación se escribe en inglés.
- Admitir consultas en español más adelante requiere un nuevo ADR y afecta a los puertos `Embedder` y `LexicalIndex` (o a un traductor de consultas en la etapa de análisis). El modelo de dominio no cambia.
