# ADR-0003: Chroma como vector store del MVP y codificación de la vigencia

- **Estado:** aceptada
- **Fecha:** 2026-10-29
- **Decisiones relacionadas:** A3 y A7 en [architecture.md](../architecture.md#9-decisiones-abiertas)

## Contexto

El puerto `VectorIndex` debe aplicar el filtro de vigencia **antes** de la fusión (D1). El MVP corre en local, con un solo usuario y un corpus chico.

## Decisión

1. **Chroma en modo embebido** (persistente en disco, sin servidor) es el adaptador de `VectorIndex` en el MVP.
2. **La versión se codifica como entero ordenable:**
   `((major * 1_000 + minor) * 1_000 + patch) * 27 + hotfix`.
   Ejemplo: `0.1.2` → `27_054`; `0.5.5` → `135_135`; `0.5.5e` → `135_140`; `0.5.6` → `135_162`.
   Se descarta concatenar dígitos (`0.1.2` → `102`): cuando un componente llega a 10 aparecen colisiones (`0.1.10` y `0.11.0` dan ambos `110`) y el orden se rompe.
   El hotfix de un parche de Path of Exile 2 (`0.5.5e`) es una versión propia, no un alias de su parche base, porque cambia números de balance y D1 exige validez exacta. Por eso se codifica como ordinal en base 27: sin sufijo `0`, `a` = 1 … `z` = 26. La escala 27 es la que deja el hotfix **después** de su parche y **antes** del siguiente: `0.5.5 < 0.5.5a < ... < 0.5.5z < 0.5.6`, y el orden de los enteros coincide con el orden de `PatchVersion`.
3. **Vigencia abierta con centinela:** un `valid_until` vacío se guarda como `27_000_000_000`, un valor estrictamente mayor que toda versión codificable, porque los filtros por metadatos no tratan bien los valores nulos.
4. **Filtro de vigencia** para una versión objetivo `v`:
   `valid_from_code <= v AND valid_until_code > v`.
5. **Rango y límites:** cada componente numérico se valida en `0..999` y el máximo codificable es `999.999.999z` → `26_999_999_999`. Ese valor **no entra en int32**, así que la codificación usa el rango de 64 bits. Antes de elegir el layout se verificó el rango real de los filtros por metadatos en la versión instalada (chromadb 1.5.9): valores por encima de `2**31 - 1` se guardan y se leen exactos, y `$gt`, `$lte` y `$lt` los ordenan bien a través del límite de 32 bits y hasta `2**63 - 1`; un valor fuera del rango int64 no es representable. En código, un componente o un código fuera de rango levanta `VersionComponentError`, nunca un desbordamiento silencioso: el layout es denso, así que todo entero en `0..26_999_999_999` corresponde a una única versión.
6. **Guard de versión del codec:** la codificación cambió al volverse el hotfix una versión de primera clase, y un índice escrito con la anterior devolvería rangos de vigencia sutilmente equivocados. La colección guarda `version_codec` en sus metadatos, junto a `embedding_model` y `dimension`; un lector cuyo codec no coincide —incluido un índice anterior al guard, que no tiene la clave— falla con `VersionCodecMismatchError` y su mensaje pide reindexar.

La codificación es un detalle del adaptador: el dominio sigue usando `PatchVersion` y `Validity`.

## Alternativas descartadas

- **Qdrant:** filtros más ricos y modo servidor, pero suma infraestructura que el MVP no necesita. Se reevalúa si el corpus o el filtrado crecen.
- **FAISS:** no filtra por metadatos de forma nativa; obligaría a filtrar después de recuperar, lo que viola D1.

## Consecuencias

- Un único proceso local, sin contenedores, para el MVP.
- El índice BM25 aplica el mismo rango de vigencia sobre los objetos de dominio (`Chunk.is_valid_at`), no sobre enteros: la codificación de esta ADR es exclusiva del adaptador de Chroma, que filtra por metadatos dentro de la consulta. Ambos adaptadores respetan el mismo intervalo, cada uno en su representación.
- Los tests de contrato de `VectorIndex` verifican el filtro de vigencia en los bordes: `v == valid_from`, `v == valid_until` y vigencia abierta.
- Un índice escrito antes del guard de codec (o con otro layout) no se lee en silencio: hay que reconstruirlo con `reindex`. Es barato a propósito: el corpus es chico y regenerable desde `data/`.
- Cambiar a Qdrant implica escribir un adaptador nuevo y correr la misma suite de contrato; el dominio y los casos de uso no cambian.
