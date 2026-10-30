# Feature: dense-index

## Objective
Implement the dense retrieval side of PoE2-RAG: a local `bge-base-en-v1.5` embedder and a
Chroma-backed `VectorIndex`, wired into `ReindexCorpus`, test-first.

## Specs
- **S1** — `answering/adapters/outbound/dense/version_codec.py`: pure encode/decode of `PatchVersion`
  and `Validity` to the ADR-0003 integers. `major * 1_000_000 + minor * 1_000 + patch`, each
  component `0..999`; a component above `999` is rejected; an open `valid_until` becomes the
  sentinel `2**31 - 1`. The encoding preserves `PatchVersion` order, so `0.1.10` (`1_010`) and
  `0.11.0` (`11_000`) stay distinct. The filter `valid_from_code <= v AND valid_until_code > v` is
  applied **inside** the query (ADR-0003).
- **S2** — `answering/adapters/outbound/dense/bge_embedder.py`: sentence-transformers wrapper around
  `BAAI/bge-base-en-v1.5`, local, 512-token limit, with model name, device and batch size injected;
  lazy model load. Queries get the BGE v1.5 retrieval instruction prefix, documents never do
  (ADR-0004). Vectors are L2-normalized. It exposes `model_id` and `dimension` (768) and reports
  which texts exceed the model's max sequence length, instead of truncating silently.
- **S3** — `answering/adapters/outbound/dense/chroma_index.py`: `ChromaVectorIndex(directory,
  collection_name, embedder)` over `chromadb.PersistentClient`. The collection stores
  `embedding_model`, `dimension` and `index_schema_version` in its metadata; `search` with an
  embedder whose model id or dimension differs raises a specific error (never compare vectors from
  different models). `rebuild` replaces the collection atomically from the caller's point of view
  (build a new collection, then swap/delete the old one) and stores the chunk data needed to return
  `RetrievedChunk`. `search` applies the validity `where` filter in the query, breaks equal scores
  by `chunk_id`, uses cosine distance and sets `scores.dense = 1 - distance` only.
- **S4** — `ingestion/application/ports.py` gains `DocumentEmbedder.embed_documents(texts) ->
  tuple[tuple[float, ...], ...]` plus `overflowing_indices(texts) -> tuple[int, ...]`, and
  `VectorIndexWriter.rebuild(chunks, vectors)`. `ReindexCorpus` takes the document embedder and the
  vector writer, embeds every chunk text, rebuilds the lexical and the vector index from one
  snapshot, and reports counts plus `truncated_chunk_ids`. The existing `ReindexCorpus` tests keep
  passing. `Embedder.embed(text)` and `VectorIndex.search(...)` in the answering side stay
  unchanged. One embedder adapter implements both embedder ports; one Chroma adapter implements both
  index ports.
- **S5** — Domain and application layers stay stdlib-only; `chromadb` and `sentence-transformers` are
  adapter-only dependencies. `torch` installs from the PyTorch CPU index (`[[tool.uv.index]]`
  `pytorch-cpu`, `explicit = true`, plus `[tool.uv.sources] torch = { index = "pytorch-cpu" }`), so
  no multi-GB CUDA wheels are pulled for a small local corpus.
- **S6** — `data/index/` covers the Chroma directory (already gitignored: confirm).
- **S7** — pytest registers an `integration` marker and excludes it by default
  (`addopts = -m "not integration"`). The default suite is offline and fast: a deterministic fake
  embedder (hashed bag-of-words, fixed dimension, normalized) drives `ChromaVectorIndex` in
  `tmp_path`; the version codec has unit tests (round-trip, ordering preserved, `0.1.10` vs `0.11.0`
  distinct, components `> 999` rejected); `ReindexCorpus` runs on fakes. The validity contract cases
  are reused for `VectorIndex` (`tests/contract/`): `v == valid_from` included, `v == valid_until`
  excluded, open validity, `v < valid_from` excluded, `limit` respected after filtering. Model
  mismatch, missing collection and the persistence round-trip (new client, same directory) each have
  a test.
- **S8** — Integration test (marked, not run by default): the real `bge-base-en-v1.5` model returns
  768-dim normalized vectors and ranks an obviously relevant chunk above an unrelated one. Run it
  once and report the result.
