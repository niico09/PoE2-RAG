"""Game patch version value object."""

from __future__ import annotations

import re
from dataclasses import dataclass

from poe2_rag.shared.domain.errors import InvalidPatchVersionError

_COMPONENT = r"(0|[1-9][0-9]*)"
_PATTERN = re.compile(rf"{_COMPONENT}\.{_COMPONENT}\.{_COMPONENT}")


@dataclass(frozen=True, slots=True, order=True)
class PatchVersion:
    """A `major.minor.patch` game version, ordered numerically component by component."""

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        for name in ("major", "minor", "patch"):
            value = getattr(self, name)
            if type(value) is not int:
                raise InvalidPatchVersionError(f"{name} must be an int, got {value!r}")
            if value < 0:
                raise InvalidPatchVersionError(f"{name} must be non-negative, got {value}")

    @classmethod
    def parse(cls, raw: str) -> PatchVersion:
        """Build a version from its canonical `major.minor.patch` text, rejecting anything else."""
        if not isinstance(raw, str):
            raise InvalidPatchVersionError(f"expected a string, got {raw!r}")
        match = _PATTERN.fullmatch(raw)
        if match is None:
            raise InvalidPatchVersionError(f"expected 'major.minor.patch', got {raw!r}")
        major, minor, patch = (int(group) for group in match.groups())
        return cls(major, minor, patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"
