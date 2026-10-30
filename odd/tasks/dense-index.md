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

## Progress / Evidence
- T1: branch `feat/dense-index` from `main` (`9e85960`). Baseline on that base: `uv run pytest -q` →
  **671 passed**. `data/index/` already covers the Chroma directory:
  `git check-ignore -v data/index/chroma/chroma.sqlite3` → `.gitignore:11:data/index/`; `git ls-files
  data` is empty. Dependencies: `uv add chromadb sentence-transformers` installed the CUDA build of
  torch first, because `[tool.uv.sources]` only applies to a direct dependency: `uv add torch` after
  the index configuration replaced it with `torch==2.15.0+cpu` from
  `https://download.pytorch.org/whl/cpu`, `uv sync` pruned the 17 `nvidia-*`/`triton` packages and
  `grep -c nvidia uv.lock` → `0` (venv 5.7G → 1.4G). No RED applicable (configuration only).

## Per-spec verdict
- S1 ✅ …
- S2 ✅ …
- S3 ✅ …
- S4 ✅ …
- S5 ✅ …
- S6 ✅ …
- S7 ✅ …
- S8 ✅ …
- S9 ✅ …
- S10 ✅ …
- S11 ✅ …
- S12 ✅ …

## Close
(Pending.)

## Deviations from the brief
(Pending.)
