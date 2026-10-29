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
