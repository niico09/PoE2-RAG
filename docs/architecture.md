# Diseño y arquitectura — PoE2-RAG

> Estado: **diseño del MVP acordado; ingesta, recuperación léxica y recuperación densa implementadas**.
> Implementados el modelo de dominio de §4, el puerto `PatchCatalog` con su adaptador TOML, el caso de uso
> `AnswerQuestion` de §5.1 con sus puertos de consulta (probado con *fakes*), el primer corte de ingesta de
> §5.2: `IngestSource`, el repositorio JSON de chunks, el fetcher de snapshots locales y el parser de notas de
> parche, y los dos índices de §5.3 a §5.5: `ReindexCorpus`, el tokenizador determinista, el adaptador BM25
> Okapi, el embedder local `bge-base-en-v1.5` y el índice vectorial sobre Chroma embebido, probados con fakes,
> con un embedder determinista y, de punta a punta, sobre el repositorio JSON del corpus. Todavía no: el
> fetcher HTTP, los adaptadores de poe2db y poe2wiki.net, el reranker, el LLM, la CLI y la evaluación de §8.
> Las decisiones se registran como ADR en [`docs/adr/`](adr/). Pendiente antes de implementar el adaptador de poe2wiki.net: una vía de acceso autorizada ([ADR-0002](adr/0002-fuentes-de-datos-mvp.md)).

---

## 1. Objetivo y alcance

Responder preguntas sobre *Path of Exile 2* con respuestas **correctas para un parche concreto** y **citadas** a su fuente.

**En alcance (v1):**
- Gemas de habilidad (activas y de soporte) y notas de parche.
- Consultas en lenguaje natural con respuesta generada y fuentes citadas.
- Filtrado determinista por versión de parche.
- Evaluación automática de recuperación y fidelidad.

**Fuera de alcance (v1):**
- Árbol de pasivas como grafo (GraphRAG).
- Cálculo de daño o de tiers de modificadores.
- Interfaz gráfica, multiusuario y despliegue en la nube.

---

## 2. Drivers de arquitectura

Ordenados por prioridad. Cada decisión de diseño se justifica contra esta lista.

| # | Driver | Por qué importa |
|---|---|---|
| D1 | **Vigencia temporal** | Una respuesta correcta para `0.1.1` puede ser falsa en `0.2.0`. Un dato obsoleto es peor que ninguno. |
| D2 | **Precisión léxica** | `increased` (aditivo) y `more` (multiplicativo) no son sinónimos. Los nombres propios requieren coincidencia exacta. |
| D3 | **Trazabilidad** | Toda afirmación de la respuesta debe poder rastrearse a un chunk y a su fuente. |
| D4 | **Evaluabilidad** | Cada cambio en el pipeline se mide contra un dataset fijo antes de aceptarse. |
| D5 | **Reemplazabilidad** | El vector store, el modelo de embeddings, el reranker y el LLM deben poder cambiarse sin tocar la lógica de negocio. |

---

## 3. Estilo arquitectónico

**Arquitectura hexagonal (puertos y adaptadores)** con dos flujos separados que comparten el modelo de dominio:

- **Ingesta (offline, escritura):** fuente → parseo → normalización → chunking → indexación.
- **Consulta (online, lectura):** pregunta → análisis → recuperación → reranking → generación → respuesta citada.
- **Evaluación:** consume el flujo de consulta como caja negra y mide resultados.

La ingesta y la consulta no se llaman entre sí: se comunican solo a través de los índices. Esto permite reindexar sin afectar la consulta y probar cada flujo por separado.

```text
              ┌──────────────── Dominio (sin dependencias externas) ────────────────┐
              │  Chunk · PatchVersion · Validity · Modifier · Query · Answer · ...  │
              └─────────────────────────────────────────────────────────────────────┘
                         ▲                                        ▲
   ┌─────────────────────┴─────────┐              ┌───────────────┴─────────────────┐
   │ Aplicación: Ingesta           │              │ Aplicación: Consulta            │
   │  IngestSource, ReindexCorpus  │              │  AnswerQuestion                 │
   └───────┬───────────────┬───────┘              └──────┬──────────────────┬───────┘
           │ puertos       │                             │ puertos          │
   SourceFetcher      LexicalIndex  ◄── índices ──►  LexicalIndex        LanguageModel
   DocumentParser     VectorIndex     compartidos     VectorIndex         Reranker
   Embedder           PatchCatalog                    PatchCatalog        Embedder
           ▲                                                 ▲
   Adaptadores: poe2db, wiki,                       Adaptadores: BM25, Chroma/Qdrant,
   patch notes, BM25, Chroma/Qdrant                 cross-encoder, proveedor de LLM
```