- **S9** — Out of scope: reranker, LLM adapter, `bootstrap.py`, CLI, query-time fusion changes.
- **S10** — Code, tests, docs and commit messages in English.
- **S11** — About 400 authored changed lines per task is a planning heuristic, not a cap.
- **S12** — Delivery: branch `feat/dense-index` from `main`; feature doc with task IDs, route per
  task and RED/GREEN evidence; one Conventional Commit per task; no `Co-Authored-By` or any AI
  attribution; do not push or open a PR. Report `<command>: <observed result>` and RED/GREEN counts
  per task.

## Non-goals
- The reranker, the LLM adapter, `bootstrap.py` and the CLI.
- Any change to `AnswerQuestion`'s fusion or to how it consumes the two index ports.
- Comparing the persisted index fingerprint against the corpus to detect a stale index.
- Deleting superseded index versions from the Chroma directory beyond the previous collection.

## Acceptance criteria
- `ReindexCorpus.execute` turns the processed corpus into a lexical index **and** a vector index, and
  reports the chunk ids whose text the embedder had to truncate.
- `ChromaVectorIndex.search` never returns a chunk that is not valid at the target patch, applies the
  filter before the limit, and sets only `scores.dense`.
- Querying an index built with another embedding model (or dimension) fails with a specific error;
  a missing collection fails with a specific error; a rebuild that fails leaves the previous index
  answering.
- `uv run pytest -q` green and offline; `uv run pytest -q -m integration` reports the real-model
  result separately.

## Checks
- `uv run pytest -q`
- `uv run pytest -q -m integration`
- Import boundary: `ingestion/application` and `answering/application` import only the standard
  library plus the shared/answering domain; only `answering/adapters/**` imports `chromadb` and
  `sentence_transformers`.
- `git check-ignore -v data/index/chroma/chroma.sqlite3`.

## Tasks
- [x] T1 — Branch `feat/dense-index`, feature doc, `data/index/` confirmation, dependency install
  (`pyproject.toml`, `uv.lock`) with the CPU torch index. Route: inline. S5, S6, S12.
- [x] T2 — `version_codec.py` and its unit tests. Route: inline. S1, S7.
- [x] T3 — `DocumentEmbedder` and `VectorIndexWriter` ports, `ReindexCorpus` wiring, ingestion fakes
  and tests. Route: inline. S4, S7.
- [x] T4 — `bge_embedder.py` and its offline unit tests. Route: inline. S2, S7.
- [x] T5 — `chroma_index.py`, its unit tests, the reusable `VectorIndex` contract suite and the
  deterministic hashing embedder. Route: inline. S3, S7.
- [x] T6 — Documentation: `docs/architecture.md` and `docs/adr/0004-embeddings-locales.md`.
  Route: inline. S1, S2, S3, S4.
- [x] T7 — Integration test, full suite, integration run and close. Route: inline. S8, S12.

## Log
- **L1 (user request, verbatim):** Implement the dense retrieval side of PoE2-RAG: a local embedder
  (bge-base-en-v1.5) and a Chroma-backed VectorIndex, wired into ReindexCorpus, test-first.
- **L2 (decision, parent):** the Chroma adapter receives the embedder it is paired with, so
  `embedding_model`/`dimension` have one source at write time and at read time; mismatch is detected
  against the collection metadata, never by comparing vectors.
- **L3 (decision, parent):** the atomic swap is a pointer file inside the Chroma directory, written
  with the BM25 adapter's `mkstemp` + `os.replace` pattern. Chroma cannot rename a collection
  atomically, and a crash between two renames would leave no collection under the canonical name.
- **L4 (decision, parent):** batching lives in the embedder adapter (injected `batch_size`), not in
  `ReindexCorpus`: the port takes the whole corpus in one call, so a second knob in the use case
  would duplicate the adapter's.
- **L5 (decision, parent):** the embed occurs before any write, so a broken model leaves both
  indexes untouched; the residual mixed state is only the lexical-then-vector rebuild risk already
  recorded in `docs/architecture.md` §10.
