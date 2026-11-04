"""The retrieval metrics as pure functions, with the arithmetic worked out by hand (§8, D4)."""

from __future__ import annotations

import pytest

from poe2_rag.evaluation.domain import (
    CaseOutcome,
    InvalidMetricsError,
    Metrics,
    first_hit_rank,
    score,
    score_by_tag,
)


def outcome(
    case_id: str,
    *,
    delivered: tuple[str, ...] = ("a", "b"),
    rank: int | None = None,
    tags: tuple[str, ...] = (),
    stale: tuple[str, ...] = (),
    answerable: bool = True,
) -> CaseOutcome:
    """Build one scored case, so a metrics test reads as data plus the expected arithmetic."""
    return CaseOutcome(
        case_id=case_id,
        answerable=answerable,
        tags=tags,
        delivered_chunk_ids=delivered,
        first_hit_rank=rank,
        stale_chunk_ids=stale,
    )


def rates(metrics: Metrics) -> tuple[tuple[int, float], ...]:
    """Return the `(k, rate)` pairs, so an assertion can compare the whole table at once."""
    return metrics.hit_at_k


def test_first_hit_rank_finds_the_expected_id_at_its_position() -> None:
    """Ranks start at 1: the first delivered chunk is rank 1, not rank 0."""
    assert first_hit_rank(("a", "b", "c"), ("c",)) == 3


def test_first_hit_rank_uses_the_earliest_expected_id() -> None:
    """A case with several acceptable chunks is a hit at the first one that appears."""
    assert first_hit_rank(("a", "c", "b"), ("b", "c")) == 2


def test_first_hit_rank_is_none_when_nothing_expected_was_delivered() -> None:
    assert first_hit_rank(("a", "b"), ("c", "d")) is None


def test_first_hit_rank_is_none_for_an_empty_delivery() -> None:
    assert first_hit_rank((), ("c",)) is None


def test_a_hit_at_rank_one_is_a_hit_at_every_k() -> None:
    metrics = score([outcome("spark", rank=1)], (1, 3, 5))

    assert rates(metrics) == ((1, 1.0), (3, 1.0), (5, 1.0))


def test_a_hit_at_k_plus_one_is_a_miss_for_k() -> None:
    """The boundary that decides whether the budget is respected: rank 4 misses Hit@3 and hits Hit@5."""
    metrics = score([outcome("spark", delivered=("a", "b", "c", "d"), rank=4)], (3, 5))

    assert rates(metrics) == ((3, 0.0), (5, 1.0))


def test_hit_at_k_is_the_share_of_scored_cases_with_a_rank_within_k() -> None:
    """Three answerable cases at ranks 1, 2 and no hit: 1/3 at Hit@1 and 2/3 from Hit@3 on."""
    metrics = score(
        [outcome("one", rank=1), outcome("two", rank=2), outcome("three", rank=None)],
        (1, 3, 5),
    )

    assert rates(metrics) == ((1, pytest.approx(1 / 3)), (3, pytest.approx(2 / 3)), (5, pytest.approx(2 / 3)))
    assert metrics.answerable_cases == 3


def test_mrr_averages_one_over_the_first_hit_rank() -> None:
    """(1/1 + 1/4 + 0) / 3, with a case that never hit contributing zero."""
    metrics = score(
        [
            outcome("one", rank=1),
            outcome("four", delivered=("a", "b", "c", "d"), rank=4),
            outcome("miss", rank=None),
        ],
        (1, 3, 5),
    )

    assert metrics.mrr == pytest.approx((1.0 + 0.25) / 3)


def test_unanswerable_cases_are_never_scored_as_a_hit_or_a_miss() -> None:
    """A case the corpus cannot answer has no rank, so it must not move any denominator."""
    scored = score([outcome("one", rank=1), outcome("two", rank=None)], (1, 3, 5))
    with_unanswerable = score(
        [
            outcome("one", rank=1),
            outcome("two", rank=None),
            outcome("unanswerable", delivered=("a", "b"), answerable=False),
        ],
        (1, 3, 5),
    )

    assert with_unanswerable.hit_at_k == scored.hit_at_k
    assert with_unanswerable.mrr == scored.mrr
    assert with_unanswerable.answerable_cases == scored.answerable_cases == 2
    # The stale guard has its own denominator, so it sees the extra case while nothing else moves.
    assert with_unanswerable.evaluated_cases == 3


