# PoE2-RAG: Retrieval-Augmented Generation para Path of Exile 2

Pipeline modular de RAG diseñado específicamente para resolver los desafíos de dominios con alta densidad técnica, ambigüedad léxica (*more* vs. *increased* damage) y obsolescencia temporal frecuente debida a parches de balance continuos en *Path of Exile 2*.

> **Estado:** en implementación, con la CLI del MVP operativa. Existen con tests el modelo de dominio compartido, el catálogo de parches, el caso de uso `AnswerQuestion` con sus puertos de consulta, el primer corte de ingesta (`IngestSource`, repositorio JSON, fetcher de snapshots locales y parser de notas de parche), el índice léxico BM25 (`ReindexCorpus`, tokenizador propio y adaptador con índice JSON persistido), el índice denso (embedder local `bge-base-en-v1.5` y adaptador sobre Chroma embebido), el reranker cross-encoder, la generación con citas nativas de Claude, la configuración (`AppConfig`), el composition root (`bootstrap.py`) y los comandos `ingest` y `ask` de la CLI. Todavía no: el fetcher HTTP, los adaptadores de poe2db y poe2wiki.net, el comando `eval` y la evaluación de §8.
> El diseño completo está en [`docs/architecture.md`](docs/architecture.md) y las decisiones en [`docs/adr/`](docs/adr/).

---

## 🎯 Objetivo y Desafíos Técnicos

Los sistemas RAG convencionales (naive RAG basado únicamente en similitud vectorial con embeddings densos) fallan en PoE2 por tres motivos estructurales:

1. **Ambigüedad léxica vs. semántica:** Términos idénticos semánticamente tienen implicaciones matemáticas opuestas en el motor de cálculo del juego (por ejemplo, `+X% increased damage` es acumulativo aditivo, mientras que `+X% more damage` es multiplicativo). La búsqueda por embeddings densos tiende a mezclarlos sin distinción estricta.
2. **Nombres propios y modificadores exactos:** Consultas sobre nodos pasivos, nombres de gemas (`Spark`, `Blight`) o modificadores de equipo requieren coincidencia léxica exacta, donde los embeddings sufren de falsos positivos semánticos.
3. **Obsolescencia temporal de parches:** Una mecánica o valor de balance válido en la versión `0.1.0` puede ser obsoleto o haber sido eliminado en la `0.2.0`. El pipeline debe filtrar de manera obligatoria por metadatos de versión y vigencia.

---

## 🏗️ Arquitectura del Sistema

Arquitectura hexagonal con dos flujos que solo se comunican a través de los índices: **ingesta** (offline) y **consulta** (online). Detalle en [`docs/architecture.md`](docs/architecture.md).

```text
[ Fuentes: poe2db / poe2wiki.net / Notas de parche ]
                    │
                    ▼
     [ Ingesta & Parser Semántico ]
   (Chunking estructurado por gema / sección,
    vigencia por rango de parches)
                    │
                    ▼
        [ Indexación Híbrida ]
    ┌───────────────┴───────────────┐
    ▼                               ▼
[ BM25 Lexical Index ]      [ Dense Vector Store ]
(Nombres, tags exactos)      (Chroma + bge-base-en-v1.5)
    └───────────────┬───────────────┘
                    │  pre-filtro por vigencia del parche
                    ▼
       [ Reciprocal Rank Fusion ]
                    │
                    ▼
     [ Reranking (Cross-Encoder) ]
    (bge-reranker-base, top-K final)
                    │
                    ▼
   [ LLM con citas nativas (Claude Sonnet 5.5) ]
```

| Componente | Elección del MVP | Decisión |
|---|---|---|
| Idioma | Todo en inglés | [ADR-0001](docs/adr/0001-idioma-mvp-ingles.md) |
| Fuentes | poe2db, notas de parche oficiales, poe2wiki.net | [ADR-0002](docs/adr/0002-fuentes-de-datos-mvp.md) |
| Vector store | Chroma embebido | [ADR-0003](docs/adr/0003-vector-store-chroma.md) |
| Embeddings | `bge-base-en-v1.5` local | [ADR-0004](docs/adr/0004-embeddings-locales.md) |
| Generación | Claude Sonnet 5.5 por API | [ADR-0005](docs/adr/0005-llm-por-api.md) |
| Interfaz | CLI (`ingest`, `ask`, `eval`) | [ADR-0006](docs/adr/0006-cli-como-interfaz.md) |

