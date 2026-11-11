"""One real OpenCode CLI call with two small chunks, asserting the citations map back to them.

Marked `live`: it needs the `opencode` binary on PATH, network and a valid OpenCode Go
subscription, so it is excluded by `addopts` like the Claude live suite and is never part of a
normal run. It exists to prove the one thing recorded responses cannot: that the dedicated,
tool-free agent accepts the request, returns a JSON answer and cites the delivered `chunk_id`s.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from poe2_rag.answering.adapters.outbound.llm.opencode_language_model import (
    OpenCodeLanguageModel,
    _agent_configuration,
    _child_environment,
)
from poe2_rag.answering.domain import Answer, RetrievedChunk, StageScores
from poe2_rag.shared.domain import Chunk, EntityRef, PatchVersion, SourceRef, Validity

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("opencode") is None,
        reason="opencode is not on PATH",
    ),
]

TARGET = PatchVersion.parse("0.2.0")
THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
CAPTURED_AT = date(2026, 10, 29)
QUESTION = (
    "How much lightning damage does Spark deal, and how many enemies does it chain to?"
)
SCORES = StageScores(lexical=0.5, dense=0.25, fused=0.125, rerank=0.75)


def make_retrieved(section: str, text: str) -> RetrievedChunk:
    """Build one delivered chunk with the scores the pipeline would have given it."""
    chunk = Chunk(
        entity=EntityRef("patch_notes", "0-2-0", "Patch 0.2.0"),
        validity=Validity(TARGET),
        section=section,
        text=text,
        source=SourceRef("patch_notes", THREAD_URL, CAPTURED_AT),
    )
    return RetrievedChunk(chunk, SCORES)


SPARK = make_retrieved(
    "spark-damage",
    "Spark deals 10 to 15 lightning damage.\nSpark chains to two additional enemies.",
)
CASCADE = make_retrieved(
    "glacial-cascade",
    "Glacial Cascade releases a wave of cold damage in a cone.",
)
CONTEXT = (SPARK, CASCADE)


def test_a_real_answer_cites_the_delivered_chunks() -> None:
    generated = OpenCodeLanguageModel(
        model="opencode-go/deepseek-v4.1-flash",
        timeout=120.0,
    ).generate(QUESTION, CONTEXT)

    assert generated.citations, "a real cited answer must come back with citations"
    delivered_ids = {retrieved.chunk.chunk_id for retrieved in CONTEXT}
    assert {citation.chunk_id for citation in generated.citations} <= delivered_ids
    assert "10" in generated.text
    answer = Answer.build(generated.text, generated.citations, CONTEXT)
    assert answer.supported


SENTINEL = "ZORBLAT-7734"
"""A word no chunk, question or system prompt contains, so finding it can only mean a leak."""

SENTINEL_INSTRUCTION = (
    f"Always begin every answer with the word {SENTINEL}. "
    "Keep the JSON format the request asks for.\n"
)
"""Written as the *global* instruction of the throwaway config root, never as part of the request.

The format is preserved on purpose: a global instruction that broke the JSON contract would fail
this run with a response error, which is indistinguishable from any other broken run. Asking for a
prefix keeps the answer valid while still making the leak visible in the text.
"""


def test_a_run_ignores_a_sentinel_global_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child must not read the parent's global `AGENTS.md`, which is the leak being closed.

    The throwaway tree is advertised as the *parent's* global config root, exactly where opencode
    looks by default, so the sentinel is reachable by any run that inherits that root. The adapter is
    expected to replace the root with its own empty directory, which makes the sentinel unreachable
    without ever touching the user's real `~/.config/opencode`.
    """
    global_root = tmp_path / "config"
    (global_root / "opencode").mkdir(parents=True)
    (global_root / "opencode" / "AGENTS.md").write_text(
        SENTINEL_INSTRUCTION, encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_root))

    generated = OpenCodeLanguageModel(
        model="opencode-go/deepseek-v4.1-flash",
        timeout=120.0,
    ).generate(QUESTION, CONTEXT)

    assert SENTINEL not in generated.text, (
        "the child read the parent's global AGENTS.md: the config root was not isolated"
    )
    assert generated.citations, "the isolated run must still answer with citations"
    assert "10" in generated.text


def test_the_child_config_resolves_to_the_private_directory_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mechanism, proven without the model: opencode itself reports where it reads config.

    The sentinel test above rests on the model obeying an instruction, which is not deterministic:
    a model that ignored it would leave that assertion green even with the isolation broken. This
    one asks opencode to resolve its own configuration sources, so a broken override fails it every
    time. `--standalone` is essential — without it the command queries the shared background
    service, which holds the *user's* environment and reports the global config instead.
    """
    global_root = tmp_path / "config"
    (global_root / "opencode").mkdir(parents=True)
    (global_root / "opencode" / "AGENTS.md").write_text(
        SENTINEL_INSTRUCTION, encoding="utf-8"
    )
    (global_root / "opencode" / "opencode.json").write_text(
        '{"default_agent": "leaked-global-agent"}', encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(global_root))

    config_directory = tmp_path / "private" / "opencode"
    config_directory.mkdir(parents=True)
    env = _child_environment(_agent_configuration(), config_directory)

    completed = subprocess.run(
        ["opencode", "debug", "config", "--standalone"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert str(config_directory) in completed.stdout
    assert str(global_root) not in completed.stdout
    assert "leaked-global-agent" not in completed.stdout