**Compromiso:** la hexagonal agrega interfaces y archivos comparado con un script lineal. Se acepta porque D4 y D5 exigen poder sustituir cada componente por un *fake* en tests y por otra implementación en producción.

---

## 4. Modelo de dominio

### 4.1 Cambios respecto del esquema del README

El esquema actual del README tiene dos problemas de diseño:

1. **`is_current_patch: true` es estado derivado guardado en el dato.** Se vuelve falso en cada parche nuevo y obliga a reescribir todo el corpus. Se reemplaza por un **rango de vigencia** y un **catálogo de parches** que sabe cuál es el actual.
2. **`doc_id: "gem_spark_v012"` mezcla identidad y versión.** Se separa en `entity` (estable: `active_gem:spark`) y la versión. Así se puede pedir "Spark en cualquier versión" o "todas las versiones de Spark".

### 4.2 Conceptos

| Concepto | Tipo | Responsabilidad e invariantes |
|---|---|---|
| `PatchVersion` | Value object | Versión semántica comparable (`0.1.2 > 0.1.0`). Inmutable. Rechaza formatos inválidos. |
| `Validity` | Value object | Rango `[valid_from, valid_until)`. `valid_until` vacío = vigente. Responde `is_valid_at(version)`. Invariante: `valid_from < valid_until`. |
| `PatchCatalog` | Puerto de dominio | Lista de parches conocidos y cuál es el actual. Única fuente de verdad de "actual". |
| `EntityRef` | Value object | Identidad estable de lo que describe el chunk: `kind` (gema, soporte, mecánica, ítem, notas de parche) + `slug`. |
| `Modifier` | Value object | Modificador estructurado: `stat`, `value`, `kind` ∈ {`increased`, `reduced`, `more`, `less`, `added`, `flat`}. Resuelve D2 por estructura, no solo por búsqueda. |
| `Chunk` | Entidad | Unidad indexable: `chunk_id`, `EntityRef`, `Validity`, `section`, `text`, `tags`, `modifiers`, `SourceRef`. |
| `SourceRef` | Value object | URL, tipo de fuente y fecha de captura. Base de las citas (D3). |
| `Query` | Value object | Texto + `target_version` (por defecto, la actual del catálogo). La detección de entidades todavía no existe: es trabajo futuro, no una capacidad de este value object. |
| `RetrievedChunk` | Value object | `Chunk` + puntajes por etapa (léxico, denso, fusión, rerank). Permite depurar y evaluar. |
| `Answer` | Value object | Texto generado + lista de `Citation` que apuntan a `chunk_id`. Invariante: una respuesta sin citas se marca como "sin respaldo". |

### 4.3 Esquema de chunk propuesto

> Ejemplo ilustrativo. El parche que el sistema considera actual lo declara `current` en
> `config/patches.toml`; un chunk es vigente por su rango de `Validity`, nunca por una marca propia.

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

## 5. Casos de uso

| Caso de uso | Flujo | Entrada → Salida |
|---|---|---|
| `IngestSource` | Ingesta | Identificador de fuente → chunks persistidos en `data/processed/` |
| `ReindexCorpus` | Ingesta | Corpus procesado → índices reconstruidos (hoy, el léxico) |
| `AnswerQuestion` | Consulta | `(texto, versión)` → `AnswerResult`: `Answer` con citas + chunks entregados |
| `RunEvaluation` | Evaluación | Dataset de evaluación → reporte de métricas |

### 5.1 Flujo de `AnswerQuestion`

