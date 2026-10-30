"""ADR-0003 integer encoding of patch versions and validity ranges.

Chroma filters on record metadata, so the domain's `PatchVersion` and `Validity` have to become
integers before they can be compared inside a query: `major * 1_000_000 + minor * 1_000 + patch`,
with each component in `0..999`, and `2**31 - 1` as the open upper bound (a metadata filter does not
handle a null well).

Concatenating the digits instead of scaling them would collide as soon as a component reaches ten
(`0.1.10` and `0.11.0` would both read `110`) and the order would stop matching `PatchVersion`'s. The
scaled form keeps the order: comparing two codes compares major, then minor, then patch.

The encoding is an adapter detail. The read side decodes a stored bound back into the domain type, and
a stored code outside the representable range is refused instead of silently wrapping into another
patch.
"""

from __future__ import annotations

from poe2_rag.shared.domain import PatchVersion, Validity

COMPONENT_LIMIT = 999
"""The largest encodable value of one component; three decimal digits (ADR-0003)."""

OPEN_VALIDITY_CODE = 2**31 - 1
"""The code stored for a `valid_until` that is open (ADR-0003)."""

_MINOR_SCALE = 1_000
_MAJOR_SCALE = _MINOR_SCALE * _MINOR_SCALE


class VersionCodecError(ValueError):
    """Base class for values the codec refuses to encode or decode."""


class VersionComponentError(VersionCodecError):
    """Raised when a version component or a stored code falls outside `0..999`."""


def encode_version(version: PatchVersion) -> int:
    """Return the sortable integer of `version`, rejecting a component above `999`."""
    if not isinstance(version, PatchVersion):
        raise VersionCodecError(f"expected a PatchVersion, got {version!r}")
    _require_encodable("major", version.major)
    _require_encodable("minor", version.minor)
    _require_encodable("patch", version.patch)
    return version.major * _MAJOR_SCALE + version.minor * _MINOR_SCALE + version.patch


def decode_version(code: int) -> PatchVersion:
    """Return the version `code` encodes, rejecting a code that no version can produce."""
    if type(code) is not int:
        raise VersionCodecError(f"expected an int code, got {code!r}")
    if code < 0:
        raise VersionComponentError(f"a version code must be non-negative, got {code!r}")
    major, remainder = divmod(code, _MAJOR_SCALE)
    minor, patch = divmod(remainder, _MINOR_SCALE)
    _require_encodable("major", major)
    _require_encodable("minor", minor)
    _require_encodable("patch", patch)
    return PatchVersion(major, minor, patch)


def encode_bound(version: PatchVersion | None) -> int:
    """Return the code of an upper bound, using the open-validity sentinel for `None`."""
    return OPEN_VALIDITY_CODE if version is None else encode_version(version)


def decode_bound(code: int) -> PatchVersion | None:
    """Return the version a stored bound encodes, or `None` when the bound is open."""
    return None if code == OPEN_VALIDITY_CODE else decode_version(code)


def encode_validity(validity: Validity) -> tuple[int, int]:
    """Return the `(valid_from, valid_until)` codes a validity filter compares against."""
    if not isinstance(validity, Validity):
        raise VersionCodecError(f"expected a Validity, got {validity!r}")
    return encode_version(validity.valid_from), encode_bound(validity.valid_until)


def _require_encodable(component: str, value: int) -> None:
    """Reject a component the three-digit encoding cannot represent, instead of truncating it."""
    if value > COMPONENT_LIMIT:
        raise VersionComponentError(
            f"{component} must be at most {COMPONENT_LIMIT} to be encoded, got {value}"
        )
