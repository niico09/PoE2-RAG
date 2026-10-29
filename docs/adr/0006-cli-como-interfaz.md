# ADR-0006: CLI como única interfaz del MVP

- **Estado:** aceptada
- **Fecha:** 2026-10-29
- **Decisión relacionada:** A6 en [architecture.md](../architecture.md#9-decisiones-abiertas)

## Contexto

El MVP tiene un solo usuario local. Hace falta poder ingerir fuentes, hacer preguntas y correr la evaluación; no hay clientes externos que consuman el sistema.

## Decisión

La única interfaz del MVP es una CLI con tres comandos, cada uno mapeado a un caso de uso:

| Comando | Caso de uso |
|---|---|
| `poe2-rag ingest <fuente>` | `IngestSource` (y `ReindexCorpus` al terminar) |
| `poe2-rag ask "<pregunta>" [--patch <versión>]` | `AnswerQuestion` |
| `poe2-rag eval [--dataset <ruta>]` | `RunEvaluation` |

La CLI es un **adaptador de entrada**: traduce argumentos a llamadas a los casos de uso y presenta el resultado (respuesta, citas y versión de parche usada). No contiene lógica de negocio.

## Alternativas descartadas

- **API HTTP:** no hay consumidores en el MVP; agrega servidor, serialización y manejo de errores HTTP sin beneficio actual.

## Consecuencias

- La estructura de carpetas incorpora `adapters/inbound/cli` en cada capacidad que expone comandos, separado de los adaptadores de salida.
- `bootstrap.py` arma las dependencias y la CLI las recibe ya construidas, lo que permite probar los comandos con fakes.
- Agregar una API HTTP más adelante es un adaptador de entrada nuevo; los casos de uso no cambian.