1. **Análisis de la consulta:** detectar entidades (nombres de gemas), tipo de modificador mencionado y versión objetivo. Sin versión explícita, usar la actual del `PatchCatalog`.
2. **Pre-filtrado por vigencia:** ambos índices reciben el filtro `is_valid_at(target_version)`. **El filtro se aplica antes de la fusión**, nunca después: si se filtra tarde, RRF ya mezcló rankings con datos obsoletos y el top-K queda con huecos. Si un índice devuelve un chunk que no es vigente para la versión objetivo, `AnswerQuestion` falla con `StaleChunkError` en vez de descartarlo: el contrato del índice está roto y descartar dejaría el mismo hueco. El adaptador léxico del MVP filtra sobre los objetos de dominio (`Chunk.is_valid_at`) y rankea solo los candidatos vigentes; el adaptador de Chroma filtra **dentro de la consulta** con el entero ordenable de [ADR-0003](adr/0003-vector-store-chroma.md), que es un detalle suyo y no del puerto.
3. **Recuperación híbrida:** BM25 (top-N) y vectorial (top-N). En el MVP las dos búsquedas corren en serie; paralelizarlas es una decisión del adaptador, no del caso de uso. Las dos están implementadas: el índice léxico de §5.4 y el denso de §5.5.
4. **Fusión:** Reciprocal Rank Fusion sobre ambos rankings.
5. **Reranking:** cross-encoder sobre el top-M fusionado → top-K final.
6. **Construcción de contexto:** cada chunk se entrega al LLM con su `chunk_id` y versión.
7. **Generación:** cada chunk se envía como documento con citas nativas habilitadas ([ADR-0005](adr/0005-llm-por-api.md)); el prompt exige responder "no hay información" si el contexto no alcanza.
8. **Validación de citas:** las citas se validan contra los chunks entregados. El dominio (`Answer.build`) **rechaza** la respuesta con `InvalidCitationError` cuando una cita nombra un chunk que no se entregó: no descarta en silencio, porque una cita sin respaldo invalida la respuesta ([ADR-0005](adr/0005-llm-por-api.md)). `AnswerQuestion` deja propagar ese rechazo: cualquier cita a un chunk no entregado invalida la respuesta completa; una respuesta sin citas se construye con `supported=False` ("sin respaldo").

Cada etapa es un componente con su propio puerto: se pueden medir y reemplazar por separado.

### 5.2 Flujo de `IngestSource`

1. **Fetch:** el `SourceFetcher` devuelve un `RawDocument` con el contenido, el locator, la fecha de captura (en el snapshot local, la del sidecar de metadatos, nunca el mtime) y el tipo de fuente.
2. **Parse:** el `DocumentParser` del tipo de fuente devuelve los chunks del dominio, en orden de documento. El parser de notas de parche lee solo el primer post de staff y fusiona las secciones que resuelven al mismo slug.
3. **Vigencia conocida:** un chunk cuyo `valid_from` no está en el `PatchCatalog` aborta la corrida con `UnknownPatchError`; no se descarta, porque un chunk fuera de la línea de tiempo haría que el corpus afirmara un parche que el sistema no reconoce (D1).
4. **Identidad única:** si un documento produce dos chunks con el mismo `chunk_id`, `DuplicateChunkIdError` aborta antes de escribir; guardar el último dejaría el corpus en silencio con una sola sección.
5. **Identidad por documento:** un `chunk_id` ya almacenado desde otra URL de origen aborta con `ChunkIdentityConflictError`; pisarlo movería la cita del primer hilo al segundo. Una recaptura de la misma URL sí es un update.
6. **Upsert:** se leen los chunks ya persistidos y solo se escriben los nuevos o cambiados. Un documento ya ingestado no reescribe el corpus y reporta sus chunks como `skipped`. Las secciones que desaparecen de la fuente no se podan: el chunk viejo sigue en el corpus con su vigencia (deuda explícita hasta que el puerto exprese el alcance de un documento).

Las notas de parche son la primera fuente implementada; el modelo de vigencia que usan se registra en §10.1.

### 5.3 Flujo de `ReindexCorpus`

1. **Lectura única:** el caso de uso lee el corpus procesado una vez y lo congela en una tupla de chunks.
   Cada escritor recibe ese mismo snapshot, así dos índices de una misma corrida describen el mismo corpus.
2. **Corpus vacío:** un corpus sin chunks aborta con `EmptyCorpusError` **antes** de llamar al embedder o a
   ningún escritor. Un índice construido desde nada respondería "no hay información" a toda consulta (D1), y
   reemplazar un índice bueno por uno vacío convertiría un reindexado roto en una regresión silenciosa.
3. **Embedding primero:** el `DocumentEmbedder` vectoriza todos los textos del snapshot en una sola llamada
   (los lotes los decide el adaptador) y el caso de uso verifica que haya exactamente un vector por chunk. Un
   modelo que no puede cargar deja los dos índices persistidos intactos, en vez de dejar un índice léxico
   reconstruido al lado de uno denso viejo. Las posiciones de los textos que superan el límite de secuencia se
   traducen a `chunk_id` para el informe.
