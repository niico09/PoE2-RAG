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
LLM_PROVIDER_VARIABLE = "POE2_RAG_LLM_PROVIDER"
LLM_MODEL_VARIABLE = "POE2_RAG_LLM_MODEL"
LLM_MAX_TOKENS_VARIABLE = "POE2_RAG_LLM_MAX_TOKENS"
LLM_EFFORT_VARIABLE = "POE2_RAG_LLM_EFFORT"
LLM_TIMEOUT_VARIABLE = "POE2_RAG_LLM_TIMEOUT"
HTTP_CONTACT_VARIABLE = "POE2_RAG_HTTP_CONTACT"
HTTP_REQUEST_INTERVAL_VARIABLE = "POE2_RAG_HTTP_REQUEST_INTERVAL"
HTTP_TIMEOUT_VARIABLE = "POE2_RAG_HTTP_TIMEOUT"
EVAL_DATASET_VARIABLE = "POE2_RAG_EVAL_DATASET"
EVAL_RUNS_DIR_VARIABLE = "POE2_RAG_EVAL_RUNS_DIR"

API_KEY_VARIABLE = "ANTHROPIC_API_KEY"
"""The one credential, read by the SDK. Configuration only checks that the variable is present."""

DEFAULT_RAW_DIR = Path("data/raw")
DEFAULT_PROCESSED_DIR = Path("data/processed")
DEFAULT_INDEX_DIR = Path("data/index")
DEFAULT_PATCH_CATALOG = Path("config/patches.toml")
DEFAULT_EMBEDDER_MODEL = "BAAI/bge-base-en-v1.5"
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-base"
DEFAULT_LLM_PROVIDER = "opencode"
DEFAULT_LLM_MAX_TOKENS = 1024
DEFAULT_LLM_EFFORT = "medium"
DEFAULT_LLM_TIMEOUT = 120.0

_LLM_PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "opencode": "opencode-go/deepseek-v4.1-flash",
    "claude": "claude-sonnet-5-5",
}
LLM_PROVIDERS = frozenset(_LLM_PROVIDER_DEFAULT_MODELS)
"""The generation providers the composition root can wire.

The set is derived from the provider-to-default-model mapping so an adapter cannot be accepted
without a matching default. `tests/unit/test_bootstrap.py` pins the bootstrap builder keys to this
set so the two layers cannot drift.
"""
DEFAULT_HTTP_REQUEST_INTERVAL = 1.0
DEFAULT_HTTP_TIMEOUT = 30.0
DEFAULT_EVAL_DATASET = Path("eval/dataset.jsonl")
DEFAULT_EVAL_RUNS_DIR = Path("data/eval/runs")

LLM_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
"""The reasoning depths the generation adapter accepts. Validated here so a typo fails at startup.

`tests/unit/test_config.py` pins this set to the adapter's own `_EFFORTS`: the adapter is the authority
on what the API takes, and a drift between the two must break a test instead of a first `ask`.
"""

_PATHS = (
    "raw_dir",
    "processed_dir",
    "index_dir",
    "patch_catalog_path",
    "eval_dataset_path",
    "eval_runs_dir",
)
_TEXT_FIELDS = (
    "embedder_model_id",
    "reranker_model_id",
    "llm_model",
    "llm_effort",
    "llm_provider",
)
_SECONDS_FIELDS = ("http_request_interval", "http_timeout", "llm_timeout")
_CONTACT_FORBIDDEN = frozenset("()")

_PROVIDER_ONLY_FIELDS: dict[str, tuple[str, object]] = {
    "llm_max_tokens": ("claude", DEFAULT_LLM_MAX_TOKENS),
    "llm_effort": ("claude", DEFAULT_LLM_EFFORT),
    "llm_timeout": ("opencode", DEFAULT_LLM_TIMEOUT),
}
"""The one table of settings a single provider's adapter reads, mapped to its owner and its default.

`load_config` applies an override only for the provider that reads it, and `AppConfig.__post_init__`
resets the field to that default for every other provider, so one field cannot end up with a
different value depending on how the config was built. A setting missing from this table is read by
both adapters (or neither) and is always validated. A new provider adds its row here and nowhere else.
"""


