"""OpenCode generation with a dedicated no-tool agent and a hard subprocess timeout (S1-S6, S12).

The adapter runs `opencode run` in a private process group, parses the JSONL event stream,
extracts the model's JSON answer and maps its citations back to the delivered chunks. All
errors are part of the shared `LanguageModel*` hierarchy so `bootstrap.EXPECTED_ERRORS` keeps
working unchanged.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import signal
import subprocess
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from poe2_rag.answering.adapters.outbound.llm.contract import (
    quotes_chunk,
    require_context,
    require_question,
)
from poe2_rag.answering.adapters.outbound.llm.errors import (
    LanguageModelConfigurationError,
    LanguageModelConnectionError,
    LanguageModelResponseError,
    LanguageModelTimeoutError,
)
from poe2_rag.answering.application import GeneratedAnswer
from poe2_rag.answering.domain import Citation, RetrievedChunk
from poe2_rag.shared.domain import is_blank

DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"
DEFAULT_TIMEOUT = 120.0
AGENT_NAME = "poe2-answer"
_DELIMITER_PATTERN = re.compile(r"<\s*/?\s*source", re.IGNORECASE)

_PIPE_DRAIN_SECONDS = 5.0

_INHERITED_ENVIRONMENT = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
)
"""What the CLI needs to find its own binary, its profile and its stored login.

Restricted on purpose: `opencode` is a third-party process and has no business reading
`ANTHROPIC_API_KEY` (or any other exported secret) from the environment it inherits.
"""

_SYSTEM_PROMPT = (
    "Answer only from the chunks provided in this request. "
    'Reply with JSON only: {"answer": str, "citations": [{"chunk_id": str, "quoted_text": str}]}. '
    "When the chunks do not answer the question, say so and return no citations. "
    "Never use outside knowledge about Path of Exile 2, even when you already know it."
)


@runtime_checkable
class ProcessRunner(Protocol):
    """Run an external command and return its result.

    An implementation must raise `LanguageModelTimeoutError` when the command does not finish
    within the requested `timeout`.
    """

    def run(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run `argv` with `env`, failing after `timeout` seconds."""
        ...


