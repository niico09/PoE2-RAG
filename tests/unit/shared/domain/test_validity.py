import pytest

from poe2_rag.shared.domain import DomainError, InvalidValidityError, PatchVersion, Validity


def v(raw: str) -> PatchVersion:
    return PatchVersion.parse(raw)


class TestIsValidAt:
    def test_valid_at_lower_bound(self) -> None:
        assert Validity(v("0.1.0"), v("0.2.0")).is_valid_at(v("0.1.0"))

    def test_not_valid_at_upper_bound(self) -> None:
        assert not Validity(v("0.1.0"), v("0.2.0")).is_valid_at(v("0.2.0"))

    def test_valid_inside_range(self) -> None:
        assert Validity(v("0.1.0"), v("0.2.0")).is_valid_at(v("0.1.9"))

    def test_not_valid_before_lower_bound(self) -> None:
        assert not Validity(v("0.1.2"), v("0.2.0")).is_valid_at(v("0.1.1"))

    def test_open_range_is_valid_far_in_the_future(self) -> None:
        assert Validity(v("0.1.2")).is_valid_at(v("99.0.0"))

    def test_open_range_not_valid_before_lower_bound(self) -> None:
        assert not Validity(v("0.1.2")).is_valid_at(v("0.1.1"))

    def test_open_range_reports_still_valid(self) -> None:
        assert Validity(v("0.1.2")).valid_until is None


class TestIsValidAtRejectsNonVersion:
    @pytest.mark.parametrize("value", ["0.1.2", None, 1, (0, 1, 2)])
    def test_raises_invalid_validity_instead_of_type_error(self, value: object) -> None:
        with pytest.raises(InvalidValidityError):
            Validity(v("0.1.0")).is_valid_at(value)  # type: ignore[arg-type]

    def test_names_the_offending_value(self) -> None:
        with pytest.raises(InvalidValidityError, match="0.1.2"):
            Validity(v("0.1.0")).is_valid_at("0.1.2")  # type: ignore[arg-type]


class TestInvariants:
    def test_rejects_equal_bounds(self) -> None:
        with pytest.raises(InvalidValidityError):
            Validity(v("0.1.0"), v("0.1.0"))

    def test_rejects_inverted_bounds(self) -> None:
        with pytest.raises(InvalidValidityError):
            Validity(v("0.2.0"), v("0.1.0"))

    def test_rejects_non_version_lower_bound(self) -> None:
        with pytest.raises(InvalidValidityError):
            Validity("0.1.0")  # type: ignore[arg-type]

    def test_rejects_non_version_upper_bound(self) -> None:
        with pytest.raises(InvalidValidityError):
            Validity(v("0.1.0"), "0.2.0")  # type: ignore[arg-type]

    def test_error_is_a_domain_error(self) -> None:
        assert issubclass(InvalidValidityError, DomainError)

    def test_is_immutable(self) -> None:
        validity = Validity(v("0.1.0"))

        with pytest.raises(AttributeError):
            validity.valid_until = v("0.2.0")  # type: ignore[misc]