4. **Reconstrucción:** los dos escritores (`LexicalIndexWriter` y `VectorIndexWriter`) reescriben su índice
   completo desde el snapshot: es un rebuild, no un upsert. Una escritura fallida deja el índice anterior
   intacto.
5. **Informe:** `ReindexReport(chunk_count, rebuilt_at, truncated_chunk_ids)`, con el tamaño del corpus leído,
   el momento UTC en que terminó la corrida y los chunks que el embedder tuvo que cortar. Un escritor que falla
   propaga su error y no se informa ningún índice.

El orden importa: leer antes de rechazar, rechazar antes de escribir y embeber antes de escribir significa que
una corrida fallida por corpus vacío o por modelo roto no reemplaza ninguno de los dos índices. Entre escritores
no hay atomicidad: un rebuild léxico exitoso seguido de uno vectorial fallido deja índices de versiones
distintas. Ese estado mixto se registra como riesgo en §10.

### 5.4 Flujo de búsqueda léxica

El adaptador BM25 Okapi (puro Python, `k1 = 1.5` y `b = 0.75` configurables) escribe **un** archivo JSON
con versión de esquema, versión del tokenizador, los parámetros `k1` y `b` con los que se construyó, el
`corpus_fingerprint` del corpus, las estadísticas (frecuencia de documentos por término, longitud media) y
los chunks completos que la consulta devuelve dentro de `RetrievedChunk`.

- **Tokenización única para indexar y consultar:** minúsculas; separación por espacios y puntuación; números,
  decimales y valores con signo o porcentaje enteros (`+10%`, `1.5`, `-20%`); términos compuestos de juego
  enteros **y** sus partes (`area-of-effect` → `area-of-effect`, `area`, `of`, `effect`). Sin stemming ni lista
  de stopwords en v1: `more`, `less` e `increased` son mecánicas distintas (D2).
- **Filtro antes de rankear y de limitar:** se descartan los chunks no vigentes y recién después se puntúa y
  se corta en `limit`, para que un éxito obsoleto no consuma un lugar.
- **IDF sobre el corpus completo:** nunca sobre el subconjunto vigente; el mismo término puntúa igual en
  cualquier parche.
- **Orden total:** puntaje descendente y, en empate, `chunk_id` ascendente; los chunks con puntaje cero no se
  devuelven.
- **Escritura atómica:** archivo temporal en el mismo directorio más `os.replace`, así un rebuild fallido
  nunca deja un índice truncado. Los documentos se serializan ordenados por `chunk_id`, de modo que dos
  rebuilds del mismo corpus producen bytes idénticos.
- **Fallos explícitos:** archivo ausente, versión de esquema desconocida, versión de tokenizador distinta y
  archivo corrupto tienen errores propios; `limit <= 0` y una consulta en blanco también. Ninguno se
  convierte en un resultado vacío.
- **Reglas y parámetros persistidos:** el archivo registra la versión del tokenizador y los valores de `k1` y
  `b`, y todo lector puntúa con los valores registrados. Así un mismo archivo produce siempre el mismo
  ranking, y un índice escrito con otras reglas de tokenización se rechaza en vez de responder con términos
  que ya no coinciden con la consulta.
- **Identidad única:** un corpus con dos chunks del mismo `chunk_id` se rechaza antes de escribir (`DuplicateIndexEntryError`),
  y un archivo con la misma identidad repetida también (`IndexCorruptError`): la consulta siguiente los
  convertiría en `DuplicateChunkError`.
- **Archivo vigente:** el lector cachea el índice decodificado y revalida la identidad del archivo (mtime, tamaño, inodo)
  en cada búsqueda, así un reindexado hecho por otra instancia no lo deja respondiendo con el índice viejo.

### 5.5 Flujo de búsqueda densa

El adaptador `BgeEmbedder` implementa los dos puertos del embedder (ADR-0004):

- **Consulta con instrucción, documentos sin ella.** `embed(text)` antepone la instrucción de recuperación del
  modelo; `embed_documents(texts)` no. Cargar el modelo perezosamente mantiene barato el armado del pipeline y
  deja el conjunto de tests por defecto sin red.
