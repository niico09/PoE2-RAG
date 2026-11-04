"""Game patch version value object."""

from __future__ import annotations

import re
from dataclasses import dataclass

from poe2_rag.shared.domain.errors import InvalidPatchVersionError

_COMPONENT = r"(0|[1-9][0-9]*)"
_HOTFIX = r"[a-z]"
_PATTERN = re.compile(rf"{_COMPONENT}\.{_COMPONENT}\.{_COMPONENT}({_HOTFIX})?")
_HOTFIX_LETTER = re.compile(_HOTFIX)


@dataclass(frozen=True, slots=True, order=True)
class PatchVersion:
    """A `major.minor.patch` game version with an optional hotfix letter, ordered numerically.

    Path of Exile 2 ships hotfixes as a single lowercase letter on top of a patch (`0.5.5e`), and a
    hotfix changes balance numbers, so it is a version of its own instead of an alias of its base
    patch: `0.5.5 < 0.5.5a < ... < 0.5.5z < 0.5.6`. The comparison is the dataclass tuple
    comparison, which works because a single lowercase letter sorts lexicographically in its own
    ordinal order, and `""` (no hotfix) sorts before every letter.
    """

    major: int
    minor: int
    patch: int
    hotfix: str = ""

    def __post_init__(self) -> None:
        for name in ("major", "minor", "patch"):
            value = getattr(self, name)
            if type(value) is not int:
                raise InvalidPatchVersionError(f"{name} must be an int, got {value!r}")
            if value < 0:
                raise InvalidPatchVersionError(f"{name} must be non-negative, got {value}")
        if type(self.hotfix) is not str:
            raise InvalidPatchVersionError(
                f"hotfix must be a string, got {self.hotfix!r}"
            )
        if self.hotfix and _HOTFIX_LETTER.fullmatch(self.hotfix) is None:
            raise InvalidPatchVersionError(
                f"hotfix must be a single lowercase letter a-z, got {self.hotfix!r}"
            )

    @classmethod
    def parse(cls, raw: str) -> PatchVersion:
        """Build a version from its canonical `major.minor.patch[letter]` text, rejecting anything else."""
        if not isinstance(raw, str):
            raise InvalidPatchVersionError(f"expected a string, got {raw!r}")
        match = _PATTERN.fullmatch(raw)
        if match is None:
            raise InvalidPatchVersionError(
                f"expected 'major.minor.patch' with an optional hotfix letter a-z, got {raw!r}"
            )
        major, minor, patch, hotfix = match.groups()
        return cls(int(major), int(minor), int(patch), hotfix or "")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}{self.hotfix}"
