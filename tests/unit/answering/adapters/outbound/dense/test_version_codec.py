"""The ADR-0003 integer encoding of patch versions and validity ranges.

Chroma filters on metadata, so the open-ended `Validity` has to become two integers before a query can
use it. This module pins the encoding as a pure function: the mapping, its order, the component range,
the hotfix ordinal and the sentinel for an open upper bound.
"""

from __future__ import annotations

import pytest

from poe2_rag.answering.adapters.outbound.dense.version_codec import (
    CODEC_VERSION,
    COMPONENT_LIMIT,
    MAX_VERSION_CODE,
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
            ("0.0.1", 27),
            ("0.1.0", 27_000),
            ("0.1.2", 27_054),
            ("0.10.0", 270_000),
            ("0.1.10", 27_270),
            ("0.11.0", 297_000),
            ("1.2.3", 27_054_081),
            ("999.999.999", 26_999_999_973),
        ],
    )
    def test_packs_the_components_in_base_one_thousand(
        self, version: str, expected: int
    ) -> None:
        assert encode_version(PatchVersion.parse(version)) == expected

    @pytest.mark.parametrize(
        ("version", "expected"),
        [
            ("0.5.5", 135_135),
            ("0.5.5a", 135_136),
            ("0.5.5b", 135_137),
            ("0.5.5e", 135_140),
            ("0.5.5z", 135_161),
            ("0.5.6", 135_162),
        ],
    )
    def test_packs_the_hotfix_as_an_ordinal_after_its_patch(
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
            ("0.5.5", "0.5.5a"),
            ("0.5.5a", "0.5.5b"),
            ("0.5.5y", "0.5.5z"),
            ("0.5.5z", "0.5.6"),
        ],
    )
    def test_preserves_the_version_order(self, earlier: str, later: str) -> None:
        assert encode_version(PatchVersion.parse(earlier)) < encode_version(
            PatchVersion.parse(later)
        )

    def test_sorted_versions_follow_the_order_of_their_encodings(self) -> None:
        """Property-style: the codec is monotone over a generated grid, hotfixes included."""
        generated = [
            PatchVersion.parse(f"0.{minor}.{patch}{hotfix}")
            for minor in (0, 1, 5, 10, 999)
            for patch in (0, 1, 5, 999)
            for hotfix in ("", *"abcdefghijklmnopqrstuvwxyz")
        ]
        codes = [encode_version(version) for version in generated]

        assert codes == sorted(codes)
        assert len(set(codes)) == len(codes)

    @pytest.mark.parametrize(
        "version", ["0.0.0", "0.1.2", "0.10.0", "0.1.10", "999.999.999", "1.2.3", "0.5.5e"]
    )
    def test_round_trips_through_decode(self, version: str) -> None:
        parsed = PatchVersion.parse(version)

        assert decode_version(encode_version(parsed)) == parsed

    @pytest.mark.parametrize("hotfix", ["", *"az"])
    def test_round_trips_a_max_range_version(self, hotfix: str) -> None:
        parsed = PatchVersion.parse(f"999.999.999{hotfix}")

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

    @pytest.mark.parametrize(
        "code",
        [
            -1,
            OPEN_VALIDITY_CODE,
            OPEN_VALIDITY_CODE + 1,
            MAX_VERSION_CODE + 2,
            2**63,
        ],
    )
    def test_rejects_a_code_no_version_can_produce(self, code: int) -> None:
        """Every stored code decodes to a version whose components are `0..999` with an ordinal `0..26`."""
        with pytest.raises(VersionComponentError):
            decode_version(code)

    @pytest.mark.parametrize("code", ["1000", 1000.0, None, True])
    def test_rejects_a_non_integer_code(self, code: object) -> None:
        with pytest.raises(VersionCodecError):
            decode_version(code)  # type: ignore[arg-type]


class TestRange:
    def test_the_open_sentinel_is_strictly_greater_than_every_encodable_version(self) -> None:
        biggest = encode_version(PatchVersion.parse("999.999.999z"))

        assert biggest < OPEN_VALIDITY_CODE
        assert OPEN_VALIDITY_CODE == biggest + 1 == 27_000_000_000

    def test_the_encoding_needs_int64_metadata(self) -> None:
        """The verified range of Chroma's metadata filters is what makes this layout possible (ADR-0003)."""
        biggest = encode_version(PatchVersion.parse("999.999.999z"))

        assert biggest > 2**31 - 1
        assert biggest < 2**63 - 1
        assert OPEN_VALIDITY_CODE < 2**63 - 1

    def test_the_component_limit_is_three_digits(self) -> None:
        assert COMPONENT_LIMIT == 999
        assert encode_version(PatchVersion.parse("999.999.999z")) == 999_999_999 * 27 + 26


class TestBounds:
    def test_open_validity_uses_the_sentinel(self) -> None:
        assert encode_bound(None) == OPEN_VALIDITY_CODE

    def test_a_closed_bound_round_trips(self) -> None:
        version = PatchVersion.parse("0.2.0")

        assert decode_bound(encode_bound(version)) == version

    def test_a_hotfix_bound_round_trips(self) -> None:
        version = PatchVersion.parse("0.5.5e")

        assert decode_bound(encode_bound(version)) == version

    def test_the_sentinel_decodes_to_an_open_bound(self) -> None:
        assert decode_bound(OPEN_VALIDITY_CODE) is None

    def test_encodes_a_closed_validity_as_two_codes(self) -> None:
        validity = Validity(PatchVersion.parse("0.1.0"), PatchVersion.parse("0.2.0"))

        assert encode_validity(validity) == (27_000, 54_000)

    def test_encodes_an_open_validity_with_the_sentinel(self) -> None:
        validity = Validity(PatchVersion.parse("0.1.0"))

        assert encode_validity(validity) == (27_000, OPEN_VALIDITY_CODE)

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

    @pytest.mark.parametrize("value", ["0.1.0", None, (1_000, 2_000)])
    def test_rejects_a_non_validity(self, value: object) -> None:
        with pytest.raises(VersionCodecError):
            encode_validity(value)  # type: ignore[arg-type]

    @pytest.mark.parametrize("code", ["1000", None, 1_000.0, True])
    def test_rejects_a_non_integer_bound(self, code: object) -> None:
        with pytest.raises(VersionCodecError):
            decode_bound(code)  # type: ignore[arg-type]


class TestCodecVersion:
    def test_the_hotfix_layout_is_codec_version_two(self) -> None:
        """A stored index records this so an older, different encoding is never read silently."""
        assert CODEC_VERSION == 2


class TestErrors:
    def test_codec_errors_are_value_errors(self) -> None:
        assert issubclass(VersionCodecError, ValueError)
        assert issubclass(VersionComponentError, VersionCodecError)