- **Vectores normalizados L2** pedidos al modelo, con la colección en espacio coseno; el puntaje denso es
  `1 - distancia`.
- **Sin truncado silencioso.** `overflowing_indices(texts)` cuenta tokens con el truncado apagado y reporta las
  posiciones que superan los 512 tokens del modelo; `ReindexCorpus` las convierte en `truncated_chunk_ids`.
- **Identidad fija.** Expone `model_id` y `dimension`; al cargar, verifica que el modelo produzca esa dimensión.

El adaptador `ChromaVectorIndex` escribe y lee una colección de Chroma embebido (`chromadb.PersistentClient`):

- **Metadatos de la colección:** `index_schema_version`, `embedding_model`, `dimension` y `chunk_count`. Cada
  registro guarda el chunk serializado y los dos enteros de vigencia de ADR-0003 (`valid_from_code`,
  `valid_until_code`), más su `chunk_id` como id.
- **Filtro dentro de la consulta:** `where = {$and: [{valid_from_code: {$lte: v}}, {valid_until_code: {$gt: v}}]}`
  antes de `n_results`, así el recorte de `limit` ocurre después de filtrar (D1).
- **Swap atómico con puntero:** Chroma no renombra una colección de forma atómica, así que `rebuild` arma una
  colección nueva `«nombre»__«uuid»`, la llena por lotes y recién entonces escribe el puntero
  `data/index/chroma/<nombre>.current.json` con `mkstemp` + `os.replace`. El lector solo confía en el puntero,
  de modo que un rebuild fallido deja la colección anterior respondiendo; después del swap se borra la vieja.
  Chroma no transacciona entre procesos: es un store local de un solo proceso (ADR-0003).
- **Identidad del modelo:** `search` compara `embedding_model` y `dimension` de la colección con el embedder
  configurado y falla con `EmbeddingModelMismatchError` si no coinciden; un vector de otro largo o no finito
  falla con `InvalidVectorError`.
- **Orden total:** puntaje descendente y, en empate, `chunk_id` ascendente; los resultados llevan solo
  `scores.dense`.
- **Fallos explícitos:** colección ausente, puntero ausente o ilegible, esquema desconocido, registro corrupto y
  rebuild fallido tienen errores propios; ninguno se convierte en un resultado vacío.

---

## 6. Puertos

| Puerto | Responsabilidad | Adaptadores candidatos |
|---|---|---|
| `SourceFetcher` | Obtener contenido crudo de una fuente | poe2db, notas de parche, poe2wiki.net, archivo local |
| `DocumentParser` | Contenido crudo → chunks del dominio (uno por tipo de fuente) | parser de gemas, parser de notas de parche |
| `ChunkRepository` | Persistir y leer el corpus procesado | JSON en `data/processed/` |
| `LexicalIndexWriter` | Reconstruir el índice léxico desde el corpus | el mismo adaptador BM25 que `LexicalIndex` |
| `PatchCatalog` | Parches conocidos y parche actual | archivo de configuración versionado |
| `Embedder` | Texto → vector (lado de la consulta) | `bge-base-en-v1.5` local ([ADR-0004](adr/0004-embeddings-locales.md)) |
| `DocumentEmbedder` | Textos del corpus → vectores (lado de la escritura) | el mismo adaptador `bge` que `Embedder` |
| `LexicalIndex` | Búsqueda léxica con filtro de vigencia | BM25 Okapi propio (puro Python) con índice JSON; Tantivy a futuro |
| `VectorIndex` | Búsqueda densa con filtro por metadatos | Chroma embebido ([ADR-0003](adr/0003-vector-store-chroma.md)); Qdrant a futuro |
| `VectorIndexWriter` | Reconstruir el índice denso desde el corpus y sus vectores | el mismo adaptador Chroma que `VectorIndex` |
| `Reranker` | Reordenar candidatos | `bge-reranker-base` |
| `LanguageModel` | Generar la respuesta | Claude Sonnet 5.5 por API ([ADR-0005](adr/0005-llm-por-api.md)); Nemotron 3.5 Lightning como candidato |