class SubprocessRunner:
    """Default `ProcessRunner`: isolated process group and whole-group kill on timeout."""

    def run(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run `argv` with `start_new_session=True` and kill the group on timeout."""
        try:
            process = subprocess.Popen(
                argv,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise LanguageModelConfigurationError(
                "opencode was not found on PATH; ensure the opencode CLI is installed and available"
            ) from exc
        except OSError as exc:
            raise LanguageModelConnectionError(
                f"opencode could not be started: {exc}"
            ) from exc
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self._kill_group(process)
            # Drain the pipes so the descriptors close even when a grandchild still holds the
            # other end, then reap the child that the group kill already terminated.
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.communicate(timeout=_PIPE_DRAIN_SECONDS)
            if process.poll() is None:
                process.kill()
                process.wait()
            raise LanguageModelTimeoutError(
                f"opencode did not finish within {timeout} seconds"
            ) from exc
        return subprocess.CompletedProcess(
            args=argv,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    @staticmethod
    def _kill_group(process: subprocess.Popen[str]) -> None:
        """Kill the whole process group, tolerating races with process exit."""
        try:
            pgid = os.getpgid(process.pid)
        except ProcessLookupError:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)


class OpenCodeLanguageModel:
    """Answer from delivered chunks with the `opencode` CLI behind the `LanguageModel` port."""

    __slots__ = ("_model", "_runner", "_timeout")

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        runner: ProcessRunner | None = None,
    ) -> None:
        if is_blank(model):
            raise LanguageModelConfigurationError(
                f"model must be a non-blank string, got {model!r}"
            )
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise LanguageModelConfigurationError(
                f"timeout must be a positive finite number, got {timeout!r}"
            )
        self._model = model
        self._timeout = float(timeout)
        self._runner = runner if runner is not None else SubprocessRunner()

    @property
    def model(self) -> str:
        """The model this adapter answers with."""
        return self._model

    def generate(
        self, question: str, context: Sequence[RetrievedChunk]
    ) -> GeneratedAnswer:
        """Answer `question` from `context` only, returning the text and the citations it claims."""
        require_question(question)
        delivered = require_context(context)
        argv, env = self._command(question, delivered)
        result = self._runner.run(argv, env=env, timeout=self._timeout)
        return _parse(result, delivered)

    def _command(
        self, question: str, delivered: tuple[RetrievedChunk, ...]
    ) -> tuple[list[str], dict[str, str]]:
        """Build the opencode invocation and the child environment."""
        config = {
            "default_agent": AGENT_NAME,
            "instructions": [],
            "agent": {
                AGENT_NAME: {
                    "mode": "primary",
                    "description": "minimal answer agent",
                    "prompt": _SYSTEM_PROMPT,
                    "tools": {"*": False},
                    "steps": 1,
                }
            },
        }
        env = _child_environment(config)
        prompt = _build_prompt(delivered, question)
        argv = [
            "opencode",
            "run",
            "--standalone",
            "--format",
            "json",
            "-m",
            self._model,
            "--agent",
            AGENT_NAME,
            prompt,
        ]
        return argv, env


def _child_environment(config: dict[str, Any]) -> dict[str, str]:
    """Return the child environment, filtered down to what the CLI needs plus our overrides."""
    environment = {
        name: value
        for name in _INHERITED_ENVIRONMENT
        if (value := os.environ.get(name)) is not None
    }
    environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(config, separators=(",", ":"))
    environment["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
    return environment


def _build_prompt(delivered: tuple[RetrievedChunk, ...], question: str) -> str:
    """Wrap each delivered chunk in `<source chunk_id="...">...</source>`, then ask the question.

    The chunk_id and text are interpolated, so a chunk containing the delimiter could close its
    own wrapper and let following text reach the same level as the question. `_neutralise_delimiters`
    closes that hole only: it stops the wrapper from being closed early and nothing more. Chunk text
    can still contain instructions inside its own `<source>` block, which is why the boundary is
    held by the agent running with `"tools": {"*": false}` and by the citation check comparing every
    quote against the chunk it names, not by this escaping.
    """
    parts: list[str] = []
    for retrieved in delivered:
        chunk_id = _neutralise_delimiters(retrieved.chunk.chunk_id)
        text = _neutralise_delimiters(retrieved.chunk.text)
        parts.append(f'<source chunk_id="{chunk_id}">{text}</source>')
    parts.append(question)
    return "\n\n".join(parts)


def _neutralise_delimiters(value: str) -> str:
    """Break any source-wrapper delimiter so payload text cannot close the tag.

    Case-insensitive and independent of the closing `>`: `</SOURCE>`, `</ Source` or a `</source`
    followed by a newline would otherwise survive and still close the wrapper for a lax parser.
    """
    return _DELIMITER_PATTERN.sub(
        lambda match: match.group(0).replace("<", "< "), value
    )


def _parse(
    result: subprocess.CompletedProcess[str],
    delivered: tuple[RetrievedChunk, ...],
) -> GeneratedAnswer:
    """Parse the JSONL stream and validate citations against the delivered chunks."""
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        message = f"opencode returned exit code {result.returncode}"
        if stderr:
            message = f"{message}: {_truncate(stderr, 200)}"
        raise LanguageModelResponseError(message)
    text = _text_from_events(result.stdout or "")
    if text is None:
        raise LanguageModelResponseError("opencode produced no text event")
    answer, raw_citations = _parse_answer_json(text)
    citations = _validated_citations(raw_citations, delivered)
    return GeneratedAnswer(text=answer, citations=tuple(citations))


def _text_from_events(stdout: str) -> str | None:
    """Return the assistant text that carries the JSON answer.

    The CLI emits one `text` event per step and `steps: 1` normally yields exactly one, but a
    multi-step run can append a stray sentence before or after the payload. Joining every part
    would then produce `{...}{...}`, which is not valid JSON, so the answer is the LAST text part
    whose content parses as a JSON object. When none does, the last part is returned and the JSON
    parser reports the malformed payload in its bounded message.
    """
    texts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "text":
            continue
        part = event.get("part")
        if isinstance(part, dict):
            part_text = part.get("text")
            if isinstance(part_text, str):
                texts.append(part_text)
    if not texts:
        return None
    for candidate in reversed(texts):
        if _is_json_object(candidate):
            return candidate
    return texts[-1]


def _is_json_object(text: str) -> bool:
    """Return True when `text` parses as a JSON object, fences allowed."""
    try:
        return isinstance(json.loads(_strip_code_fence(text)), dict)
    except json.JSONDecodeError:
        return False


def _strip_code_fence(text: str) -> str:
    """Return `text` without a surrounding Markdown fence, which some models add anyway."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json")
        cleaned = cleaned.removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    return cleaned


