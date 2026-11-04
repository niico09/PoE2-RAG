"""Adapter-specific behaviour of the git commit probe."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from poe2_rag.evaluation.adapters.outbound import current_commit

_ROOT = Path("/repo/root")


class TestCurrentCommit:
    def test_success_returns_trimmed_sha(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=("git", "rev-parse", "--short", "HEAD"),
                returncode=0,
                stdout="6fb9b03\n",
                stderr="",
            )

        monkeypatch.setattr("subprocess.run", fake_run)

        assert current_commit(_ROOT) == "6fb9b03"

    def test_non_zero_exit_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=("git", "rev-parse", "--short", "HEAD"),
                returncode=1,
                stdout="",
                stderr="fatal",
            )

        monkeypatch.setattr("subprocess.run", fake_run)

        assert current_commit(_ROOT) is None

    def test_blank_stdout_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=("git", "rev-parse", "--short", "HEAD"),
                returncode=0,
                stdout="   \n",
                stderr="",
            )

        monkeypatch.setattr("subprocess.run", fake_run)

        assert current_commit(_ROOT) is None

    def test_os_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_os(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise OSError("boom")

        monkeypatch.setattr("subprocess.run", raise_os)

        assert current_commit(_ROOT) is None

    def test_timeout_expired_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_timeout(
            *args: str, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd="git", timeout=5)

        monkeypatch.setattr("subprocess.run", raise_timeout)

        assert current_commit(_ROOT) is None