def test_stale_retrieval_rate_is_zero_when_every_delivered_chunk_is_valid() -> None:
    """The expected value, and the reason the metric exists: the filter runs before fusion (D1)."""
    metrics = score([outcome("one", rank=1), outcome("two", rank=2)], (1,))

    assert metrics.stale_retrieval_rate == 0.0


def test_stale_retrieval_rate_is_the_share_of_scored_cases_with_a_stale_chunk() -> None:
    metrics = score(
        [
            outcome("fresh", rank=1),
            outcome("stale", rank=2, stale=("a",)),
        ],
        (1,),
    )

    assert metrics.stale_retrieval_rate == 0.5


def test_stale_retrieval_rate_counts_an_unanswerable_case_that_delivered_a_stale_chunk() -> None:
    """D1's guard covers every evaluated case: delivering a stale chunk is a failure either way."""
    metrics = score(
        [
            outcome("fresh", rank=1),
            outcome("unanswerable", delivered=("a",), stale=("a",), answerable=False),
        ],
        (1,),
    )

    assert metrics.stale_retrieval_rate == 0.5
    assert metrics.evaluated_cases == 2
    assert metrics.answerable_cases == 1


def test_a_score_set_without_answerable_cases_reports_zero_and_no_cases() -> None:
    """An all-unanswerable dataset measures nothing, and the zero has a visible denominator."""
    metrics = score([outcome("unanswerable", answerable=False)], (1, 3, 5))

    assert metrics.answerable_cases == 0
    assert metrics.evaluated_cases == 1
    assert rates(metrics) == ((1, 0.0), (3, 0.0), (5, 0.0))
    assert metrics.mrr == 0.0
    assert metrics.stale_retrieval_rate == 0.0


def test_per_tag_metrics_group_the_scored_cases_by_tag() -> None:
    """A multi-tag case counts in every tag it declares, and the aggregate stays the truth."""
    outcomes = [
        outcome("spark", rank=1, tags=("gems", "lightning")),
        outcome("pierce", rank=2, tags=("gems",)),
        outcome("monster-life", rank=None, tags=("patch-notes",)),
    ]

    by_tag = score_by_tag(outcomes, (1, 3))

    assert sorted(by_tag) == ["gems", "lightning", "patch-notes"]
    assert by_tag["gems"].answerable_cases == 2
    assert by_tag["gems"].hit_at_k == ((1, 0.5), (3, 1.0))
    assert by_tag["lightning"].answerable_cases == 1
    assert by_tag["lightning"].hit_at_k == ((1, 1.0), (3, 1.0))
    assert by_tag["patch-notes"].answerable_cases == 1
    assert by_tag["patch-notes"].hit_at_k == ((1, 0.0), (3, 0.0))
    assert score(outcomes, (1, 3)).answerable_cases == 3


def test_a_case_without_tags_only_contributes_to_the_aggregate() -> None:
    by_tag = score_by_tag([outcome("untagged", rank=1)], (1,))

    assert by_tag == {}


def test_an_unanswerable_case_creates_no_tag_of_its_own() -> None:
    """A tag whose only case is unanswerable would be a table row that measures nothing."""
    by_tag = score_by_tag([outcome("unanswerable", tags=("gems",), answerable=False)], (1,))

    assert by_tag == {}


@pytest.mark.parametrize("ks", [(), (0,), (-1,), (True,), (1, 1)])
def test_an_unusable_k_set_is_rejected(ks: tuple[int, ...]) -> None:
    """A run with no K, or with a repeated or non-positive K, cannot produce a metric table."""
    with pytest.raises(InvalidMetricsError, match="k"):
        score([outcome("one", rank=1)], ks)


def test_a_malformed_outcome_is_rejected() -> None:
    """An unanswerable case with a rank would be scored as a miss it never was."""
    with pytest.raises(InvalidMetricsError, match="first_hit_rank"):
        outcome("unanswerable", rank=1, answerable=False)
