"""What one evaluation run is asked for: a dataset, a K set and where the report goes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from poe2_rag.evaluation.application.errors import InvalidEvaluationRequestError
from poe2_rag.evaluation.domain import InvalidMetricsError, require_ks

DEFAULT_KS: tuple[int, ...] = (1, 3, 5)
"""The K values a run measures unless it is asked for others: top-1, a short window and the budget."""


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    """The arguments of one run; every field is optional and falls back to the wiring's default.

    `dataset=None` means "the dataset the container is configured with", and `output_dir=None` means
    "the configured runs directory", so the CLI can pass what the user actually typed and let the
    composition root own the defaults (S7).
    """

    dataset: Path | None = None
    ks: tuple[int, ...] = DEFAULT_KS
    output_dir: Path | None = None

    def __post_init__(self) -> None:
        for name in ("dataset", "output_dir"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Path):
                raise InvalidEvaluationRequestError(f"{name} must be a Path or None, got {value!r}")
        try:
            ordered = require_ks(self.ks)
        except InvalidMetricsError as exc:
            raise InvalidEvaluationRequestError(str(exc)) from exc
        object.__setattr__(self, "ks", ordered)
