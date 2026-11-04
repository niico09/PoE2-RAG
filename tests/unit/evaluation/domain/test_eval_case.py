"""`EvalCase`: one question of the fixed dataset, validated once and then read as fact (§8, D4)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from poe2_rag.evaluation.domain import EvalCase, InvalidEvalCaseError
from poe2_rag.shared.domain import PatchVersion

PATCH = PatchVersion.parse("0.1.1")


def case(**overrides: object) -> EvalCase:
    """Build a valid answerable case, letting a test replace one field at a time."""
    fields: dict[str, object] = {
        "case_id": "spark-mana-cost",
        "question": "How much mana does Spark cost?",
        "expected_chunk_ids": ("poe2db:active_gem:spark@0.1.1#properties",),
        "patch": PATCH,
        "expected_answer": "12 mana",
        "tags": ("gems",),
    }
    fields.update(overrides)
    return EvalCase(**fields)  # type: ignore[arg-type]


def test_a_valid_case_keeps_every_field() -> None:
    """The case is a record: nothing is normalized away or derived."""
    built = case()

    assert built.case_id == "spark-mana-cost"
    assert built.question == "How much mana does Spark cost?"
    assert built.expected_chunk_ids == ("poe2db:active_gem:spark@0.1.1#properties",)
    assert built.patch == PATCH
    assert built.expected_answer == "12 mana"
    assert built.tags == ("gems",)


def test_the_optional_fields_have_neutral_defaults() -> None:
    """A case with no patch, no expected answer and no tags is valid: the defaults are the policy."""
    built = EvalCase("pierce", "What does Pierce do?", ("poe2db:support_gem:pierce@0.1.1#description",))

    assert built.patch is None
    assert built.expected_answer == ""
    assert built.tags == ()
    assert built.expected_chunk_ids


def test_a_case_with_expected_ids_is_answerable() -> None:
    assert case().is_answerable is True


def test_a_case_without_expected_ids_is_unanswerable() -> None:
    """An empty list means the corpus must not answer this, and it is never scored as a miss."""
    assert case(expected_chunk_ids=()).is_answerable is False


def test_the_case_is_an_immutable_value_object() -> None:
    built = case()

    with pytest.raises(FrozenInstanceError):
        built.question = "something else"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"case_id": ""}, "case_id"),
        ({"case_id": "Spark Mana"}, "case_id"),
        ({"case_id": "spark_mana"}, "case_id"),
        ({"question": "   "}, "question"),
        ({"question": 7}, "question"),
        ({"expected_chunk_ids": ["a"]}, "expected_chunk_ids"),
        ({"expected_chunk_ids": ("a", "a")}, "expected_chunk_ids"),
        ({"expected_chunk_ids": ("a", " ")}, "expected_chunk_ids"),
        ({"expected_chunk_ids": (1,)}, "expected_chunk_ids"),
        ({"expected_answer": None}, "expected_answer"),
        ({"tags": ["gems"]}, "tags"),
        ({"tags": ("gems", "gems")}, "tags"),
        ({"tags": ("",)}, "tags"),
        ({"patch": "0.1.1"}, "patch"),
    ],
    ids=[
        "blank-id",
        "id-with-space",
        "id-with-underscore",
        "blank-question",
        "non-string-question",
        "ids-as-list",
        "duplicate-ids",
        "blank-id-value",
        "non-string-id",
        "non-string-answer",
        "tags-as-list",
        "duplicate-tags",
        "blank-tag",
        "patch-as-string",
    ],
)
def test_a_malformed_case_is_rejected(overrides: dict[str, object], message: str) -> None:
    """The dataset is data, so a malformed case fails at construction and names the broken field."""
    with pytest.raises(InvalidEvalCaseError, match=message):
        case(**overrides)


def test_a_blank_expected_answer_is_allowed() -> None:
    """`expected_answer` is free text for the generation stage: unused now, and optional."""
    assert case(expected_answer="").expected_answer == ""
