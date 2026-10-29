# ADR-0003: Chroma como vector store del MVP y codificación de la vigencia

- **Estado:** aceptada
- **Fecha:** 2026-10-29
- **Decisiones relacionadas:** A3 y A7 en [architecture.md](../architecture.md#9-decisiones-abiertas)

## Contexto

El puerto `VectorIndex` debe aplicar el filtro de vigencia **antes** de la fusión (D1). El MVP corre en local, con un solo usuario y un corpus chico.

## Decisión

1. **Chroma en modo embebido** (persistente en disco, sin servidor) es el adaptador de `VectorIndex` en el MVP.
2. **La versión se codifica como entero ordenable:** `major * 1_000_000 + minor * 1_000 + patch`.
   Ejemplo: `0.1.2` → `1_002`; `0.10.0` → `10_000`.
   Se descarta concatenar dígitos (`0.1.2` → `102`): cuando un componente llega a 10 aparecen colisiones (`0.1.10` y `0.11.0` dan ambos `110`) y el orden se rompe.
3. **Vigencia abierta con centinela:** un `valid_until` vacío se guarda como `2**31 - 1`, porque los filtros por metadatos no tratan bien los valores nulos.
4. **Filtro de vigencia** para una versión objetivo `v`:
   `valid_from_code <= v AND valid_until_code > v`.

La codificación es un detalle del adaptador: el dominio sigue usando `PatchVersion` y `Validity`. Cada componente de la versión se valida en el rango `0..999`.

## Alternativas descartadas

- **Qdrant:** filtros más ricos y modo servidor, pero suma infraestructura que el MVP no necesita. Se reevalúa si el corpus o el filtrado crecen.
- **FAISS:** no filtra por metadatos de forma nativa; obligaría a filtrar después de recuperar, lo que viola D1.

## Consecuencias

- Un único proceso local, sin contenedores, para el MVP.
- El índice BM25 aplica el mismo filtro con la misma codificación para mantener consistencia.
- Los tests de contrato de `VectorIndex` verifican el filtro de vigencia en los bordes: `v == valid_from`, `v == valid_until` y vigencia abierta.
- Cambiar a Qdrant implica escribir un adaptador nuevo y correr la misma suite de contrato; el dominio y los casos de uso no cambian.
