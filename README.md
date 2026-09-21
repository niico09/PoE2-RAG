# PoE2-RAG: Retrieval-Augmented Generation para Path of Exile 2

Pipeline modular de RAG diseñado específicamente para resolver los desafíos de dominios con alta densidad técnica, ambigüedad léxica (*more* vs. *increased* damage) y obsolescencia temporal frecuente debida a parches de balance continuos en *Path of Exile 2*.

---

## 🎯 Objetivo y Desafíos Técnicos

Los sistemas RAG convencionales (naive RAG basado únicamente en similitud vectorial con embeddings densos) fallan en PoE2 por tres motivos estructurales:

1. **Ambigüedad léxica vs. semántica:** Términos idénticos semánticamente tienen implicaciones matemáticas opuestas en el motor de cálculo del juego (por ejemplo, `+X% increased damage` es acumulativo aditivo, mientras que `+X% more damage` es multiplicativo). La búsqueda por embeddings densos tiende a mezclarlos sin distinción estricta.
2. **Nombres propios y modificadores exactos:** Consultas sobre nodos pasivos, nombres de gemas (`Spark`, `Blight`) o modificadores de equipo requieren coincidencia léxica exacta, donde los embeddings sufren de falsos positivos semánticos.
3. **Obsolescencia temporal de parches:** Una mecánica o valor de balance válido en la versión `0.1.0` puede ser obsoleto o haber sido eliminado en la `0.1.2`. El pipeline debe filtrar de manera obligatoria por metadatos de versión y vigencia.

---

## 🏗️ Arquitectura del Sistema

```text
[ Fuentes: Wiki / JSON de Datos / Patch Notes ]
                    │
                    ▼
     [ Ingesta & Parser Semántico ]
   (Chunking estructurado por gema / sección)
                    │
                    ▼
        [ Indexación Híbrida ]
    ┌───────────────┴───────────────┐
    ▼                               ▼
[ BM25 Lexical Index ]      [ Dense Vector Store ]
(Nombres, tags exactos)      (ChromaDB / Qdrant)
    └───────────────┬───────────────┘
                    │
                    ▼
       [ Reciprocal Rank Fusion ]
                    │
                    ▼
     [ Reranking (Cross-Encoder) ]
    (Top-K reordenado por relevancia)
                    │
                    ▼
          [ LLM + In-Context ]
```

---

## 📂 Estructura del Repositorio

```text
poe2-rag/
├── data/
│   ├── raw/                 # Snapshots de la wiki y notas de parche sin procesar
│   └── processed/           # Documentos normalizados en JSON con metadatos
├── src/
│   ├── ingestion/           # Parsers de datos, splitters semánticos y metadatos
│   │   ├── gem_parser.py
│   │   └── patch_parser.py
│   ├── retrieval/           # Búsqueda densa, BM25, fusión y reranker
│   │   ├── hybrid_retriever.py
│   │   └── reranker.py
│   ├── generation/          # Templates de prompts y llamada al LLM
│   │   └── rag_chain.py
│   └── eval/                # Dataset de pruebas y métricas (Hit Rate, MRR, Faithfulness)
│       └── eval_pipeline.py
├── tests/                   # Pruebas unitarias de parsing y recuperación
├── .env.example              # Variables de entorno y API keys
├── requirements.txt          # Dependencias del proyecto
└── README.md
```

---

## ⚙️ Esquema de Metadatos

Cada chunk indexado contiene metadatos normalizados para permitir pre/post-filtrado determinista:

```json
{
  "doc_id": "gem_spark_v012",
  "name": "Spark",
  "category": "active_gem",
  "tags": ["Spell", "Lightning", "Projectile"],
  "patch_version": "0.1.2",
  "is_current_patch": true,
  "source_url": "https://poe2db.tw/us/Spark",
  "content": "Spark dispara proyectiles de rayo que rebotan en las paredes y se mueven erráticamente. Coste de Maná: 12. Escala con modificadores de Proyectiles y Daño de Rayo."
}
```

---

## 🗺️ Roadmap

- [x] Ingesta y chunking con metadatos de gemas de habilidad.
- [x] Implementación de retriever híbrido (BM25 + ChromaDB / FAISS).
- [x] Reranking con modelo Cross-Encoder (bge-reranker-base).
- [ ] Filtrado temporal determinista para notas de parche concurrentes.
- [ ] Modelado del árbol de habilidades pasivas mediante grafos de conocimiento (GraphRAG).
- [ ] Soporte para balance de armas y cálculo de tiers de modificadores.