def _parse_answer_json(text: str) -> tuple[str, list[Any]]:
    """Parse the model's JSON answer, rejecting malformed or schema-invalid payloads.

    Only the list-ness of `citations` is known here; each element is validated by
    `_validated_citations`, which is why the element type is `Any` and not `dict`.
    """
    cleaned = _strip_code_fence(text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LanguageModelResponseError(
            f"model output is not valid JSON: {_truncate(cleaned)}"
        ) from exc
    if not isinstance(payload, dict):
        raise LanguageModelResponseError(
            f"model output is not a JSON object: {_truncate(cleaned)}"
        )
    answer = payload.get("answer")
    if not isinstance(answer, str) or is_blank(answer):
        raise LanguageModelResponseError(
            "model output lacks a non-blank 'answer' string"
        )
    if "citations" not in payload:
        raise LanguageModelResponseError("model output lacks a 'citations' list")
    raw_citations = payload["citations"]
    if not isinstance(raw_citations, list):
        raise LanguageModelResponseError("model output 'citations' must be a list")
    return answer, raw_citations


def _validated_citations(
    raw_citations: Sequence[object],
    delivered: tuple[RetrievedChunk, ...],
) -> list[Citation]:
    """Map raw citations to domain citations, checking verbatim quotes.

    A citation whose `chunk_id` was not delivered is treated the same as a non-verbatim quote:
    both are model-authored response errors, so the caller sees a single `LanguageModelResponseError`
    type and the domain `InvalidCitationError` stays reserved for downstream code that bypasses
    the adapter.
    """
    chunks_by_id = {
        retrieved.chunk.chunk_id: retrieved.chunk.text for retrieved in delivered
    }
    citations: list[Citation] = []
    for index, raw in enumerate(raw_citations):
        if not isinstance(raw, dict):
            raise LanguageModelResponseError(f"citation {index} is not an object")
        chunk_id = raw.get("chunk_id")
        quoted_text = raw.get("quoted_text")
        if not isinstance(chunk_id, str) or is_blank(chunk_id):
            raise LanguageModelResponseError(
                f"citation {index} has no non-blank chunk_id"
            )
        if not isinstance(quoted_text, str) or is_blank(quoted_text):
            raise LanguageModelResponseError(
                f"citation {index} has no non-blank quoted_text"
            )
        chunk_text = chunks_by_id.get(chunk_id)
        if chunk_text is None:
            raise LanguageModelResponseError(
                f"citation {index} references chunk {chunk_id!r}, which was not delivered"
            )
        if not quotes_chunk(quoted_text, chunk_text):
            raise LanguageModelResponseError(
                f"citation {index} quotes text that is not part of chunk {chunk_id!r}"
            )
        citations.append(Citation(chunk_id, quoted_text))
    return citations


def _truncate(text: str, limit: int = 80) -> str:
    """Return a short, single-line preview of `text` for secret-free error messages."""
    collapsed = " ".join(text.splitlines())
    if len(collapsed) > limit:
        return collapsed[: limit - 3] + "..."
    return collapsed