Los puertos son interfaces pequeñas (un método principal cada uno). En el código son `typing.Protocol`: los de consulta viven en `answering/application/ports.py` y los de ingesta en `ingestion/application/ports.py`, con sus *fakes* en `tests/` (§8). `LexicalIndex` y `VectorIndex` son puertos distintos porque sus capacidades de filtrado y sus parámetros difieren; unificarlos forzaría una interfaz inflada. Cada índice suma además un puerto de escritura que implementa el mismo adaptador: `LexicalIndexWriter.rebuild` y `VectorIndexWriter.rebuild(chunks, vectors)`. La consulta nunca adquiere la capacidad de reescribir un índice y solo `bootstrap.py` une las dos mitades. El embedder tiene la misma división: `Embedder.embed` responde a una consulta y `DocumentEmbedder` vectoriza el corpus, porque la instrucción de recuperación se aplica de un lado y no del otro (ADR-0004).

---

## 7. Estructura de carpetas propuesta

Organizada por capacidad (*screaming architecture*) y, dentro de cada una, por capa:

```text
src/poe2_rag/
├── shared/                  # núcleo compartido por ingesta y consulta
│   ├── domain/              # PatchVersion, Validity, PatchCatalog, EntityRef, Chunk, SourceRef, Modifier
│   └── adapters/outbound/   # adaptadores de puertos que usan ambas capacidades (config/patches.toml)
├── ingestion/
│   ├── domain/              # reglas de chunking y normalización
│   ├── application/         # IngestSource, ReindexCorpus, puertos de ingesta
│   └── adapters/
│       ├── inbound/cli/     # comando `ingest`
│       └── outbound/        # fetchers, parsers, repositorio JSON
├── answering/
│   ├── domain/              # Query, RetrievedChunk, Answer, Citation, RRF
│   ├── application/         # AnswerQuestion, puertos de consulta
│   └── adapters/
│       ├── inbound/cli/     # comando `ask`
│       └── outbound/        # lexical/ (tokenizador + BM25), dense/ (embedder bge + Chroma), reranker, LLM
├── evaluation/
│   ├── application/         # RunEvaluation, métricas
│   └── adapters/
│       ├── inbound/cli/     # comando `eval`
│       └── outbound/        # lector del dataset, reporte
└── bootstrap.py             # composition root: arma adaptadores según configuración
tests/
├── unit/                    # dominio y casos de uso con fakes
├── contract/                # cada adaptador cumple el contrato de su puerto
└── eval/                    # dataset de preguntas con respuestas esperadas
```

**Regla de dependencias:** `domain` no importa nada externo; `application` solo importa `domain`; `adapters` implementan puertos; solo `bootstrap.py` conoce todas las piezas. Las bibliotecas externas del denso (`chromadb`, `sentence-transformers`) solo pueden importarse desde `answering/adapters/outbound/dense/`: ni el dominio ni los casos de uso las ven, y el suite por defecto las evita salvo por el adaptador de Chroma. Un puerto que consumen las dos capacidades tiene su adaptador en `shared/adapters/outbound/` (hoy, `PatchCatalog`, que lee `config/patches.toml`); el de un puerto de una sola capacidad vive en el `adapters/` de esa capacidad. `LexicalIndexWriter` y `VectorIndexWriter` son la excepción deliberada: los consume solo la ingesta, pero su único adaptador vive en `answering/adapters/outbound/` porque implementa también el puerto de lectura y ambos comparten el almacenamiento; partirlo en dos adaptadores rompería la garantía de que la escritura y la lectura describen el mismo índice. Bajo `data/` conviven el corpus crudo (`data/raw/`), el procesado (`data/processed/`) y los índices derivados (`data/index/`, con `bm25.json` y el directorio `chroma/`); los tres están fuera de control de versiones porque derivan del contenido scrapeado ([ADR-0002](adr/0002-fuentes-de-datos-mvp.md)). El adaptador BM25 no importa el repositorio JSON de chunks aunque comparta su formato de chunk: la capacidad de consulta no depende de un adaptador de ingesta; el adaptador de Chroma copia el mismo codificador de chunk por la misma razón.

RRF vive en el dominio de `answering` porque es una función pura sobre rankings, no depende de ninguna librería.

---

## 8. Estrategia de pruebas

| Nivel | Qué prueba | Cómo |
|---|---|---|
| Unitario | `PatchVersion`, `Validity`, RRF, validación de citas, casos de uso | Fakes en memoria para cada puerto; registran sus llamadas para verificar orden y argumentos |
| Contrato | Cada adaptador cumple su puerto (ej.: el vector store respeta el filtro de vigencia) | Misma suite ejecutada contra cada implementación |
| Evaluación | Calidad de punta a punta | Dataset fijo; métricas Hit@K, MRR, fidelidad y **tasa de respuestas con datos obsoletos** |

