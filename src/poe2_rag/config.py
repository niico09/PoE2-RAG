"""Application configuration: where the artifacts live and which models the pipeline uses.

One frozen `AppConfig` describes a whole run, and one function resolves it from a `Mapping` of
environment variables. Every override is documented here by name, defaults hang from a project root,
and a value that cannot be used raises `ConfigurationError` at construction instead of failing three
stages into a pipeline.

The API key is deliberately absent: `bootstrap.py` checks that `ANTHROPIC_API_KEY` is present and the
Anthropic SDK reads the value itself, so no configuration object ever holds a credential.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from poe2_rag.shared.domain import is_blank

PROJECT_ROOT_VARIABLE = "POE2_RAG_PROJECT_ROOT"
RAW_DIR_VARIABLE = "POE2_RAG_RAW_DIR"
PROCESSED_DIR_VARIABLE = "POE2_RAG_PROCESSED_DIR"
INDEX_DIR_VARIABLE = "POE2_RAG_INDEX_DIR"
PATCH_CATALOG_VARIABLE = "POE2_RAG_PATCH_CATALOG"
EMBEDDER_MODEL_VARIABLE = "POE2_RAG_EMBEDDER_MODEL"
RERANKER_MODEL_VARIABLE = "POE2_RAG_RERANKER_MODEL"
LLM_MODEL_VARIABLE = "POE2_RAG_LLM_MODEL"
LLM_MAX_TOKENS_VARIABLE = "POE2_RAG_LLM_MAX_TOKENS"
LLM_EFFORT_VARIABLE = "POE2_RAG_LLM_EFFORT"
HTTP_CONTACT_VARIABLE = "POE2_RAG_HTTP_CONTACT"
HTTP_REQUEST_INTERVAL_VARIABLE = "POE2_RAG_HTTP_REQUEST_INTERVAL"
HTTP_TIMEOUT_VARIABLE = "POE2_RAG_HTTP_TIMEOUT"

API_KEY_VARIABLE = "ANTHROPIC_API_KEY"
"""The one credential, read by the SDK. Configuration only checks that the variable is present."""

DEFAULT_RAW_DIR = Path("data/raw")
DEFAULT_PROCESSED_DIR = Path("data/processed")
DEFAULT_INDEX_DIR = Path("data/index")
DEFAULT_PATCH_CATALOG = Path("config/patches.toml")
DEFAULT_EMBEDDER_MODEL = "BAAI/bge-base-en-v1.5"
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-base"
DEFAULT_LLM_MODEL = "claude-sonnet-5-5"
DEFAULT_LLM_MAX_TOKENS = 1024
DEFAULT_LLM_EFFORT = "medium"
DEFAULT_HTTP_REQUEST_INTERVAL = 1.0
DEFAULT_HTTP_TIMEOUT = 30.0

LLM_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
"""The reasoning depths the generation adapter accepts. Validated here so a typo fails at startup.

