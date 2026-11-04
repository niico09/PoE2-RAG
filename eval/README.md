# Evaluation dataset format

`poe2-rag eval` measures retrieval only: retrieval is deterministic, so the same corpus and the same
question always produce the same ranking (D4, [ADR-0005](../docs/adr/0005-llm-por-api.md)). Generation
metrics (faithfulness, multi-run averaging) are a later feature and nothing in this file covers them.

## Where the files live

| Path | Purpose |
|---|---|
| `eval/dataset.jsonl` | The dataset the command measures by default. **Not committed**: writing it is your job. |
| `eval/dataset.example.jsonl` | Four cases written against the committed test fixtures. It doubles as the integration dataset. |
| `data/eval/runs/` | Where the JSON reports land. Gitignored: a report is a local artifact. |

Override the paths with `--dataset` / `--output-dir`, or with `POE2_RAG_EVAL_DATASET` /
`POE2_RAG_EVAL_RUNS_DIR`.

## The format

JSON Lines: one case per line, UTF-8, blank lines ignored. Every line is a JSON object.

| Field | Required | Type | Meaning |
|---|---|---|---|
| `schema_version` | yes | integer | Format version. Must be `1`; a different value is rejected instead of guessed. |
| `id` | yes | string | Unique slug (`spark-mana-cost`): lowercase words joined by single dashes. |
| `question` | yes | string | The question, in English, exactly as a user would type it. |
| `expected_chunk_ids` | yes | list of strings | The chunks a correct retrieval should deliver. Empty means "the corpus does not answer this". |
| `patch` | no | string | Patch to answer for (`"0.1.0"`). Absent means "the current patch", resolved by the catalog. |
| `expected_answer` | no | string | Short free text kept for the generation stage. Not used by the retrieval metrics yet. |
| `tags` | no | list of strings | Free labels used to group the report (`gems`, `patch-notes`, ...). |
| `any other key` | — | — | Rejected. A silently ignored typo is a case that lies about what it measures. |

A case with `expected_chunk_ids: []` is **unanswerable**: it is counted and listed in the report but
never scored as a hit or a miss, because there is no correct answer to find.

A line that cannot be read fails the whole run with the line number, and so does a repeated `id`.

```json
{"schema_version": 1, "id": "spark-mana-cost", "question": "How much mana does Spark cost?", "expected_chunk_ids": ["poe2db:active_gem:spark@0.1.1#properties"], "expected_answer": "Spark costs 12 mana in 0.1.1.", "tags": ["gems"]}
```

## Dataset drift

Before retrieving anything, every `expected_chunk_id` is checked against the current corpus. An id the
corpus does not have fails the run and is listed: a dataset that no longer matches the corpus cannot
produce numbers comparable with a previous run, so nothing is evaluated instead.

`chunk_id` carries the source, the entity, the patch it became valid at and the section
(`poe2db:active_gem:spark@0.1.1#properties`). Re-ingesting a snapshot under a new patch therefore
changes the ids, and the dataset has to follow.

## Metrics

`K` defaults to `1,3,5` and is set with `--k`. No `K` may exceed the number of chunks retrieval
delivers (`final_top_k`, today 5): retrieval cannot return more, so a larger `K` would repeat the last
column while looking like a wider measurement, and the run is refused instead.

`Hit@K` and `MRR` are computed over the **answerable** cases. The stale guard has its own denominator,
every case the run evaluated, because a chunk that is not valid at the target patch is a D1 failure
whether or not the case expected an answer. The report carries both denominators (`answerable_cases`
and `evaluated_cases`, next to the dataset's own `cases`), so a zero is always readable against what
produced it.

| Metric | Definition |
|---|---|
| `Hit@K` | Share of answerable cases whose first expected chunk appears at rank `K` or better. |
| `MRR` | Mean of `1 / rank` of the first expected chunk found, over the answerable cases; a case that found nothing contributes `0`. |
| `stale-retrieval rate` | Share of evaluated cases where any delivered chunk is not valid at the case's target patch. It should be `0`, because the validity filter runs before fusion; it exists as a guard for D1. |

Each metric is also reported per tag. A case with two tags counts in both tag tables while the
aggregate stays the total, so tag tables overlap by design.

## Running it

```bash
uv run poe2-rag eval
uv run poe2-rag eval --dataset eval/dataset.example.jsonl --k 1,5 --output-dir /tmp/eval-runs
```

The command prints a compact table and writes a JSON report with the run timestamp, the git commit, the
dataset path and digest, the config snapshot (model ids and retrieval budgets), the aggregate metrics,
the per-tag metrics and one record per case (rank of the first hit, delivered ids, stale ids). The
report holds ids only, never chunk text. Evaluation needs no API key: it never builds the Anthropic
client.
