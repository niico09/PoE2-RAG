"""Retrieval budgets for one `AnswerQuestion` run."""

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from poe2_rag.answering.application import (
    AnswerQuestionError,
    InvalidRetrievalSettingsError,
    RetrievalSettings,
)

FIELDS = ("lexical_top_n", "dense_top_n", "rerank_top_m", "final_top_k", "rrf_k")


def budgets(**overrides: Any) -> dict[str, Any]:
    """Return one valid budget per field, with `overrides` applied on top.

    `Any` is deliberate: several cases pass values that are not ints, to check the rejection.
    """
    values: dict[str, Any] = {
        "lexical_top_n": 30,
        "dense_top_n": 20,
        "rerank_top_m": 8,
        "final_top_k": 4,
        "rrf_k": 60,
    }
    values.update(overrides)
    return values


def test_keeps_the_budgets() -> None:
    settings = RetrievalSettings(**budgets(lexical_top_n=50, dense_top_n=40, rerank_top_m=10, final_top_k=3))

    assert settings.lexical_top_n == 50
    assert settings.dense_top_n == 40
    assert settings.rerank_top_m == 10
    assert settings.final_top_k == 3


def test_rrf_k_defaults_to_the_domain_default() -> None:
    assert RetrievalSettings(lexical_top_n=1, dense_top_n=1, rerank_top_m=1, final_top_k=1).rrf_k == 60


def test_keeps_an_explicit_rrf_k() -> None:
    assert RetrievalSettings(**budgets(rrf_k=10)).rrf_k == 10


def test_final_top_k_may_equal_rerank_top_m() -> None:
    assert RetrievalSettings(**budgets(rerank_top_m=4, final_top_k=4)).final_top_k == 4


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [0, -1])
def test_rejects_a_non_positive_budget(field: str, value: int) -> None:
    with pytest.raises(InvalidRetrievalSettingsError, match=field):
        RetrievalSettings(**budgets(**{field: value}))


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [True, 1.5, "3", None])
def test_rejects_a_non_int_budget(field: str, value: object) -> None:
    with pytest.raises(InvalidRetrievalSettingsError, match=field):
        RetrievalSettings(**budgets(**{field: value}))


def test_rejects_final_top_k_above_rerank_top_m() -> None:
    with pytest.raises(InvalidRetrievalSettingsError, match="final_top_k"):
        RetrievalSettings(**budgets(rerank_top_m=2, final_top_k=3))


def test_is_frozen() -> None:
    settings = RetrievalSettings(**budgets())

    with pytest.raises(FrozenInstanceError):
        settings.final_top_k = 2  # type: ignore[misc]


def test_settings_error_is_an_application_error() -> None:
    assert issubclass(InvalidRetrievalSettingsError, AnswerQuestionError)