`tests/unit/test_config.py` pins this set to the adapter's own `_EFFORTS`: the adapter is the authority
on what the API takes, and a drift between the two must break a test instead of a first `ask`.
"""

_PATHS = ("raw_dir", "processed_dir", "index_dir", "patch_catalog_path")
_TEXT_FIELDS = ("embedder_model_id", "reranker_model_id", "llm_model", "llm_effort")
_SECONDS_FIELDS = ("http_request_interval", "http_timeout")
_CONTACT_FORBIDDEN = frozenset("()")


class ConfigurationError(ValueError):
    """Base class for every unusable configuration value, raised at construction."""


class MissingApiKeyError(ConfigurationError):
    """Raised when the API key the generation stage needs is not in the environment.

    It names the variable and never its value: the message is printed by the CLI, and a key that
    reached stderr would live on in a terminal, a log or a bug report.
    """


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Paths, model identities and the LLM budget one run uses, resolved and validated once.

    Relative paths are resolved against `project_root`, so a value read from the environment means the
    same thing from any working directory. An absolute value is kept exactly as given.
    """

    project_root: Path
    raw_dir: Path
    processed_dir: Path
    index_dir: Path
    patch_catalog_path: Path
    embedder_model_id: str
    reranker_model_id: str
    llm_model: str
    llm_max_tokens: int
    llm_effort: str
    http_contact: str | None = None
    """Contact for the poe2db `User-Agent`. `None` means "not configured": fetching fails."""
    http_request_interval: float = DEFAULT_HTTP_REQUEST_INTERVAL
    """Minimum seconds between two requests to the same host (respectful access, ADR-0002)."""
    http_timeout: float = DEFAULT_HTTP_TIMEOUT
    """Seconds a single HTTP request may take before it fails."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", _require_path("project_root", self.project_root))
        for name in _PATHS:
            path = _require_path(name, getattr(self, name))
            if not path.is_absolute():
                path = self.project_root / path
            object.__setattr__(self, name, path)
        for name in _TEXT_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str) or is_blank(value):
                raise ConfigurationError(f"{name} must be a non-blank string, got {value!r}")
        if self.llm_effort not in LLM_EFFORTS:
            raise ConfigurationError(
                f"llm_effort must be one of {sorted(LLM_EFFORTS)}, got {self.llm_effort!r}"
            )
        max_tokens = self.llm_max_tokens
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ConfigurationError(f"llm_max_tokens must be a positive int, got {max_tokens!r}")
        self._require_contact()
        for name in _SECONDS_FIELDS:
            seconds = _require_positive_seconds(name, getattr(self, name))
            object.__setattr__(self, name, seconds)

    def _require_contact(self) -> None:
        """Accept no contact or a header-safe one; a blank string is a bug, not "unset"."""
        contact = self.http_contact
        if contact is None:
            return
        if not isinstance(contact, str) or is_blank(contact):
            raise ConfigurationError(
                f"http_contact must be a non-blank string or None, got {contact!r}"
            )
        if any(character in _CONTACT_FORBIDDEN for character in contact) or any(
            character.isspace() for character in contact.strip()
        ):
            raise ConfigurationError(
                f"http_contact must not contain parentheses or whitespace, got {contact!r}"
            )
        object.__setattr__(self, "http_contact", contact.strip())


def load_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Resolve the configuration from `environ` (the process environment when it is omitted).

    Every documented `POE2_RAG_*` variable is read here and nowhere else, so a test passes a plain
    mapping instead of mutating `os.environ`. A blank value counts as unset: `uv run --env-file .env`
    hands over the empty entries of a copied `.env.example` as empty strings, and an empty path or
    model id is a trap, never an intention.
    """
    source = os.environ if environ is None else environ
    project_root = Path(_text(source, PROJECT_ROOT_VARIABLE, str(Path.cwd())))
    return AppConfig(
        project_root=project_root,
        raw_dir=Path(_text(source, RAW_DIR_VARIABLE, str(DEFAULT_RAW_DIR))),
        processed_dir=Path(_text(source, PROCESSED_DIR_VARIABLE, str(DEFAULT_PROCESSED_DIR))),
        index_dir=Path(_text(source, INDEX_DIR_VARIABLE, str(DEFAULT_INDEX_DIR))),
        patch_catalog_path=Path(_text(source, PATCH_CATALOG_VARIABLE, str(DEFAULT_PATCH_CATALOG))),
        embedder_model_id=_text(source, EMBEDDER_MODEL_VARIABLE, DEFAULT_EMBEDDER_MODEL),
        reranker_model_id=_text(source, RERANKER_MODEL_VARIABLE, DEFAULT_RERANKER_MODEL),
        llm_model=_text(source, LLM_MODEL_VARIABLE, DEFAULT_LLM_MODEL),
        llm_max_tokens=_positive_int(source, LLM_MAX_TOKENS_VARIABLE, DEFAULT_LLM_MAX_TOKENS),
        llm_effort=_choice(source, LLM_EFFORT_VARIABLE, DEFAULT_LLM_EFFORT, LLM_EFFORTS),
        http_contact=_optional_text(source, HTTP_CONTACT_VARIABLE),
        http_request_interval=_positive_seconds(
            source, HTTP_REQUEST_INTERVAL_VARIABLE, DEFAULT_HTTP_REQUEST_INTERVAL
        ),
        http_timeout=_positive_seconds(source, HTTP_TIMEOUT_VARIABLE, DEFAULT_HTTP_TIMEOUT),
    )


def _text(source: Mapping[str, str], variable: str, default: str) -> str:
    """Return the override for `variable`, or `default` when it is absent or blank."""
    value = source.get(variable)
    if value is None or is_blank(value):
        return default
    return value


def _optional_text(source: Mapping[str, str], variable: str) -> str | None:
    """Return the override for a variable that has no default, or `None` when it is unset or blank."""
    value = source.get(variable)
    if value is None or is_blank(value):
        return None
    return value


def _positive_seconds(source: Mapping[str, str], variable: str, default: float) -> float:
    """Return a positive, finite number of seconds, naming the variable when it is unusable."""
    raw = source.get(variable)
    if raw is None or is_blank(raw):
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{variable} must be a number, got {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise ConfigurationError(f"{variable} must be a positive finite number, got {raw!r}")
    return value


def _choice(
    source: Mapping[str, str], variable: str, default: str, allowed: frozenset[str]
) -> str:
    """Return the override for `variable` when it is one of `allowed`, or `default` when it is unset.

    The check runs here *and* in `AppConfig.__post_init__` on purpose: this path can name the variable
    the operator typed, which is the message that actually helps, while the value object still refuses
    an unusable value built by hand.
    """
    value = _text(source, variable, default)
    if value not in allowed:
        raise ConfigurationError(
            f"{variable} must be one of {sorted(allowed)}, got {value!r}"
        )
    return value


def _positive_int(source: Mapping[str, str], variable: str, default: int) -> int:
    """Return the override for `variable` parsed as a positive int, or `default` when it is unset."""
    raw = source.get(variable)
    if raw is None or is_blank(raw):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{variable} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigurationError(f"{variable} must be a positive integer, got {raw!r}")
    return value


def _require_positive_seconds(name: str, value: object) -> float:
    """Return `value` as a positive, finite float, rejecting bools and non-numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{name} must be a positive number, got {value!r}")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ConfigurationError(f"{name} must be a positive finite number, got {value!r}")
    return seconds


def _require_path(name: str, value: object) -> Path:
    """Return `value` as a `Path`, rejecting anything whose text is blank.

    `is_blank` treats every non-string as blank, so a `Path` is unwrapped before the check instead of
    being rejected for the form it arrived in.
    """
    if isinstance(value, (Path, str)):
        text = str(value)
    else:
        raise ConfigurationError(f"{name} must be a path, got {value!r}")
    if is_blank(text):
        raise ConfigurationError(f"{name} must be a non-blank path, got {value!r}")
    return Path(text)
