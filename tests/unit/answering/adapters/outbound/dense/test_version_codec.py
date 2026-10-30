"""The ADR-0003 integer encoding of patch versions and validity ranges.

Chroma filters on metadata, so the open-ended `Validity` has to become two integers before a query can
use it. This module pins the encoding as a pure function: the mapping, its order, the component range
and the sentinel for an open upper bound.
"""

from __future__ import annotations

import pytest

from poe2_rag.answering.adapters.outbound.dense.version_codec import (
    OPEN_VALIDITY_CODE,
    VersionCodecError,
    VersionComponentError,
    decode_bound,
    decode_version,
    encode_bound,
    encode_validity,
    encode_version,
)
from poe2_rag.shared.domain import PatchVersion, Validity


class TestEncodeVersion:
    @pytest.mark.parametrize(
        ("version", "expected"),
        [
            ("0.0.0", 0),
            ("0.0.1", 1),
            ("0.1.0", 1_000),
            ("0.1.2", 1_002),
            ("0.10.0", 10_000),
            ("0.1.10", 1_010),
            ("0.11.0", 11_000),
            ("1.2.3", 1_002_003),
            ("999.999.999", 999_999_999),
        ],
    )
    def test_packs_the_components_in_base_one_thousand(
        self, version: str, expected: int
    ) -> None:
        assert encode_version(PatchVersion.parse(version)) == expected

    def test_distinguishes_zero_one_ten_from_zero_eleven_zero(self) -> None:
        """Concatenating digits would map both to `110` and break the order (ADR-0003)."""
        early = encode_version(PatchVersion.parse("0.1.10"))
        late = encode_version(PatchVersion.parse("0.11.0"))

        assert early != late
        assert early < late

    @pytest.mark.parametrize(
        ("earlier", "later"),
        [
            ("0.1.9", "0.1.10"),
            ("0.9.99", "0.10.0"),
            ("0.99.99", "0.100.0"),
            ("0.999.999", "1.0.0"),
            ("9.0.0", "10.0.0"),
            ("0.1.0", "0.1.1"),
        ],
    )
    def test_preserves_the_version_order(self, earlier: str, later: str) -> None:
        assert encode_version(PatchVersion.parse(earlier)) < encode_version(
            PatchVersion.parse(later)
        )

    @pytest.mark.parametrize(
        "version", ["0.0.0", "0.1.2", "0.10.0", "0.1.10", "999.999.999", "1.2.3"]
    )
    def test_round_trips_through_decode(self, version: str) -> None:
        parsed = PatchVersion.parse(version)

        assert decode_version(encode_version(parsed)) == parsed

    @pytest.mark.parametrize("version", [(1_000, 0, 0), (0, 1_000, 0), (0, 0, 1_000)])
    def test_rejects_a_component_above_the_limit(self, version: tuple[int, int, int]) -> None:
        with pytest.raises(VersionComponentError, match="999"):
            encode_version(PatchVersion(*version))

    @pytest.mark.parametrize("value", ["0.1.0", None, 1_002])
    def test_rejects_a_non_version(self, value: object) -> None:
        with pytest.raises(VersionCodecError):
            encode_version(value)  # type: ignore[arg-type]


class TestDecodeVersion:
    @pytest.mark.parametrize("version", ["0.0.0", "0.1.0", "0.1.2", "0.11.0", "999.999.999"])
    def test_round_trips_through_encode(self, version: str) -> None:
        parsed = PatchVersion.parse(version)

        assert encode_version(decode_version(encode_version(parsed))) == encode_version(parsed)

    @pytest.mark.parametrize("code", [-1, 1_000_000_000, OPEN_VALIDITY_CODE])
    def test_rejects_a_code_outside_the_representable_range(self, code: int) -> None:
        """Every stored code decodes to a version whose components are `0..999`."""
        with pytest.raises(VersionComponentError):
            decode_version(code)

    @pytest.mark.parametrize("code", ["1000", 1000.0, None, True])
    def test_rejects_a_non_integer_code(self, code: object) -> None:
        with pytest.raises(VersionCodecError):
            decode_version(code)  # type: ignore[arg-type]


class TestBounds:
    def test_open_validity_uses_the_sentinel(self) -> None:
        assert encode_bound(None) == OPEN_VALIDITY_CODE
        assert OPEN_VALIDITY_CODE == 2**31 - 1

    def test_a_closed_bound_round_trips(self) -> None:
        version = PatchVersion.parse("0.2.0")

        assert decode_bound(encode_bound(version)) == version

    def test_the_sentinel_decodes_to_an_open_bound(self) -> None:
        assert decode_bound(OPEN_VALIDITY_CODE) is None

    def test_encodes_a_closed_validity_as_two_codes(self) -> None:
        validity = Validity(PatchVersion.parse("0.1.0"), PatchVersion.parse("0.2.0"))

        assert encode_validity(validity) == (1_000, 2_000)

    def test_encodes_an_open_validity_with_the_sentinel(self) -> None:
        validity = Validity(PatchVersion.parse("0.1.0"))

        assert encode_validity(validity) == (1_000, OPEN_VALIDITY_CODE)

    def test_the_encoded_range_keeps_the_domain_boundaries(self) -> None:
        """`valid_from <= v` and `valid_until > v` are the domain's half-open interval (ADR-0003)."""
        validity = Validity(PatchVersion.parse("0.1.0"), PatchVersion.parse("0.2.0"))
        valid_from, valid_until = encode_validity(validity)

        def matches(version: str) -> bool:
            code = encode_version(PatchVersion.parse(version))
            return valid_from <= code and valid_until > code

        assert matches("0.1.0")
        assert matches("0.1.5")
        assert not matches("0.2.0")
        assert not matches("0.0.9")


class TestErrors:
    def test_codec_errors_are_value_errors(self) -> None:
        assert issubclass(VersionCodecError, ValueError)
        assert issubclass(VersionComponentError, VersionCodecError)
