"""Retrieval metrics as pure functions: Hit@K, MRR and the stale-retrieval guard (§8, D4).

The input is what one retrieval run produced for one case, already reduced to ids and a rank: no
chunk, no text and no port. The output is arithmetic over the answerable cases only, because a case
whose expected list is empty has no correct answer to find and scoring it as a miss would punish the
system for behaving correctly.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from poe2_rag.evaluation.domain.errors import InvalidMetricsError
from poe2_rag.shared.domain import is_blank


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """What retrieval delivered for one case: the ids, the rank of the first hit and the stale ids.

    `first_hit_rank` is 1-based and `None` when nothing expected was delivered. `stale_chunk_ids`
    holds the delivered chunks that are not valid at the case's target patch; it is always empty when
    the validity filter ran before fusion, which is exactly what the metric checks (D1).
    """

    case_id: str
    answerable: bool
    tags: tuple[str, ...]
    delivered_chunk_ids: tuple[str, ...]
    first_hit_rank: int | None = None
    stale_chunk_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or is_blank(self.case_id):
            raise InvalidMetricsError(f"case_id must be a non-blank string, got {self.case_id!r}")
        if not isinstance(self.answerable, bool):
            raise InvalidMetricsError(f"answerable must be a bool, got {self.answerable!r}")
        _require_texts("tags", self.tags)
        _require_texts("delivered_chunk_ids", self.delivered_chunk_ids)
        _require_texts("stale_chunk_ids", self.stale_chunk_ids)
        if not set(self.stale_chunk_ids) <= set(self.delivered_chunk_ids):
            raise InvalidMetricsError(
                f"stale_chunk_ids must have been delivered, got {self.stale_chunk_ids!r} "
                f"against {self.delivered_chunk_ids!r}"
            )
        self._require_rank()

    def _require_rank(self) -> None:
        """A rank is a position inside the delivery, and only an answerable case can have one."""
        rank = self.first_hit_rank
        if rank is None:
            return
        if isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0:
            raise InvalidMetricsError(f"first_hit_rank must be a positive int, got {rank!r}")
        if not self.answerable:
            raise InvalidMetricsError(
                f"first_hit_rank only applies to an answerable case, got {self.first_hit_rank!r} "
                f"for {self.case_id!r}"
            )
        if rank > len(self.delivered_chunk_ids):
            raise InvalidMetricsError(
                f"first_hit_rank {rank} is beyond the {len(self.delivered_chunk_ids)} delivered chunks "
                f"of {self.case_id!r}"
            )


@dataclass(frozen=True, slots=True)
class Metrics:
    """One metrics table: how many cases were evaluated and the three numbers D4 compares.

    Hit@K and MRR are computed over `answerable_cases`: a case without an expected chunk has no correct
    answer to find. The stale guard is computed over `evaluated_cases`, every case the run measured,
    because a chunk that is not valid at the target patch is a D1 failure whether or not the case
    expected an answer; an unanswerable case left out of that denominator could deliver a stale chunk
    while the report printed a clean zero. Both denominators travel with the numbers, and neither is
    called `cases`: `dataset.cases` is the whole dataset, and one name cannot mean two universes.
    """

    answerable_cases: int
    evaluated_cases: int
    hit_at_k: tuple[tuple[int, float], ...]
    mrr: float
    stale_retrieval_rate: float

    def __post_init__(self) -> None:
        _require_count("answerable_cases", self.answerable_cases)
        _require_count("evaluated_cases", self.evaluated_cases)
        if self.answerable_cases > self.evaluated_cases:
            raise InvalidMetricsError(
                f"answerable_cases ({self.answerable_cases}) cannot exceed "
                f"evaluated_cases ({self.evaluated_cases})"
            )
        if not isinstance(self.hit_at_k, tuple):
            raise InvalidMetricsError(f"hit_at_k must be a tuple, got {self.hit_at_k!r}")
        for entry in self.hit_at_k:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise InvalidMetricsError(f"hit_at_k entries must be (k, rate) pairs, got {entry!r}")
            k, rate = entry
            _require_k(k)
            _require_rate("hit_at_k", rate)
        _require_rate("mrr", self.mrr)
        _require_rate("stale_retrieval_rate", self.stale_retrieval_rate)

    def as_dict(self) -> dict[str, object]:
        """Render the table for a report, with the K keys as strings so JSON stays an object."""
        return {
            "answerable_cases": self.answerable_cases,
            "evaluated_cases": self.evaluated_cases,
            "hit_at_k": {str(k): rate for k, rate in self.hit_at_k},
            "mrr": self.mrr,
            "stale_retrieval_rate": self.stale_retrieval_rate,
        }


def first_hit_rank(
    delivered_chunk_ids: Sequence[str], expected_chunk_ids: Sequence[str]
) -> int | None:
    """Return the 1-based position of the first expected chunk in the delivery, or `None`."""
    expected = set(expected_chunk_ids)
    for rank, chunk_id in enumerate(delivered_chunk_ids, start=1):
        if chunk_id in expected:
            return rank
    return None


def require_ks(ks: Sequence[int]) -> tuple[int, ...]:
    """Return `ks` as a sorted tuple of distinct positive ints, or fail loud.

    A repeated K would duplicate a column of the table and a zero or negative one has no meaning, so
    both are rejected instead of being silently normalized.
    """
    if isinstance(ks, (str, bytes)) or not isinstance(ks, Sequence):
        raise InvalidMetricsError(f"ks must be a sequence of ints, got {ks!r}")
    for k in ks:
        _require_k(k)
    if not ks:
        raise InvalidMetricsError("ks must not be empty: a run with no K measures nothing")
    if len(set(ks)) != len(ks):
        raise InvalidMetricsError(f"ks must not repeat a value, got {tuple(ks)!r}")
    return tuple(sorted(ks))


def score(outcomes: Sequence[CaseOutcome], ks: Sequence[int]) -> Metrics:
    """Compute the metrics table over the answerable outcomes in `outcomes`.

    A set with no answerable case scores zero on Hit@K and MRR and reports `answerable_cases == 0`: the
    aggregate is invalid input for a conclusion, never an error, and the report shows the denominator.
    The stale guard keeps its own denominator (`evaluated_cases`), so it still sees what those cases
    delivered.
    """
    ordered_ks = require_ks(ks)
    _require_outcomes(outcomes)
    scored = tuple(outcome for outcome in outcomes if outcome.answerable)
    ranks = tuple(outcome.first_hit_rank for outcome in scored)
    return Metrics(
        answerable_cases=len(scored),
        evaluated_cases=len(outcomes),
        hit_at_k=tuple((k, _share(_hits_within(ranks, k), len(scored))) for k in ordered_ks),
        mrr=_mean(_reciprocal_rank(rank) for rank in ranks),
        stale_retrieval_rate=_share(
            sum(1 for outcome in outcomes if outcome.stale_chunk_ids), len(outcomes)
        ),
    )


def score_by_tag(outcomes: Sequence[CaseOutcome], ks: Sequence[int]) -> dict[str, Metrics]:
    """Compute one metrics table per tag, in tag order, over the answerable cases that carry it.

    A case with several tags counts in each of them, so the tag tables describe overlapping slices of
    the dataset while the aggregate stays the only total. A tag whose only cases are unanswerable is
    omitted: it would be a row that measures nothing.
    """
    require_ks(ks)
    _require_outcomes(outcomes)
    tags = sorted({tag for outcome in outcomes if outcome.answerable for tag in outcome.tags})
    return {
        tag: score([outcome for outcome in outcomes if tag in outcome.tags], ks) for tag in tags
    }


def _hits_within(ranks: Sequence[int | None], k: int) -> int:
    """Count the cases whose first hit is at rank `k` or better."""
    return sum(1 for rank in ranks if rank is not None and rank <= k)


def _reciprocal_rank(rank: int | None) -> float:
    """1/rank for a case that hit, 0 for one that did not: the MRR contribution."""
    return 0.0 if rank is None else 1 / rank


def _share(count: int, total: int) -> float:
    """Return `count / total`, or 0.0 when nothing was scored, keeping the rate finite."""
    return 0.0 if total == 0 else count / total


def _mean(values: Iterable[float]) -> float:
    """Return the mean of `values`, or 0.0 when the iterable is empty."""
    collected = tuple(values)
    return 0.0 if not collected else sum(collected) / len(collected)


def _require_outcomes(outcomes: object) -> None:
    """Require a sequence of `CaseOutcome`, so a caller cannot score raw ids by accident."""
    if isinstance(outcomes, (str, bytes)) or not isinstance(outcomes, Sequence):
        raise InvalidMetricsError(f"outcomes must be a sequence of CaseOutcome, got {outcomes!r}")
    for outcome in outcomes:
        if not isinstance(outcome, CaseOutcome):
            raise InvalidMetricsError(f"outcomes must be CaseOutcome values, got {outcome!r}")


def _require_texts(name: str, values: object) -> None:
    """Require a tuple of non-blank strings under `name`."""
    if not isinstance(values, tuple):
        raise InvalidMetricsError(f"{name} must be a tuple, got {values!r}")
    for value in values:
        if not isinstance(value, str) or is_blank(value):
            raise InvalidMetricsError(f"{name} must hold non-blank strings, got {value!r}")


def _require_count(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidMetricsError(f"{name} must be a non-negative int, got {value!r}")


def _require_k(k: object) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise InvalidMetricsError(f"k must be a positive int, got {k!r}")


def _require_rate(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidMetricsError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise InvalidMetricsError(f"{name} must be between 0 and 1, got {value!r}")