- T2: RED → collection `ModuleNotFoundError: No module named
  'poe2_rag.answering.adapters.outbound.dense'`, 0 tests run (47 cases written). GREEN → 47 passed; full
  suite **718 passed**. Seven error-path cases were added while green (non-`Validity`, non-integer bound,
  non-integer code) → 54 cases.
  - The encoding is hand-computed in the tests, not recomputed with the production formula: `0.1.2` →
    `1_002`, `0.10.0` → `10_000`, `0.1.10` → `1_010` and `0.11.0` → `11_000`, and the ordering test pairs
    `0.1.9 < 0.1.10`, `0.9.99 < 0.10.0`, `0.999.999 < 1.0.0` across component boundaries.
  - `decode_version` rejects every code whose components cannot fit in `0..999`, so the open-validity
    sentinel decodes only through `decode_bound`, never as a version (`major 2147`).
- T3: RED → `ImportError: cannot import name 'DocumentEmbedder' from
  'poe2_rag.ingestion.application'`, 0 tests run in the two touched files (4 new port cases).
  GREEN → full suite **735 passed**.
  - The embed runs before either writer: a model that cannot load leaves both persisted indexes untouched,
    and the test asserts the journal stops at `all, embed_documents`.
  - `EmbedderContractError` covers the two ways an embedder can break the batch pair: a vector count that
    does not match the corpus (checked before asking for overflow) and an overflow position outside the
    batch (checked before any writer runs). Neither can reach the writers.
  - Three existing `ReindexCorpus` cases asserted the exact port journal, so their expected sequence grew
    the two new calls; every other assertion is unchanged. The lexical pipeline test needed two local
    stand-ins for the dense half (see Deviations).
- T4: RED → `ModuleNotFoundError: ... dense.bge_embedder`, 0 tests run (28 cases written). First GREEN
  attempt: **1 failed, 27 passed**, and the failure was mine — the ordering test asked for a 3-wide
  embedder while the stub model produced 768, which the new dimension guard correctly rejected. Test
  corrected, then **28 passed**; full suite **770 passed**.
  - The model is never loaded in this suite: `_load_model` is monkeypatched, so the two rules that only
    the adapter owns (instruction on queries, never on documents; one `encode` call with the injected
    `batch_size`, `normalize_embeddings=True`, no progress bar) are pinned without importing torch.
  - The dimension guard closes the `model_id`/`dimension` loophole: a smaller model id left on the default
    768 configuration fails on first use instead of writing an index of the wrong width.
  - `overflowing_indices` counts tokens with `truncation=False`; a recording tokenizer pins that argument,
    because counting with truncation on would always report nothing.
- T5: delegated to `gentle-ai-worker` for the module, its unit suite and the reusable contract suite, after
  the parent fixed the surface, the error taxonomy and the swap design. RED was observed by the parent
  before the module existed: the two new suites failed collection with
  `ModuleNotFoundError: ... dense.chroma_index` (39 cases). The worker delivered; the parent reviewed it,
  found and fixed three defects, and added one test:
  - the pointer-write failure path was dead code: `_write_pointer` already wrapped `OSError` in
    `VectorIndexWriteError`, so `except OSError` never ran and the half-built collection leaked. Now the
    rebuild drops the unpromoted collection and re-raises, pinned by a new test that also proves the
    previous index still answers and that no orphan collection remains;
  - `collection.metadata` could be `None` on a foreign collection and raised `AttributeError`; it now falls
    back to `{}` so the schema check reports `UnsupportedIndexSchemaError`;
  - a record without a metadata object or without the `chunk` key raised `KeyError`; both now raise
    `IndexCorruptError` with the record id.
  GREEN → 26 adapter cases + 13 contract cases + 4 composition cases; full suite **813 passed**.
  - The swap is a pointer file written with the BM25 adapter's `mkstemp` + `fsync` + `os.replace` pattern:
    Chroma cannot rename a collection atomically, and a crash between two renames would leave no collection
    under the canonical name. The test that injects a failed `add` proves the old collection answers; the
    new test that injects a failed `mkstemp` proves the unpromoted collection is not left behind.
  - The contract suite imports `VALIDITY_CASES`, `make_chunk` and `chunk_ids` from
    `test_lexical_index_contract.py`, so the five validity boundaries and the filter-before-limit rule are
    the same data for both ports, and a deterministic 64-dimension hashed bag-of-words embedder keeps it
    offline.
