"""Patch validity range value object."""

from __future__ import annotations

from dataclasses import dataclass

from poe2_rag.shared.domain.errors import InvalidValidityError
from poe2_rag.shared.domain.patch_version import PatchVersion


@dataclass(frozen=True, slots=True)
class Validity:
    """Half-open patch range `[valid_from, valid_until)`; `valid_until=None` means still valid."""

    valid_from: PatchVersion
    valid_until: PatchVersion | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.valid_from, PatchVersion):
            raise InvalidValidityError(f"valid_from must be a PatchVersion, got {self.valid_from!r}")
        if self.valid_until is None:
            return
        if not isinstance(self.valid_until, PatchVersion):
            raise InvalidValidityError(f"valid_until must be a PatchVersion, got {self.valid_until!r}")
        if not self.valid_from < self.valid_until:
            raise InvalidValidityError(
                f"valid_from ({self.valid_from}) must be lower than valid_until ({self.valid_until})"
            )

    def is_valid_at(self, version: PatchVersion) -> bool:
        """Return True when `version` falls inside the range (lower bound inclusive, upper exclusive).

        Raise `InvalidValidityError` when `version` is not a `PatchVersion`.
        """
        if not isinstance(version, PatchVersion):
            raise InvalidValidityError(f"version must be a PatchVersion, got {version!r}")
        if version < self.valid_from:
            return False
        return self.valid_until is None or version < self.valid_until
