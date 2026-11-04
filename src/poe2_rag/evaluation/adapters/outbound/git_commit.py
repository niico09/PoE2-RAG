"""Git commit probe for evaluation reports."""

from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_TIMEOUT = 5


def current_commit(root: Path) -> str | None:
    """Return the short SHA of the current HEAD in `root`, or `None` when git is unavailable.

    Any failure (missing binary, no repository, timeout) returns `None`: a report without a commit
    is still valid, but a crash would not be.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    sha = result.stdout.strip()
    return sha if sha else None
