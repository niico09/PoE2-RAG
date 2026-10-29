"""Reciprocal Rank Fusion: merge ranked chunk ids into one ordered list."""

from __future__ import annotations

from collections.abc import Sequence

from poe2_rag.answering.domain.errors import InvalidRankingError
from poe2_rag.shared.domain._text import is_blank

DEFAULT_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], k: int = DEFAULT_K
) -> list[tuple[str, float]]:
    """Fuse rankings of chunk ids into `(chunk_id, score)` pairs, best first.

    `score = sum(1 / (k + rank))` over the rankings that contain the id, with `rank` starting at 1.
    Equal scores break by ascending `chunk_id`, so the result is a total order and the same input
    always produces the same list.

    This function does not filter by validity: rankings must arrive already filtered for the target
    patch (§5.1 step 2). Filtering here would fuse obsolete chunks and leave holes in the top-K.
    """
    _require_k(k)
    _require_rankings(rankings)
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1 / (k + rank)
    return sorted(fused.items(), key=lambda item: (-item[1], item[0]))


def _require_k(k: object) -> None:
    if isinstance(k, bool) or not isinstance(k, int):
        raise InvalidRankingError(f"k must be an int, got {k!r}")
    if k <= 0:
        raise InvalidRankingError(f"k must be positive, got {k}")


def _require_rankings(rankings: object) -> None:
    if isinstance(rankings, (str, bytes)) or not isinstance(rankings, Sequence):
        raise InvalidRankingError(f"rankings must be a sequence of sequences, got {rankings!r}")
    for ranking in rankings:
        _require_ranking(ranking)


def _require_ranking(ranking: object) -> None:
    if isinstance(ranking, (str, bytes)) or not isinstance(ranking, Sequence):
        raise InvalidRankingError(f"each ranking must be a sequence of chunk ids, got {ranking!r}")
    seen: set[str] = set()
    for chunk_id in ranking:
        if is_blank(chunk_id):
            raise InvalidRankingError(f"chunk ids must be non-blank strings, got {chunk_id!r}")
        if chunk_id in seen:
            raise InvalidRankingError(f"duplicate chunk id {chunk_id!r} in one ranking")
        seen.add(chunk_id)