- T6: documentation only, no RED applicable. `docs/architecture.md`: state header, §5.1 steps 2-3, §5.3
  rewritten for two writers and the embed-first order, a new §5.5 for the dense flow (embedder rules,
  collection metadata, in-query filter, pointer swap, identity check, total order, explicit failures), §6
  with the four ports and their write/read split, §7 with the adapter-only dependency rule and
  `data/index/chroma/`, §8 with the `VectorIndex` contract and the offline-by-default strategy, and §10
  with five risks (mixed state, model change, half-built collection, truncation blind spot) replacing the
  two stale rows. `docs/adr/0004-embeddings-locales.md`: the five implemented rules and the verified MIT
  license, plus the truncation consequence.
- T7: close. `uv run pytest -q` → **813 passed, 6 deselected** (baseline 671, **+142**).
  `uv run pytest -q -m integration` → **6 passed, 813 deselected in 4.29s** (first run 21.04s; the model
  is cached afterwards). Per suite: version codec 54, `BgeEmbedder` 28, `ChromaVectorIndex` 26, vector
  contract 13, dense composition 4, `ReindexCorpus` 24 (11 before), ingestion ports 28 (24 before).
  - `uvx ruff check --select I src tests` → `All checks passed`; `uvx pyright==1.1.414` (whole workspace,
    repo config) → `0 errors, 0 warnings, 0 informations`.
  - Import boundary: `grep -rl "import chromadb|from chromadb|sentence_transformers" src` lists exactly
    the two dense adapters (plus bytecode); `ingestion/application` and `answering/application` import only
    the standard library, `typing` and the domain.
  - `git ls-files data` → 0 files; `git check-ignore -v data/index/chroma/chroma.sqlite3` →
    `.gitignore:11:data/index/`; `git log main..HEAD --format=%B | grep -ci 'co-authored-by|generated
    with|claude|anthropic'` → `0`; `git ls-remote --heads origin feat/dense-index` → nothing.
  - Environment note, same class the previous feature recorded: the editor LSP reports `poe2_rag` imports
    in test files as unresolved and flags an import-order error that the repository's own tools do not
    see. Proof that the code is fine: `uv run python -c "from
    poe2_rag.answering.adapters.outbound.dense.chroma_index import ChromaVectorIndex"` imports,
    `uv run pytest -q -m integration --collect-only` collects 6 tests, `uvx ruff check --select I src tests`
    passes from the repository root, and `uvx pyright` over the workspace reports 0 errors. Run from
    outside the repository root, ruff reproduces the LSP message, which places the cause in the analyzer's
    working directory rather than in the imports.

## Per-spec verdict
- S1 ✅ `version_codec.py` encodes `major * 1_000_000 + minor * 1_000 + patch` with components `0..999`,
  the `2**31 - 1` sentinel for an open bound and a specific error above `999`; 54 cases pin round-trip,
  cross-component order, `0.1.10` vs `0.11.0`, the sentinel and the rejected codes. The filter
  `valid_from_code <= v AND valid_until_code > v` is applied inside the Chroma query (§5.5).
- S2 ✅ `BgeEmbedder` defaults to `BAAI/bge-base-en-v1.5`, loads lazily, applies the retrieval instruction to
  queries only, asks for L2 normalization, injects model id, device, batch size, dimension, instruction and
  the 512-token limit, exposes `model_id`/`dimension`, verifies the loaded width and reports overflow
  positions with truncation off. 28 offline cases plus the real-model integration cases.
- S3 ✅ `ChromaVectorIndex(directory, collection_name, embedder=…)` over `chromadb.PersistentClient`; the
  collection stores `index_schema_version`, `embedding_model`, `dimension` and `chunk_count`; a model or
  dimension mismatch raises `EmbeddingModelMismatchError`; the rebuild builds a new collection, promotes it
  through an atomically replaced pointer and deletes the old one; `search` filters inside the query, breaks
  ties by `chunk_id`, uses cosine distance and sets only `scores.dense`. 26 adapter cases.
