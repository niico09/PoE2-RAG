import math

import pytest

from poe2_rag.shared.domain import (
    DomainError,
    InvalidModifierError,
    Modifier,
    ModifierKind,
)


def test_kind_values_are_lowercase_names() -> None:
    assert {kind.value for kind in ModifierKind} == {"increased", "reduced", "more", "less", "added", "flat"}


def test_kind_can_be_built_from_its_value() -> None:
    assert ModifierKind("more") is ModifierKind.MORE


@pytest.mark.parametrize("kind", [ModifierKind.MORE, ModifierKind.LESS])
def test_more_and_less_are_multiplicative(kind: ModifierKind) -> None:
    assert Modifier("damage", 20, kind).is_multiplicative


@pytest.mark.parametrize(
    "kind", [ModifierKind.INCREASED, ModifierKind.REDUCED, ModifierKind.ADDED, ModifierKind.FLAT]
)
def test_other_kinds_are_not_multiplicative(kind: ModifierKind) -> None:
    assert not Modifier("damage", 20, kind).is_multiplicative


def test_accepts_float_values() -> None:
    assert Modifier("cast_speed", 12.5, ModifierKind.INCREASED).value == 12.5


@pytest.mark.parametrize("stat", ["", "   "])
def test_rejects_blank_stat(stat: str) -> None:
    with pytest.raises(InvalidModifierError):
        Modifier(stat, 1, ModifierKind.FLAT)


@pytest.mark.parametrize("value", ["12", None, True, math.nan, math.inf])
def test_rejects_non_finite_or_non_numeric_values(value: object) -> None:
    with pytest.raises(InvalidModifierError):
        Modifier("mana_cost", value, ModifierKind.FLAT)  # type: ignore[arg-type]


def test_accepts_arbitrarily_large_integers() -> None:
    huge = 10**400
    modifier = Modifier("damage", huge, ModifierKind.FLAT)
    assert modifier.value == huge


def test_rejects_raw_string_kind() -> None:
    with pytest.raises(InvalidModifierError):
        Modifier("damage", 20, "more")  # type: ignore[arg-type]


def test_error_is_a_domain_error() -> None:
    assert issubclass(InvalidModifierError, DomainError)


@pytest.mark.parametrize("kind", [ModifierKind.ADDED, ModifierKind.FLAT])
def test_added_and_flat_are_both_additive(kind: ModifierKind) -> None:
    """`added` and `flat` differ by pool, not by math: neither multiplies (see ModifierKind)."""
    assert not Modifier("damage", 5, kind).is_multiplicative
