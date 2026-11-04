import pytest

from poe2_rag.shared.domain import DomainError, InvalidPatchVersionError, PatchVersion


class TestParse:
    def test_parses_three_components(self) -> None:
        version = PatchVersion.parse("0.1.2")

        assert (version.major, version.minor, version.patch) == (0, 1, 2)

    def test_str_round_trips(self) -> None:
        assert str(PatchVersion.parse("0.10.3")) == "0.10.3"

    @pytest.mark.parametrize(
        "raw",
        ["", "1.2", "1.2.3.4", "a.b.c", "-1.0.0", "1.-2.0", "+1.0.0", " 1.0.0", "1.0.0 ", "1..0", "1.0.x", "01.0.0"],
    )
    def test_rejects_invalid_formats(self, raw: str) -> None:
        with pytest.raises(InvalidPatchVersionError):
            PatchVersion.parse(raw)


    @pytest.mark.parametrize("raw", [None, 12, b"0.1.2"])
    def test_rejects_non_string_input(self, raw: object) -> None:
        with pytest.raises(InvalidPatchVersionError):
            PatchVersion.parse(raw)  # type: ignore[arg-type]


class TestHotfixSuffix:
    def test_parses_a_hotfix_suffix(self) -> None:
        version = PatchVersion.parse("0.5.5e")

        assert (version.major, version.minor, version.patch, version.hotfix) == (0, 5, 5, "e")

    def test_str_round_trips_the_suffix(self) -> None:
        assert str(PatchVersion.parse("0.5.5e")) == "0.5.5e"

    def test_a_version_without_a_suffix_has_no_hotfix(self) -> None:
        version = PatchVersion.parse("0.5.5")

        assert version.hotfix == ""
        assert str(version) == "0.5.5"

    def test_every_hotfix_letter_round_trips(self) -> None:
        for letter in "abcdefghijklmnopqrstuvwxyz":
            raw = f"0.5.5{letter}"

            assert str(PatchVersion.parse(raw)) == raw

    def test_the_suffix_is_part_of_equality_and_hash(self) -> None:
        hotfix = PatchVersion.parse("0.5.5e")

        assert hotfix != PatchVersion.parse("0.5.5")
        assert hash(hotfix) != hash(PatchVersion.parse("0.5.5"))
        assert hotfix == PatchVersion(0, 5, 5, "e")
        assert hash(hotfix) == hash(PatchVersion(0, 5, 5, "e"))

    def test_a_hotfix_sorts_between_its_patch_and_the_next_one(self) -> None:
        versions = [PatchVersion.parse(raw) for raw in ("0.5.6", "0.5.5b", "0.5.5", "0.5.5z", "0.5.5a")]

        assert [str(version) for version in sorted(versions)] == [
            "0.5.5",
            "0.5.5a",
            "0.5.5b",
            "0.5.5z",
            "0.5.6",
        ]

    def test_the_alphabetical_order_of_the_suffixes_is_the_version_order(self) -> None:
        """A single lowercase letter makes lexicographic order the ordinal order (S1)."""
        letters = "abcdefghijklmnopqrstuvwxyz"
        generated = [
            PatchVersion.parse(f"0.{minor}.{patch}{letter}")
            for minor in (0, 5, 12)
            for patch in (0, 5, 999)
            for letter in letters
        ]
        assert sorted(generated) == generated
        assert sorted(generated, reverse=True) == list(reversed(generated))

    @pytest.mark.parametrize(
        "raw",
        [
            "0.5.5E",
            "0.5.5A",
            "0.5.5ab",
            "0.5.5aa",
            "0.5.5e1",
            "0.5.5e12",
            "0.5.5 e",
            " 0.5.5e",
            "0.5.5e ",
            "0.5.5-e",
            "0.5.5.e",
            "0.5.5_",
            "05.5.5e",
            "0.5.05e",
        ],
    )
    def test_rejects_a_malformed_suffix(self, raw: str) -> None:
        with pytest.raises(InvalidPatchVersionError):
            PatchVersion.parse(raw)

    @pytest.mark.parametrize("hotfix", ["E", "ab", "1", "-", "e "])
    def test_construction_rejects_a_malformed_suffix(self, hotfix: str) -> None:
        with pytest.raises(InvalidPatchVersionError):
            PatchVersion(0, 5, 5, hotfix)

    @pytest.mark.parametrize("hotfix", [5, None, b"e"])
    def test_construction_rejects_a_non_string_suffix(self, hotfix: object) -> None:
        with pytest.raises(InvalidPatchVersionError):
            PatchVersion(0, 5, 5, hotfix)  # type: ignore[arg-type]

    def test_the_existing_three_component_construction_still_works(self) -> None:
        assert PatchVersion(0, 1, 2) == PatchVersion.parse("0.1.2")
        assert PatchVersion(0, 1, 2).hotfix == ""


class TestConstruction:
    def test_rejects_negative_components(self) -> None:
        with pytest.raises(InvalidPatchVersionError):
            PatchVersion(0, -1, 0)

    def test_rejects_non_integer_components(self) -> None:
        with pytest.raises(InvalidPatchVersionError):
            PatchVersion(0, 1.5, 0)  # type: ignore[arg-type]

    def test_rejects_bool_components(self) -> None:
        with pytest.raises(InvalidPatchVersionError):
            PatchVersion(True, 0, 0)  # type: ignore[arg-type]

    def test_error_is_a_domain_error_and_value_error(self) -> None:
        assert issubclass(InvalidPatchVersionError, DomainError)
        assert issubclass(DomainError, ValueError)

    def test_is_immutable(self) -> None:
        version = PatchVersion(0, 1, 2)

        with pytest.raises(AttributeError):
            version.major = 1  # type: ignore[misc]


class TestOrdering:
    def test_compares_numerically_not_lexically(self) -> None:
        assert PatchVersion.parse("0.10.0") > PatchVersion.parse("0.9.9")

    def test_patch_component_breaks_ties(self) -> None:
        assert PatchVersion.parse("0.1.0") < PatchVersion.parse("0.1.2")

    def test_equal_versions_are_equal_and_hash_alike(self) -> None:
        assert PatchVersion.parse("0.1.2") == PatchVersion(0, 1, 2)
        assert hash(PatchVersion.parse("0.1.2")) == hash(PatchVersion(0, 1, 2))

    def test_sorts_a_list(self) -> None:
        versions = [PatchVersion.parse(v) for v in ("0.2.0", "0.1.10", "0.1.2")]

        assert [str(v) for v in sorted(versions)] == ["0.1.2", "0.1.10", "0.2.0"]