def _reads(field: str, provider: str) -> bool:
    """Return True when the adapter behind `provider` reads `field`, or when it is not provider-specific."""
    owner = _PROVIDER_ONLY_FIELDS.get(field)
    return owner is None or owner[0] == provider


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
    """Response ceiling read by the Claude adapter only; the OpenCode adapter has no such knob."""
    llm_effort: str
    """Reasoning depth read by the Claude adapter only; the OpenCode adapter has no such knob."""
    llm_provider: str = DEFAULT_LLM_PROVIDER
    """Generation provider wired by the composition root: `opencode` (local CLI) or `claude` (API)."""
    llm_timeout: float = DEFAULT_LLM_TIMEOUT
    """Seconds an OpenCode generation may run before its process group is killed; Claude ignores it."""
    http_contact: str | None = None
    """Contact for the poe2db `User-Agent`. `None` means "not configured": fetching fails."""
    http_request_interval: float = DEFAULT_HTTP_REQUEST_INTERVAL
    """Minimum seconds between two requests to the same host (respectful access, ADR-0002)."""
    http_timeout: float = DEFAULT_HTTP_TIMEOUT
    """Seconds a single HTTP request may take before it fails."""
    eval_dataset_path: Path = DEFAULT_EVAL_DATASET
    """The dataset `poe2-rag eval` measures by default: JSONL, committed, one case per line."""
    eval_runs_dir: Path = DEFAULT_EVAL_RUNS_DIR
    """Where eval reports land. Gitignored: a report is a local artifact, never a committed one."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", _require_path("project_root", self.project_root))
        for name in _PATHS:
            path = _require_path(name, getattr(self, name))
            if not path.is_absolute():
                path = self.project_root / path
            object.__setattr__(self, name, path)
        if self.llm_provider not in LLM_PROVIDERS:
            raise ConfigurationError(
                f"llm_provider must be one of {sorted(LLM_PROVIDERS)}, got {self.llm_provider!r}"
            )
        # A setting the chosen provider never reads is normalised to its default here, in the value
        # object, so `load_config` and a config built by hand cannot disagree about the same field.
        for name, (owner, default) in _PROVIDER_ONLY_FIELDS.items():
            if owner != self.llm_provider:
                object.__setattr__(self, name, default)
        for name in _TEXT_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str) or is_blank(value):
                raise ConfigurationError(f"{name} must be a non-blank string, got {value!r}")
        self._require_generation_knobs()
        self._require_contact()
        for name in _SECONDS_FIELDS:
            seconds = _require_positive_seconds(name, getattr(self, name))
            object.__setattr__(self, name, seconds)

    def _require_generation_knobs(self) -> None:
        """Validate the generation knobs.

        No provider check here on purpose: the normalisation above already replaced every knob the
        chosen provider does not read with its (valid) default, so only a value the provider really
        uses can reach these lines. `_reads` lives in `load_config`, where it changes behaviour by
        not parsing a value this class would discard.
        """
        if self.llm_effort not in LLM_EFFORTS:
            raise ConfigurationError(
                f"llm_effort must be one of {sorted(LLM_EFFORTS)}, got {self.llm_effort!r}"
            )
        max_tokens = self.llm_max_tokens
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ConfigurationError(
                f"llm_max_tokens must be a positive int, got {max_tokens!r}"
            )

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
    provider = _choice(source, LLM_PROVIDER_VARIABLE, DEFAULT_LLM_PROVIDER, LLM_PROVIDERS)
    return AppConfig(
        project_root=project_root,
        raw_dir=Path(_text(source, RAW_DIR_VARIABLE, str(DEFAULT_RAW_DIR))),
        processed_dir=Path(_text(source, PROCESSED_DIR_VARIABLE, str(DEFAULT_PROCESSED_DIR))),
        index_dir=Path(_text(source, INDEX_DIR_VARIABLE, str(DEFAULT_INDEX_DIR))),
        patch_catalog_path=Path(_text(source, PATCH_CATALOG_VARIABLE, str(DEFAULT_PATCH_CATALOG))),
        embedder_model_id=_text(source, EMBEDDER_MODEL_VARIABLE, DEFAULT_EMBEDDER_MODEL),
        reranker_model_id=_text(source, RERANKER_MODEL_VARIABLE, DEFAULT_RERANKER_MODEL),
        llm_model=_text(source, LLM_MODEL_VARIABLE, _default_llm_model(provider)),
        # Provider-specific settings: an override is read only for the provider that uses it, so a
        # stale or misspelled value cannot block a run whose adapter never reads it.
        llm_max_tokens=(
            _positive_int(source, LLM_MAX_TOKENS_VARIABLE, DEFAULT_LLM_MAX_TOKENS)
            if _reads("llm_max_tokens", provider)
            else DEFAULT_LLM_MAX_TOKENS
        ),
        llm_effort=(
            _choice(source, LLM_EFFORT_VARIABLE, DEFAULT_LLM_EFFORT, LLM_EFFORTS)
            if _reads("llm_effort", provider)
            else DEFAULT_LLM_EFFORT
        ),
        llm_provider=provider,
        llm_timeout=(
            _positive_seconds(source, LLM_TIMEOUT_VARIABLE, DEFAULT_LLM_TIMEOUT)
            if _reads("llm_timeout", provider)
            else DEFAULT_LLM_TIMEOUT
        ),
        http_contact=_optional_text(source, HTTP_CONTACT_VARIABLE),
        http_request_interval=_positive_seconds(
            source, HTTP_REQUEST_INTERVAL_VARIABLE, DEFAULT_HTTP_REQUEST_INTERVAL
        ),
        http_timeout=_positive_seconds(source, HTTP_TIMEOUT_VARIABLE, DEFAULT_HTTP_TIMEOUT),
        eval_dataset_path=Path(_text(source, EVAL_DATASET_VARIABLE, str(DEFAULT_EVAL_DATASET))),
        eval_runs_dir=Path(_text(source, EVAL_RUNS_DIR_VARIABLE, str(DEFAULT_EVAL_RUNS_DIR))),
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


def _default_llm_model(provider: str) -> str:
    """Return the adapter's default model for a validated provider, failing closed on unknown keys.

    `_choice` already validates the provider at the environment boundary; this helper exists so the
    default-model mapping is the single source of truth and an unknown provider never silently falls
    back to a default.
    """
    if provider not in _LLM_PROVIDER_DEFAULT_MODELS:
        raise ConfigurationError(f"unknown llm provider {provider!r}")
    return _LLM_PROVIDER_DEFAULT_MODELS[provider]


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