---

## 🚀 Uso

Requisitos: Python ≥ 3.12 y [`uv`](https://docs.astral.sh/uv/). Los artefactos derivados (`data/raw/`, `data/processed/`, `data/index/`) se generan en local y están fuera de control de versiones ([ADR-0002](docs/adr/0002-fuentes-de-datos-mvp.md)).

### Ingesta

```bash
# Ingiere un snapshot local de notas de parche y reconstruye los dos índices
uv run poe2-rag ingest patch-notes data/raw/patch-notes-0.1.1.html
```

El snapshot necesita su sidecar `<archivo>.meta.json` con `{"captured_at": "<ISO 8601 con zona>"}`; sin él la corrida falla en vez de inventar la fecha de captura. `patch-notes` es la única fuente disponible por ahora: `poe2db` y `poe2wiki.net` se suman al mismo comando cuando existan sus adaptadores, sin cambiar la forma del comando. La ingesta necesita la clave solo para reconstruir el índice denso del modelo de embeddings local, nunca la de Claude.

### Consulta

La generación usa la API de Claude ([ADR-0005](docs/adr/0005-llm-por-api.md)): la key se lee del entorno, la lee el SDK y nunca se guarda en la configuración ni se imprime. Copiá `.env.example` a `.env`, completá `ANTHROPIC_API_KEY` y corré los comandos con `--env-file`:

```bash
uv run --env-file .env poe2-rag ask "How much mana does Spark cost?"
uv run --env-file .env poe2-rag ask "What changed for Spark?" --patch 0.1.0
```

Sin `--patch` se responde para el parche `current` de `config/patches.toml`. La salida trae la respuesta, la versión usada y las citas numeradas (chunk, texto citado y URL de la fuente); si el contexto recuperado no alcanza, se imprime la respuesta fija de "sin información" con la aclaración de que ninguna cita la respalda.

Códigos de salida: `0` éxito, `1` error esperado (una línea en stderr, sin traza) y `2` error de uso de la línea de comandos.

### Configuración por entorno

Todo se puede ajustar por variables de entorno; un valor vacío usa el default y `.env.example` lista los nombres sin valores.

| Variable | Default | Qué define |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | credencial de `ask`; la lee el SDK |
| `POE2_RAG_PROJECT_ROOT` | directorio de trabajo | raíz de la que cuelgan los defaults |
| `POE2_RAG_RAW_DIR` | `data/raw` | snapshots crudos de las fuentes |
| `POE2_RAG_PROCESSED_DIR` | `data/processed` | corpus procesado (`chunks.json`) |
| `POE2_RAG_INDEX_DIR` | `data/index` | índices derivados (`bm25.json` y `chroma/`) |
| `POE2_RAG_PATCH_CATALOG` | `config/patches.toml` | catálogo de parches |
| `POE2_RAG_EMBEDDER_MODEL` | `BAAI/bge-base-en-v1.5` | modelo de embeddings local |
| `POE2_RAG_RERANKER_MODEL` | `BAAI/bge-reranker-base` | cross-encoder de reranking |
| `POE2_RAG_LLM_MODEL` | `claude-sonnet-5-5` | modelo de generación |
| `POE2_RAG_LLM_MAX_TOKENS` | `1024` | tope de tokens de la respuesta |
| `POE2_RAG_LLM_EFFORT` | `medium` | profundidad de razonamiento del modelo |

---

## 📂 Estructura del Repositorio (objetivo)

```text
poe2-rag/
├── config/
│   └── patches.toml         # Catálogo de parches: cuáles existen y cuál es el actual
├── data/
│   ├── raw/                 # Snapshots de las fuentes sin procesar
│   ├── processed/           # Chunks normalizados en JSON con metadatos
│   └── index/               # Índices derivados (BM25 JSON y Chroma), reconstruibles desde data/processed/
├── docs/
│   ├── architecture.md      # Diseño del sistema
│   └── adr/                 # Registro de decisiones de arquitectura
├── src/poe2_rag/
│   ├── shared/
│   │   ├── domain/          # PatchVersion, Validity, PatchCatalog, Chunk, Modifier, ...
│   │   └── adapters/outbound/  # adaptadores compartidos (catálogo de parches)
│   ├── ingestion/           # Fuentes, parsers, chunking e indexación
│   ├── answering/           # Recuperación híbrida, RRF, reranking y generación
│   ├── evaluation/          # Dataset y métricas (Hit@K, MRR, fidelidad, obsolescencia)
│   ├── cli.py               # CLI (`ingest`, `ask`): argparse, despacho y contrato de errores
│   ├── config.py            # AppConfig: rutas, modelos y presupuesto del LLM desde el entorno
│   └── bootstrap.py         # Composition root
└── tests/                   # Unitarios, de contrato y de evaluación
```

Cada capacidad se divide en `domain/`, `application/` y `adapters/`.

---

## ⚙️ Esquema de Chunk

La vigencia se expresa como un rango de parches, no como una marca de "parche actual": el catálogo de parches es el único que sabe cuál es el vigente. La identidad de la entidad (`active_gem:spark`) es independiente de la versión.

El catálogo se declara en `config/patches.toml` (`current` más una tabla `[[patches]]` por parche) y lo carga el adaptador `TomlPatchCatalog`; el puerto `PatchCatalog` es la única fuente de verdad de "parche actual".

> Los valores de versión del ejemplo siguiente son ilustrativos: el parche actual autoritativo es `current` en `config/patches.toml`.

```json
{
  "chunk_id": "poe2db:active_gem:spark@0.1.1#description",
  "entity": { "kind": "active_gem", "slug": "spark", "name": "Spark" },
  "validity": { "valid_from": "0.1.1", "valid_until": null },
  "section": "description",
  "tags": ["Spell", "Lightning", "Projectile"],
  "modifiers": [
    { "stat": "mana_cost", "value": 12, "kind": "flat" }
  ],
  "source": {
    "type": "poe2db",
    "url": "https://poe2db.tw/us/Spark",
    "captured_at": "2026-10-29"
  },
  "text": "Spark fires lightning projectiles that bounce off walls and move erratically..."
}
```

---

## 🗺️ Roadmap

- [x] Diseño de arquitectura y decisiones del MVP.
- [x] Revisar las condiciones de uso de poe2db ([ADR-0002](docs/adr/0002-fuentes-de-datos-mvp.md)).
- [ ] Conseguir acceso autorizado a poe2wiki.net.
- [x] Modelo de dominio (`PatchVersion`, `Validity`, `EntityRef`, `SourceRef`, `Modifier`, `Chunk`) y catálogo de parches (`PatchCatalog`) con tests unitarios y de contrato.
- [x] Caso de uso `AnswerQuestion` y puertos de consulta (`Embedder`, `LexicalIndex`, `VectorIndex`, `Reranker`, `LanguageModel`) con tests unitarios y fakes.
- [x] Primer corte de ingesta: notas de parche oficiales → chunks validados y persistidos (`IngestSource`, `ChunkRepository` JSON, fetcher de snapshots con metadatos y parser del foro) con fixtures mínimas. La ingesta HTTP queda pendiente.
- [ ] Ingesta y chunking con metadatos de gemas de habilidad.
- [x] Índice léxico BM25 (Okapi, puro Python) reconstruido desde el corpus con `ReindexCorpus`, con
  pre-filtrado por vigencia, tokenizador determinista y tests unitarios, de contrato y de integración.
- [x] Índice denso: embedder local `bge-base-en-v1.5` y adaptador sobre Chroma embebido, con pre-filtrado
  por vigencia dentro de la consulta, identidad del modelo persistida e informe de textos truncados.
- [x] Retriever híbrido (BM25 + Chroma) con pre-filtrado por vigencia, fusionado con RRF dentro de
  `AnswerQuestion` y expuesto por el comando `ask`.
- [x] Reranking con Cross-Encoder (`bge-reranker-base`).
- [x] Generación con citas nativas, composition root (`bootstrap.py`), configuración (`AppConfig`) y
  CLI (`ingest`, `ask`).
- [ ] Comando `eval` y dataset de evaluación con métricas.
- [ ] Comparar Nemotron 3.5 Lightning como generador local.
- [ ] Modelado del árbol de habilidades pasivas mediante grafos de conocimiento (GraphRAG).
- [ ] Soporte para balance de armas y cálculo de tiers de modificadores.
