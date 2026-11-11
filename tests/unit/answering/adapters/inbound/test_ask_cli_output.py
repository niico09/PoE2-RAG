"""The loader silencing the CLI entry point applies, and the purity of `build_container` (S4).

`ask` is the only command that loads two local models, and both loaders print progress bars and an
unauthenticated-Hub warning unless the process environment says otherwise. A unit test cannot watch a
loader print — that needs a real model — so this module pins the three things that *are* observable
and that the silencing actually rests on:

- the import chain the CLI runs pulls neither library in, so the flags can still be set in time;
- the CLI entry point sets the three flags before the container exists, with the libraries still
  unimported;
- `build_container` stays a value of its mapping and touches nothing.

They matter because both libraries read their flags at import time: a new module-level dependency that
imported `huggingface_hub` would freeze them before `_default_container` ran, and nothing but a
`sys.modules` check would notice. That is why the probe runs in a subprocess — the pytest process has
already imported half the world.

What `ask` prints is pinned once, in `test_ask_cli_command.py` next door, and is not repeated here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import pytest

from poe2_rag.bootstrap import build_container

NOISE_VARIABLES = (
    "HF_HUB_DISABLE_PROGRESS_BARS",
    "HF_HUB_VERBOSITY",
    "TRANSFORMERS_VERBOSITY",
)
"""The variables the loaders read, and the ones the CLI entry point has to set for them."""

MODEL_LIBRARIES = ("transformers", "huggingface_hub")
"""The two libraries that freeze their progress-bar settings at import time."""

_SILENCING_PROBE = f"""
import json
import os
import sys

import poe2_rag.bootstrap  # noqa: F401  - the composition root, imported by the CLI anyway
from poe2_rag import cli

before = {{name: name in sys.modules for name in {MODEL_LIBRARIES!r}}}
cli._default_container()
after = {{name: name in sys.modules for name in {MODEL_LIBRARIES!r}}}
flags = {{name: os.environ.get(name) for name in {NOISE_VARIABLES!r}}}
print(json.dumps({{"before": before, "after": after, "flags": flags}}))
"""
"""Import the CLI, build the real container, and report what the process looked like at each step."""


def run_probe() -> dict[str, Any]:
    """Run the probe in a fresh interpreter that inherits none of the loader flags."""
    env = {
        name: value for name, value in os.environ.items() if name not in NOISE_VARIABLES
    }
    completed = subprocess.run(
        [sys.executable, "-c", _SILENCING_PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def silencing_probe() -> dict[str, Any]:
    """One probe run shared by both assertions: it builds the real container, so it is not free."""
    return run_probe()


def test_the_composition_root_does_not_import_the_model_libraries(
    silencing_probe: dict[str, Any],
) -> None:
    """Importing the composition root must leave the flags settable, so neither library may load."""
    assert silencing_probe["before"] == dict.fromkeys(MODEL_LIBRARIES, False)


def test_the_cli_entry_point_sets_the_loader_flags_before_anything_loads_them(
    silencing_probe: dict[str, Any],
) -> None:
    """The flags have to be set while the libraries are still unimported, or they are read too late."""
    assert silencing_probe["after"] == dict.fromkeys(MODEL_LIBRARIES, False)
    assert silencing_probe["flags"] == {
        "HF_HUB_DISABLE_PROGRESS_BARS": "1",
        "HF_HUB_VERBOSITY": "error",
        "TRANSFORMERS_VERBOSITY": "error",
    }


def test_build_container_does_not_touch_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silencing is a process-wide effect of the CLI entry point, so the composition root must not do it.

    Without this, putting `silence_model_loading_noise()` back inside `build_container` would leave
    the suite green: the probe above only checks the positive path of the CLI.
    """
    for name in NOISE_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    build_container({})

    for name in NOISE_VARIABLES:
        assert name not in os.environ, f"build_container set {name}"
