"""The tokenizer the lexical index indexes and queries with (driver D2)."""

from __future__ import annotations

import pytest

from poe2_rag.answering.adapters.outbound.lexical.tokenizer import tokenize


class TestNormalization:
    def test_lowercases_every_token(self) -> None:
        assert tokenize("Spark Fires LIGHTNING") == ("spark", "fires", "lightning")

    def test_splits_on_whitespace_and_punctuation(self) -> None:
        assert tokenize("Spark, fires;  lightning!\nDamage.") == (
            "spark",
            "fires",
            "lightning",
            "damage",
        )

    def test_drops_punctuation_between_words(self) -> None:
        assert tokenize("(20% damage)") == ("20%", "damage")


class TestNumbers:
    def test_keeps_a_percent_value_as_one_token(self) -> None:
        assert tokenize("+10% increased damage") == ("+10%", "increased", "damage")

    def test_keeps_a_signed_percent_value_as_one_token(self) -> None:
        assert tokenize("-20% less damage") == ("-20%", "less", "damage")

    def test_keeps_a_decimal_as_one_token(self) -> None:
        assert tokenize("1.5 metres per second") == ("1.5", "metres", "per", "second")

    def test_keeps_an_integer_as_one_token(self) -> None:
        assert tokenize("12 mana") == ("12", "mana")


class TestGameTerms:
    def test_keeps_a_hyphenated_term_with_its_parts(self) -> None:
        assert tokenize("area-of-effect") == ("area-of-effect", "area", "of", "effect")

    def test_keeps_an_underscored_term_with_its_parts(self) -> None:
        assert tokenize("cast_on_crit") == ("cast_on_crit", "cast", "on", "crit")

    def test_keeps_a_hyphenated_term_inside_a_sentence(self) -> None:
        assert tokenize("Gain area-of-effect radius.") == (
            "gain",
            "area-of-effect",
            "area",
            "of",
            "effect",
            "radius",
        )


class TestNoSemanticReduction:
    def test_does_not_stem(self) -> None:
        """`more` and `increased` are different mechanics, so their word forms stay apart (D2)."""
        assert tokenize("increased increases increasing") == (
            "increased",
            "increases",
            "increasing",
        )

    def test_does_not_drop_domain_words(self) -> None:
        """No stopword list in v1: these words decide what a modifier does (D2)."""
        assert tokenize("more less increased reduced added") == (
            "more",
            "less",
            "increased",
            "reduced",
            "added",
        )

    def test_repeated_words_stay_repeated_for_term_frequency(self) -> None:
        assert tokenize("spark spark damage") == ("spark", "spark", "damage")


class TestLetters:
    def test_keeps_accented_letters_inside_words(self) -> None:
        """An accent is a letter, not punctuation: splitting there leaves broken prefixes (D2)."""
        assert tokenize("naïve café Über") == ("naïve", "café", "über")

    def test_does_not_truncate_a_word_at_its_accent(self) -> None:
        assert "na" not in tokenize("naïve")

    def test_keeps_an_accented_compound_term_with_its_parts(self) -> None:
        assert tokenize("Einhänder-Griff") == ("einhänder-griff", "einhänder", "griff")


class TestEdges:
    def test_returns_nothing_for_an_empty_string(self) -> None:
        assert tokenize("") == ()

    def test_returns_nothing_for_punctuation_only(self) -> None:
        assert tokenize("-- ... !!") == ()

    def test_is_deterministic(self) -> None:
        """Pin the exact output: a persisted index depends on one text tokenizing the same way."""
        assert tokenize("Spark: +10% area-of-effect damage, cast_on_crit 1.5s") == (
            "spark",
            "+10%",
            "area-of-effect",
            "area",
            "of",
            "effect",
            "damage",
            "cast_on_crit",
            "cast",
            "on",
            "crit",
            "1.5",
            "s",
        )

    def test_returns_an_immutable_sequence(self) -> None:
        assert isinstance(tokenize("spark"), tuple)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("!!!", ()),
            ("   ", ()),
            ("+10%", ("+10%",)),
        ],
    )
    def test_pins_the_edge_outputs(self, text: str, expected: tuple[str, ...]) -> None:
        assert tokenize(text) == expected
