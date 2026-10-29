import pytest

from poe2_rag.answering.domain import InvalidRankingError, reciprocal_rank_fusion
from poe2_rag.shared.domain import DomainError

K = 60


def ids(fused: list[tuple[str, float]]) -> list[str]:
    return [chunk_id for chunk_id, _ in fused]


def scores(fused: list[tuple[str, float]]) -> list[float]:
    return [score for _, score in fused]


def test_error_is_a_domain_error() -> None:
    assert issubclass(InvalidRankingError, DomainError)


class TestHandComputedExample:
    def test_scores_are_one_over_k_plus_rank_summed_across_rankings(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "a"]])

        assert ids(fused) == ["a", "c", "b"]
        assert scores(fused) == pytest.approx([1 / 61 + 1 / 62, 1 / 63 + 1 / 61, 1 / 62])

    def test_k_defaults_to_sixty(self) -> None:
        assert reciprocal_rank_fusion([["a"]]) == [("a", 1 / 61)]

    def test_k_shifts_every_rank(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b"]], k=1)

        assert scores(fused) == pytest.approx([1 / 2, 1 / 3])

    def test_rank_starts_at_one_not_zero(self) -> None:
        assert reciprocal_rank_fusion([["a"]], k=9) == [("a", 1 / 10)]


class TestSingleRankingPresence:
    def test_a_chunk_in_only_one_ranking_keeps_its_single_contribution(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b"], ["c"]])

        assert ids(fused) == ["a", "c", "b"]
        assert scores(fused) == pytest.approx([1 / 61, 1 / 61, 1 / 62])

    def test_an_empty_ranking_contributes_nothing(self) -> None:
        fused = reciprocal_rank_fusion([["a"], []])

        assert fused == [("a", 1 / 61)]


class TestDeterministicTies:
    def test_equal_scores_are_ordered_by_chunk_id(self) -> None:
        assert ids(reciprocal_rank_fusion([["b"], ["a"]])) == ["a", "b"]

    def test_the_order_of_the_rankings_does_not_change_the_output(self) -> None:
        assert reciprocal_rank_fusion([["b"], ["a"]]) == reciprocal_rank_fusion([["a"], ["b"]])

    def test_a_chunk_ranked_first_in_every_ranking_still_ties_by_chunk_id(self) -> None:
        fused = reciprocal_rank_fusion([["z", "y"], ["y", "z"]])

        assert ids(fused) == ["y", "z"]
        assert scores(fused) == pytest.approx([1 / 61 + 1 / 62, 1 / 62 + 1 / 61])

    def test_the_same_chunk_in_several_rankings_accumulates_its_score(self) -> None:
        assert reciprocal_rank_fusion([["a"], ["a"]]) == [("a", 2 / 61)]

    def test_output_is_a_total_order_of_every_input_id(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b"], ["c", "a"]])

        assert sorted(ids(fused)) == ["a", "b", "c"]
        assert len(fused) == len(set(ids(fused)))


class TestEmptyInput:
    def test_no_rankings_fuse_to_an_empty_list(self) -> None:
        assert reciprocal_rank_fusion([]) == []

    def test_only_empty_rankings_fuse_to_an_empty_list(self) -> None:
        assert reciprocal_rank_fusion([[], []]) == []

    def test_returns_a_list(self) -> None:
        assert isinstance(reciprocal_rank_fusion([["a"]]), list)


class TestInvalidInput:
    @pytest.mark.parametrize("k", [0, -1, -60])
    def test_rejects_k_that_is_not_positive(self, k: int) -> None:
        with pytest.raises(InvalidRankingError, match="k must be positive"):
            reciprocal_rank_fusion([["a"]], k=k)

    @pytest.mark.parametrize("k", [True, 1.5, "60", None])
    def test_rejects_k_that_is_not_an_int(self, k: object) -> None:
        with pytest.raises(InvalidRankingError, match="k must be an int"):
            reciprocal_rank_fusion([["a"]], k=k)  # type: ignore[arg-type]

    def test_rejects_a_duplicate_id_within_one_ranking(self) -> None:
        with pytest.raises(InvalidRankingError, match="duplicate chunk id 'a'"):
            reciprocal_rank_fusion([["a", "b", "a"]])

    def test_rejects_a_duplicate_id_in_the_second_ranking(self) -> None:
        with pytest.raises(InvalidRankingError, match="duplicate chunk id 'b'"):
            reciprocal_rank_fusion([["a"], ["b", "b"]])

    def test_accepts_the_same_id_in_different_rankings(self) -> None:
        assert ids(reciprocal_rank_fusion([["a"], ["a"]])) == ["a"]

    @pytest.mark.parametrize("rankings", [None, 42, {"a": 1}])
    def test_rejects_something_that_is_not_a_sequence_of_rankings(self, rankings: object) -> None:
        with pytest.raises(InvalidRankingError, match="rankings"):
            reciprocal_rank_fusion(rankings)  # type: ignore[arg-type]

    def test_rejects_a_bare_string_as_the_rankings(self) -> None:
        with pytest.raises(InvalidRankingError, match="rankings"):
            reciprocal_rank_fusion("ab")  # type: ignore[arg-type]

    @pytest.mark.parametrize("ranking", ["ab", None, 42])
    def test_rejects_a_ranking_that_is_not_a_sequence_of_ids(self, ranking: object) -> None:
        with pytest.raises(InvalidRankingError, match="ranking"):
            reciprocal_rank_fusion([ranking])  # type: ignore[arg-type]

    @pytest.mark.parametrize("chunk_id", ["", "   ", None, 3])
    def test_rejects_a_chunk_id_that_is_not_a_non_blank_string(self, chunk_id: object) -> None:
        with pytest.raises(InvalidRankingError, match="chunk id"):
            reciprocal_rank_fusion([[chunk_id]])  # type: ignore[arg-type]