- S4 ✅ `DocumentEmbedder.embed_documents`/`overflowing_indices` and `VectorIndexWriter.rebuild(chunks,
  vectors)` live in `ingestion/application/ports.py`; `ReindexCorpus` takes both, embeds the whole corpus
  before writing, rebuilds both indexes from one snapshot and reports `truncated_chunk_ids`; the answering
  `Embedder`/`VectorIndex` ports are unchanged; one adapter serves each pair. 24 `ReindexCorpus` cases and
  4 composition cases.
- S5 ✅ `chromadb`, `sentence-transformers` and `torch` are adapter-only dependencies; `torch` resolves from
  `https://download.pytorch.org/whl/cpu` (`torch==2.15.0+cpu`, no `nvidia-*`/`triton` in `uv.lock`);
  `ingestion/application` and `answering/application` import only the standard library and the domain.
- S6 ✅ `data/index/` already covered the Chroma directory; `git check-ignore -v
  data/index/chroma/chroma.sqlite3` → `.gitignore:11:data/index/` and `git ls-files data` is empty.
- S7 ✅ the `integration` marker is registered and excluded by `addopts`; the default suite drives
  `ChromaVectorIndex` with a deterministic hashed bag-of-words embedder in `tmp_path`; the codec, the
  fakes-based `ReindexCorpus`, the model mismatch, the missing collection and the persistence round-trip all
  have offline tests; the contract suite reuses `VALIDITY_CASES` for `VectorIndex`.
- S8 ✅ `tests/integration/test_dense_retrieval_integration.py` (6 cases) passes against the real model:
  768-dimension unit vectors, the 512-token overflow report and both rankings.
- S9 ✅ no reranker, LLM adapter, `bootstrap.py`, CLI or fusion change; `AnswerQuestion` and its ports are
  untouched.
- S10 ✅ code, tests, feature document and commit messages in English. The two Markdown documents under
  `docs/` stay Spanish (see Deviations).
- S11 ✅ the heuristic is recorded in Delivery slices; the largest task is justified there.
- S12 ✅ branch `feat/dense-index` from `main`, one Conventional Commit per task, no attribution, no push,
  no PR; every command and its observed result is in Progress / Evidence.

## Close
All tasks are done, including the fix round below. `uv run pytest -q` → **823 passed, 6 deselected**;
`uv run pytest -q -m integration` → **6 passed, 823 deselected**; `uvx ruff check --select I src tests` → clean; `uvx pyright==1.1.414` →
0 errors. The branch holds nine commits and nothing is pushed.

Open follow-ups, deliberately out of scope: a reranker over the fused candidates, the LLM adapter, the
`bootstrap.py` composition root that wires one embedder and one store to both halves, the CLI commands, and
comparing the persisted corpus fingerprint with the corpus to detect a stale index. The mixed-state risk
between the two writers stays registered in `docs/architecture.md` §10 until both indexes share one
version pointer.

## Fix round 1 (Herdr ronda 1/2)
Applied every qa and review finding of round 1, test-first: each one has a test that failed before the
fix and passes after it, except the two documentation-only findings.

- **Total order broken past the limit** (qa, `chroma_index.py`): `search` asked Chroma for `limit` results
  and only then sorted by `(-score, chunk_id)`, so a tie group larger than the limit was cut by the store.
  Ten identical vectors with `limit=3` returned the store's arbitrary picks, not `s00, s01, s02`. The query
  now asks for every row matching the validity filter (`n_results = max(count(), limit)`), sorts, and cuts
  after the tie-break, which is what the port and §5.5 promise and what `Bm25Index` already did. Pinned by
  `test_a_tie_group_larger_than_the_limit_breaks_by_chunk_id` for `limit=3` and `limit=5`.
- **`search` wrote to disk and leaked foreign exceptions** (qa): it opened the client (mkdir +
  `PersistentClient`) before reading the pointer, so a read-only process materialized the index directory,
  and a store that could not be opened escaped as a raw `chromadb`/sqlite error. `search` now reads the
  pointer first, opens the client with `create_directory=False`, and maps any open/read/query failure to
  `VectorIndexReadError`. Pinned by `test_a_search_that_finds_no_index_creates_nothing` and
  `test_a_broken_store_raises_a_read_error`.
- **`rebuild` only cleaned up after `ChromaError`** (qa): any other exception from `Collection.add` left the
  half-built collection on disk and escaped unwrapped. The add and pointer paths now catch `Exception`,
  delete the unpromoted collection and raise `VectorIndexWriteError`. Pinned by
  `test_any_add_failure_cleans_up_and_wraps` (a `ValueError` from `add`).