La métrica de datos obsoletos es la que protege D1: una pregunta sobre `0.2.0` que recupera un chunk válido solo en `0.1.1` cuenta como fallo.

Hoy hay suites de contrato para `PatchCatalog`, `ChunkRepository`, `LexicalIndex` y `VectorIndex`. La de
`LexicalIndex` declara los bordes de vigencia como datos reutilizables (`VALIDITY_CASES`: `v == valid_from`
incluido, `v == valid_until` excluido, vigencia abierta, `v < valid_from` excluido), y la de `VectorIndex`
aplica esos mismos bordes a su propio `search` en vez de reescribirlos. La implementación en memoria del mismo
archivo es la especificación ejecutable del puerto: un test que pasa en una y falla en la otra señala al
adaptador.

El suite por defecto es **offline y rápido**: los adaptadores densos se prueban con un embedder determinista
(*hashed bag-of-words*, dimensión fija, normalizado) en `tmp_path`, y el embedder real solo corre en un suite
marcado `integration` que el `addopts` excluye (`uv run pytest -q -m integration` para correrlo a propósito).
Así la descarga del modelo y su costo de CPU no entran en cada corrida, pero la prueba de que el modelo real
produce vectores de 768 dimensiones y rankea un chunk relevante arriba de uno ajeno queda escrita y se puede
ejecutar cuando se quiera.

---

## 9. Decisiones abiertas

Cada una se registra como ADR en `docs/adr/` al resolverse.

| # | Decisión | Opciones | Impacto |
|---|---|---|---|
| A1 | Fuentes de datos v1 y permiso de uso | **Resuelta en parte** — [ADR-0002](adr/0002-fuentes-de-datos-mvp.md): poe2db, notas de parche y poe2wiki.net indexadas; foro solo para el dataset de evaluación. Datos minados todavía como candidata | Define los parsers y si se puede scrapear o hay que usar exportaciones |
| A2 | Idioma | **Resuelta** — [ADR-0001](adr/0001-idioma-mvp-ingles.md): MVP todo en inglés | Embedder monolingüe; BM25 sin mapeo de términos |
| A3 | Vector store | **Resuelta** — [ADR-0003](adr/0003-vector-store-chroma.md): Chroma embebido | Capacidad de pre-filtrado por rango de versión |
| A4 | Embeddings | **Resuelta** — [ADR-0004](adr/0004-embeddings-locales.md): local, `bge-base-en-v1.5` | Costo, privacidad, reproducibilidad de la evaluación |
| A5 | LLM | **Resuelta** — [ADR-0005](adr/0005-llm-por-api.md): Claude Sonnet 5.5 por API con citas nativas | Costo, calidad de citas, soporte de salida estructurada |
| A6 | Interfaz de entrada | **Resuelta** — [ADR-0006](adr/0006-cli-como-interfaz.md): solo CLI | Define el adaptador de entrada |
| A7 | Representación de vigencia en el vector store | **Resuelta** — [ADR-0003](adr/0003-vector-store-chroma.md): versión como entero ordenable y centinela para vigencia abierta | Depende de A3: no todos los stores filtran rangos de versión |

