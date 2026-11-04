"""JSON evaluation report writer.

The report is written atomically (temporary file in the target directory plus `os.replace`), so a
failed write never leaves a truncated report: either the previous report survives or the new one is
complete.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from datetime import UTC
from pathlib import Path

from poe2_rag.evaluation.application.errors import EvaluationError
from poe2_rag.evaluation.application.report import COMMIT_ID_PATTERN, EvaluationReport

_DIGEST_PREFIX = 8
"""How much of the dataset digest disambiguates two runs inside the same second."""


class ReportWriteError(EvaluationError):
    """Raised when the report file cannot be written."""


class JsonReportWriter:
    """Persist evaluation reports as JSON files under a directory."""

    def write(self, report: EvaluationReport, directory: Path) -> Path:
        """Write `report` into `directory` and return the file path.

        The file name is derived from the report, so it is validated before anything is created: a
        commit id that could not be a single path segment fails here instead of writing outside the
        runs directory.
        """
        run_at = report.run.run_at.astimezone(UTC)
        commit = report.run.git_commit
        if commit is not None and not COMMIT_ID_PATTERN.fullmatch(commit):
            raise ReportWriteError(
                f"the commit id {commit!r} cannot be used in a report file name"
            )
        suffix = commit if commit is not None else "nogit"
        # The timestamp has second resolution, so two datasets (or two K sets) started inside the
        # same second would write the same path and `os.replace` would drop one run silently. The
        # digest prefix makes the name unique per dataset while keeping a re-run idempotent.
        digest = report.dataset.sha256[:_DIGEST_PREFIX]
        filename = f"{run_at.strftime('%Y%m%dT%H%M%SZ')}-{suffix}-{digest}.json"
        final_path = directory / filename

        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ReportWriteError(f"cannot create {directory}: {exc}") from exc

        payload = json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n"

        temporary: str | None = None
        try:
            handle, temporary = tempfile.mkstemp(
                dir=directory, prefix=".eval-", suffix=".tmp"
            )
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, final_path)
            temporary = None
        except OSError as exc:
            raise ReportWriteError(f"cannot write {final_path}: {exc}") from exc
        finally:
            if temporary is not None:
                with suppress(OSError):
                    os.unlink(temporary)

        return final_path