- **The swap was not durable** (qa): the pointer file was `fsync`ed but its directory entry was not, and the
  previous collection was deleted immediately afterwards, so a lost rename would leave the pointer naming a
  deleted collection with nothing to fall back to. `_write_pointer` now calls `_fsync_directory` after
  `os.replace` and before the old collection is removed. Pinned by `test_the_pointer_replace_is_made_durable`
  (a recording `os.fsync` proves a directory descriptor was flushed).
- **§10 described a crash mitigation that does not exist** (qa): the row claimed the unpromoted collection is
  discarded on process death. It is not; it survives as an orphan until the next successful rebuild reaps it.
  The row now says exactly that, and the new segment reaping is recorded in §5.5 and in its own §10 row.
- **ADR-0004 claimed a chunker enforces 512 tokens** (qa): there is no token-based chunker, and a patch-note
  section exceeds the window routinely. The consequence now states that nothing guarantees the limit, that
  the model truncates silently, and that `truncated_chunk_ids` exists precisely for that.
- **`_truncated_chunk_ids` promised an order it did not impose** (qa): the report followed the embedder's
  positions, so `(2, 0)` produced chunk 2 before chunk 0. The positions are now sorted before mapping, and
  `test_reports_truncated_chunks_in_corpus_order` pins `(2, 0)` → `(chunk0, chunk2)`.
- **Vectors were accepted only as `list`/`tuple`** (qa): `numpy.ndarray` is not a `Sequence`, so the natural
  output of `SentenceTransformer.encode` was rejected. `_normalize_vector` now accepts any iterable of
  numbers, converts with `float()` (which also accepts `numpy` scalars), rejects booleans and non-finite
  values, and returns the dimension-checked list. Pinned by `test_an_ndarray_query_vector_is_accepted`.
- **Disk leak: one HNSW segment directory per rebuild** (review): `delete_collection` removes the catalog rows
  but leaves the segment directory, so every reindex leaked one full copy of the vectors, and the suppressed
  `ChromaError` hid it. `_reap_segment_directories` reads the live segment ids from the store's own catalog
  and deletes the unreferenced segment directories; if the catalog cannot be read it deletes nothing.
  Pinned by `test_a_second_rebuild_reclaims_the_previous_segment_directory`.
- **`BgeEmbedder` never applied `max_sequence_length`** (review): the model truncated at its own window while
  the overflow report compared against the injected value, so the report could lie in both directions. The
  limit is now assigned to `model.max_seq_length` on load. Pinned by
  `test_applies_the_sequence_limit_to_the_loaded_model`.
- **README contradicted the shipped slice** (review): the state line and the roadmap still said the dense half
  was missing, and the tree listed `data/index/` as BM25 only. All three now describe what exists.
- **ADR-0003 claimed BM25 shared the integer encoding** (review): the lexical adapter filters on domain
  objects. The bullet now states that the encoding is exclusive to the Chroma adapter.
- **The codec error escaped the adapter taxonomy** (review): `rebuild` propagated `VersionComponentError`,
  a bare `ValueError`, while every other invalid input raised `VectorIndexError`. It is wrapped, and
  `test_a_version_component_above_999_leaves_previous_index_intact` now expects `VectorIndexError`.
- **A test asserted laziness while performing the write it denied** (review): it called `search`, which
  created the store, and only checked the exception. It is now two tests: construction creates nothing, and a
  search that finds no index creates nothing either.
- **The Chroma unit suite duplicated the contract's hashing embedder** (review): two copies of the fake the
  expected vectors depend on. It moved to `tests/hashing_embedder.py`, which both suites import; a new
  `tests/conftest.py` puts `tests/` on `sys.path` so any suite can reach it.

Gates after the round: `uv run pytest -q` → **823 passed, 6 deselected**; `uv run pytest -q -m integration` →
**6 passed, 823 deselected**; `uvx ruff check --select I src tests` → `All checks passed`;
`uvx pyright==1.1.414` → `0 errors, 0 warnings, 0 informations`. Per suite: Chroma adapter 34 (26 before),
`BgeEmbedder` 29 (28), vector contract 13, dense composition 4, `ReindexCorpus` 25 (24), codec 54.

