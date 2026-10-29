# PoE2-RAG: Retrieval-Augmented Generation para Path of Exile 2

Pipeline modular de RAG diseñado específicamente para resolver los desafíos de dominios con alta densidad técnica, ambigüedad léxica (*more* vs. *increased* damage) y obsolescencia temporal frecuente debida a parches de balance continuos en *Path of Exile 2*.

> **Estado:** diseño del MVP acordado; implementado el modelo de dominio compartido
> (`src/poe2_rag/shared/domain/`) con su suite de tests unitarios. La ingesta, la consulta y la
> evaluación siguen sin implementar.
> El diseño completo está en [`docs/architecture.md`](docs/architecture.md) y las decisiones en [`docs/adr/`](docs/adr/).

---

## 🎯 Objetivo y Desafíos Técnicos

Los sistemas RAG convencionales (naive RAG basado únicamente en similitud vectorial con embeddings densos) fallan en PoE2 por tres motivos estructurales:

1. **Ambigüedad léxica vs. semántica:** Términos idénticos semánticamente tienen implicaciones matemáticas opuestas en el motor de cálculo del juego (por ejemplo, `+X% increased damage` es acumulativo aditivo, mientras que `+X% more damage` es multiplicativo). La búsqueda por embeddings densos tiende a mezclarlos sin distinción estricta.
2. **Nombres propios y modificadores exactos:** Consultas sobre nodos pasivos, nombres de gemas (`Spark`, `Blight`) o modificadores de equipo requieren coincidencia léxica exacta, donde los embeddings sufren de falsos positivos semánticos.
3. **Obsolescencia temporal de parches:** Una mecánica o valor de balance válido en la versión `0.1.0` puede ser obsoleto o haber sido eliminado en la `0.1.2`. El pipeline debe filtrar de manera obligatoria por metadatos de versión y vigencia.

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

## 📂 Estructura del Repositorio (objetivo)

```text
poe2-rag/
├── data/
│   ├── raw/                 # Snapshots de las fuentes sin procesar
│   └── processed/           # Chunks normalizados en JSON con metadatos
├── docs/
│   ├── architecture.md      # Diseño del sistema
│   └── adr/                 # Registro de decisiones de arquitectura
├── src/poe2_rag/
│   ├── shared/domain/       # PatchVersion, Validity, Chunk, Modifier, ...
│   ├── ingestion/           # Fuentes, parsers, chunking e indexación
│   ├── answering/           # Recuperación híbrida, RRF, reranking y generación
│   ├── evaluation/          # Dataset y métricas (Hit@K, MRR, fidelidad, obsolescencia)
│   └── bootstrap.py         # Composition root
└── tests/                   # Unitarios, de contrato y de evaluación
```

Cada capacidad se divide en `domain/`, `application/` y `adapters/`.

---

## ⚙️ Esquema de Chunk

La vigencia se expresa como un rango de parches, no como una marca de "parche actual": el catálogo de parches es el único que sabe cuál es el vigente. La identidad de la entidad (`active_gem:spark`) es independiente de la versión.

```json
{
  "chunk_id": "poe2db:active_gem:spark@0.1.2#description",
  "entity": { "kind": "active_gem", "slug": "spark", "name": "Spark" },
  "validity": { "valid_from": "0.1.2", "valid_until": null },
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
- [ ] Revisar términos de uso de poe2db y conseguir acceso autorizado a poe2wiki.net.
- [x] Modelo de dominio (`PatchVersion`, `Validity`, `EntityRef`, `SourceRef`, `Modifier`, `Chunk`) con tests unitarios.
- [ ] Ingesta y chunking con metadatos de gemas de habilidad.
- [ ] Retriever híbrido (BM25 + Chroma) con pre-filtrado por vigencia.
- [ ] Reranking con Cross-Encoder (`bge-reranker-base`).
- [ ] Generación con citas nativas y CLI.
- [ ] Dataset de evaluación y métricas.
- [ ] Comparar Nemotron 3.5 Lightning como generador local.
- [ ] Modelado del árbol de habilidades pasivas mediante grafos de conocimiento (GraphRAG).
- [ ] Soporte para balance de armas y cálculo de tiers de modificadores.