---

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Las fuentes cambian de formato y rompen los parsers | Guardar snapshots completos en `data/raw/` (gitignored, [ADR-0002](adr/0002-fuentes-de-datos-mvp.md)); los tests de parser corren sobre fixtures mínimas escritas a mano y versionadas en `tests/fixtures/`. |
| Las notas de parche describen cambios, no estados completos | En el MVP cada hilo se indexa como su propia entidad `patch_notes`, con una sección por encabezado y vigencia abierta desde su parche (§10.1). Aplicarlas como transiciones que cierran el `valid_until` de chunks de gemas requiere poe2db primero y queda fuera de este corte. |
| El título del hilo puede traer una versión con sufijo (`0.5.5e`) que `PatchVersion` no representa | El parser falla con `PatchVersionUnparseableError` en vez de mapearla a otro parche; si el MVP necesita esos parches, se amplía `PatchVersion` con una decisión explícita. |
| Encabezados repetidos, o dos encabezados que slugifican igual, producen un `chunk_id` repetido | El parser fusiona las secciones con el mismo slug en orden de documento, incluido el `overview` sintético; `IngestSource` conserva el rechazo con `DuplicateChunkIdError` como red de seguridad. |
| Dos hilos del mismo parche reclaman el mismo `chunk_id` (el id no incluye la URL) | `IngestSource` rechaza con `ChunkIdentityConflictError` un id ya almacenado desde otra URL; una recaptura de la misma URL es un update. |
| Una sección borrada del hilo sigue en el corpus | Deuda explícita: el puerto solo expresa upsert y no el alcance de un documento, así que no se poda; se resolverá cuando el corpus necesite reflejar borrados. |
| Un snapshot local no conserva ni la URL del hilo ni su fecha de captura | El parser recupera la URL de los enlaces `/forum/post-reply/<id>`; `LocalFileFetcher` exige un sidecar `<snapshot>.meta.json` con `captured_at` ISO 8601 con zona horaria y falla si falta o es inválido, en vez de usar el mtime. |
| Un índice léxico queda desactualizado respecto del corpus | El índice persiste el `corpus_fingerprint` del corpus desde el que se construyó, y también la versión del tokenizador y los parámetros de puntuación, así que un archivo escrito con otras reglas se rechaza en vez de responder. Comparar el fingerprint con el corpus y reindexar automáticamente es trabajo del próximo corte. |
| Un reindexado con dos escritores deja índices de versiones distintas | `rebuild` garantiza atomicidad por almacenamiento, no entre los dos escritores: un rebuild léxico exitoso seguido de uno vectorial fallido deja índices de corpus distintos. `ReindexCorpus` mitiga el caso barato (lee y embebe antes de escribir, así un corpus vacío o un modelo roto no tocan ningún índice) y el estado mixto restante queda como deuda explícita hasta que los dos índices compartan un puntero de versión. |
| Un reindexado sobre un corpus vacío reemplaza índices buenos por índices vacíos | `ReindexCorpus` aborta con `EmptyCorpusError` antes de llamar al embedder o a ningún escritor, y `rebuild` rechaza una secuencia vacía: el índice anterior sobrevive. |
| El modelo de embeddings cambia y los vectores del índice dejan de ser comparables | La colección guarda `embedding_model` y `dimension`, y `search` falla con `EmbeddingModelMismatchError` si el embedder configurado no coincide; cambiar de modelo implica reindexar, nunca comparar vectores de dos modelos (ADR-0004). |
| Un rebuild del índice denso deja la colección a medio llenar si el proceso muere | La colección nueva se llena entera antes de escribir el puntero con `os.replace`: hasta ese momento los lectores siguen viendo la colección anterior, y la nueva se descarta. El puntero no transacciona entre procesos (store local de un solo proceso, ADR-0003). |
| Un chunk más largo que la ventana del modelo queda a medio indexar en el índice denso | El embedder reporta las posiciones que superan los 512 tokens con el truncado apagado, `ReindexCorpus` las expone como `truncated_chunk_ids` y el índice léxico sigue leyendo el texto completo: el punto ciego es visible en vez de silencioso (ADR-0004). |
| La búsqueda léxica sin stemming no encuentra una palabra por su variante morfológica | Decisión deliberada (D2): `increased` y `more` son mecánicas distintas y `more` no es ruido. El recall semántico es trabajo del embedder y del reranker, no de la búsqueda léxica. |
| El LLM inventa valores numéricos | Validación de citas y regla de "sin respaldo" en el dominio |
| Sobreingeniería antes de tener datos reales | Implementar primero un corte vertical mínimo (una fuente, un índice, un LLM) y medir |

### 10.1 Modelo de vigencia de las notas de parche (MVP)

Una nota de parche **es su propia entidad**, no una transición aplicada a chunks de gemas:

- `EntityRef(kind="patch_notes", slug="<major>-<minor>-<patch>", name="Patch <version>")`;
- un `Chunk` por sección de primer nivel del hilo (`section` = slug del encabezado);
- `Validity(valid_from=<parche detectado>, valid_until=None)`: un cambio es un hecho desde ese parche en adelante.

Aplicar las notas como transiciones que cierran el `valid_until` de chunks de gemas exige primero los datos
completos de cada gema en poe2db; queda fuera del MVP y se retomará cuando esa fuente exista. Con este
modelo el filtro de vigencia de §5.1 sigue funcionando: una nota es válida desde su parche y el catálogo
decide cuál es el actual.