## Deviations from the brief
- **Documentation language.** `docs/architecture.md` and `docs/adr/0004-embeddings-locales.md` were updated
  in Spanish. The brief asks for docs in English, but both files are written in Spanish throughout and the
  two previous features kept them that way; code, tests, the feature document and commit messages are
  English.
- **Batching.** The brief puts "embeds all chunk texts in batches" on `ReindexCorpus`; batching lives in the
  embedder adapter instead (injected `batch_size`, one `embed_documents` call per corpus). The batch size is
  a property of the model and the device, and a second knob in the use case would duplicate it (L4).
- **The pointer file.** Chroma cannot rename a collection atomically, so the swap-promise is implemented as
  a pointer file written with the BM25 adapter's atomic-replace pattern (L3). The collection is still built
  new and the old one deleted, as the brief describes.
- **`torch` is a direct dependency.** `[tool.uv.sources]` only redirects a direct dependency, so
  `uv add chromadb sentence-transformers` alone installed the CUDA build. Declaring `torch` on the CPU index
  and running `uv sync` replaced it with `torch==2.15.0+cpu` and pruned 17 accelerator packages.
- **The embedder identity.** `search` receives only a vector, so the Chroma adapter is constructed with the
  embedder it is paired with and reads `model_id`/`dimension` from it (a `runtime_checkable`
  `EmbedderIdentity` protocol local to the adapter). That is how "search with an embedder whose model id or
  dimension differs" is detected; the answering `Embedder` port keeps its single `embed` method.
- **Two ports grew one method each.** `DocumentEmbedder` carries `overflowing_indices(texts)` so the
  truncation report has a source, and `ReindexCorpus` gained `EmbedderContractError` to reject a batch that
  does not pair one vector with one chunk. Neither changes an existing port.
- **One file outside the allowed surfaces.** `tests/unit/ingestion/test_reindex_lexical_pipeline.py` composes
  `ReindexCorpus` and had to learn the two new required constructor arguments; the change is two local
  stand-ins plus a helper. Making the ports optional would have been the alternative, and it would weaken
  production code to spare a test edit.
- **`ReindexCorpus` call sites.** The constructor parameters are required keyword-only arguments, so the
  existing tests were adapted (a `Harness` dataclass and the extended port journal); their assertions are
  otherwise unchanged. `ReindexReport.truncated_chunk_ids` defaults to `()`, so a report can still be built
  without it.
- **T1 landed as two commits** (plan document, then dependencies) instead of one, so the dependency change is
  reviewable apart from the plan.
- **T2 was split out of T3 with a rebase** after it had been committed together, to honour one commit per
  task. The branch is local, so the rewrite touched nothing published.
- **An extra `fix(...)` commit** updates the dimension read to `get_embedding_dimension`, the name the
  installed sentence-transformers uses; the deprecated call worked but warned on every run.
- **An extra `[tool.ruff.lint.isort]` section** in `pyproject.toml` declares `poe2_rag` as first-party. The
  repository does not depend on ruff, but any isort-compatible tool run from outside the repository root
  otherwise classifies the package as third-party and disagrees with the repository's own check.
- **T5 was delegated.** The module and its two suites were written by `gentle-ai-worker` against a fixed
  surface and error taxonomy; the parent authored the dense composition suite, reviewed the delivered code,
  fixed three defects (dead pointer-failure cleanup, a `None` collection metadata crash, a `KeyError` on a
  malformed record) and added the test that pins the pointer failure path.

## Delivery slices
- One commit per task, except T1 (plan + dependencies) and T7 (integration suite + this close record), and
  the `fix(...)` follow-up for the renamed sentence-transformers method.
- T5 is ~1,450 authored lines across the adapter, its 26 cases and the 13-case contract suite. It does not
  split: the adapter and both suites pin the same `search`/`rebuild` surface and cannot reach green in
  separate commits, so the task lands whole, as the previous feature did for its BM25 adapter.
- T3 (452 lines) includes the mechanical adaptation of the lexical pipeline test and three existing
  `ReindexCorpus` cases; the behaviour it adds is the embed-then-rebuild wiring.
