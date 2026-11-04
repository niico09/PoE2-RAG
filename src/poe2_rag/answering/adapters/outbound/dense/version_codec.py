"""ADR-0003 integer encoding of patch versions and validity ranges.

Chroma filters on record metadata, so the domain's `PatchVersion` and `Validity` have to become
integers before they can be compared inside a query: `((major * 1_000 + minor) * 1_000 + patch) * 27 +
hotfix`, with each numeric component in `0..999` and the hotfix as an ordinal (`none = 0`, `a = 1` ...
`z = 26`). The base-27 factor is what keeps a hotfix between its patch and the next one:
`0.5.5 < 0.5.5a < ... < 0.5.5z < 0.5.6`.

Concatenating the digits instead of scaling them would collide as soon as a component reaches ten
(`0.1.10` and `0.11.0` would both read `110`) and the order would stop matching `PatchVersion`'s. The
scaled form keeps the order: comparing two codes compares major, then minor, then patch, then hotfix.

Range: the largest encodable version is `999.999.999z` → `26_999_999_999`, which does not fit int32.
chromadb 1.5.9 was measured for this: metadata values above `2**31 - 1` round-trip exactly and `$gt` /
`$lte` / `$lt` filters order them correctly across the int32 boundary and up to `2**63 - 2`, so the
layout uses the int64 range. The open upper bound (`valid_until` empty) is stored as one past the
largest encodable version (`27_000_000_000`), which keeps the sentinel strictly greater than every
version and still far below `2**63 - 1`; a metadata filter does not handle a null well.

`CODEC_VERSION` names this layout. It is written into the collection metadata so an index built with
the older, suffix-blind encoding is refused instead of read with subtly wrong validity bounds.

The encoding is an adapter detail. The read side decodes a stored bound back into the domain type, and
a stored code outside the representable range is refused instead of silently wrapping into another
patch.
"""

from __future__ import annotations

from poe2_rag.shared.domain import PatchVersion, Validity

CODEC_VERSION = 2
"""Version of the integer layout; a stored index records it and a mismatch is refused (ADR-0003)."""

COMPONENT_LIMIT = 999
"""The largest encodable value of one numeric component; three decimal digits (ADR-0003)."""

HOTFIX_LIMIT = 26
"""The largest hotfix ordinal: `z` (ADR-0003)."""

_MINOR_SCALE = 1_000
_MAJOR_SCALE = _MINOR_SCALE * _MINOR_SCALE
_HOTFIX_SCALE = HOTFIX_LIMIT + 1

_MAX_COMPONENT_CODE = COMPONENT_LIMIT * _MAJOR_SCALE + COMPONENT_LIMIT * _MINOR_SCALE + COMPONENT_LIMIT

MAX_VERSION_CODE = _MAX_COMPONENT_CODE * _HOTFIX_SCALE + HOTFIX_LIMIT
"""The code of `999.999.999z`, the largest encodable version."""

OPEN_VALIDITY_CODE = MAX_VERSION_CODE + 1
"""The code stored for a `valid_until` that is open (ADR-0003)."""


class VersionCodecError(ValueError):
    """Base class for values the codec refuses to encode or decode."""


class VersionComponentError(VersionCodecError):
    """Raised when a version component or a stored code falls outside the encodable range."""


def encode_version(version: PatchVersion) -> int:
    """Return the sortable integer of `version`, rejecting a component above `999`."""
    if not isinstance(version, PatchVersion):
        raise VersionCodecError(f"expected a PatchVersion, got {version!r}")
    _require_component("major", version.major)
    _require_component("minor", version.minor)
    _require_component("patch", version.patch)
    return (
        (
            version.major * _MAJOR_SCALE
            + version.minor * _MINOR_SCALE
            + version.patch
        )
        * _HOTFIX_SCALE
        + _hotfix_ordinal(version)
    )


def decode_version(code: int) -> PatchVersion:
    """Return the version `code` encodes, rejecting a code that no version can produce."""
    if type(code) is not int:
        raise VersionCodecError(f"expected an int code, got {code!r}")
    if code < 0:
        raise VersionComponentError(f"a version code must be non-negative, got {code!r}")
    if code > MAX_VERSION_CODE:
        raise VersionComponentError(
            f"a version code must be at most {MAX_VERSION_CODE} (999.999.999z), got {code}"
        )
    # The layout is dense: every code in `0..MAX_VERSION_CODE` splits into components in `0..999`
    # and an ordinal in `0..26`, so the range check above is the whole validation.
    packed, ordinal = divmod(code, _HOTFIX_SCALE)
    major, remainder = divmod(packed, _MAJOR_SCALE)
    minor, patch = divmod(remainder, _MINOR_SCALE)
    return PatchVersion(major, minor, patch, _hotfix_letter(ordinal))


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


def _hotfix_ordinal(version: PatchVersion) -> int:
    """Return the hotfix as `0..26`: no suffix is `0` and `a` is `1` (ADR-0003)."""
    if version.hotfix == "":
        return 0
    return ord(version.hotfix) - ord("a") + 1


def _hotfix_letter(ordinal: int) -> str:
    """Return the hotfix letter of an ordinal, or `""` for `0`."""
    return "" if ordinal == 0 else chr(ord("a") + ordinal - 1)


def _require_component(component: str, value: int) -> None:
    """Reject a component the three-digit encoding cannot represent, instead of truncating it."""
    if value > COMPONENT_LIMIT:
        raise VersionComponentError(
            f"{component} must be at most {COMPONENT_LIMIT} to be encoded, got {value}"
        )
