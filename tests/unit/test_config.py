"""Configuration: documented overrides from a mapping, defaults relative to the project root (S1).

Every case passes a `Mapping` instead of touching `os.environ`, so the suite pins the exact
documented variable names without depending on the shell that runs it. The HTTP block covers the
poe2db settings added for the polite fetcher: no committed contact, a positive request interval and a
positive timeout. The last block checks the one thing configuration must never do: read or keep the
API key.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from poe2_rag.answering.adapters.outbound.dense.bge_embedder import DEFAULT_MODEL_ID
from poe2_rag.answering.adapters.outbound.llm.claude_language_model import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
)
from poe2_rag.answering.adapters.outbound.rerank.cross_encoder_reranker import (
    DEFAULT_MODEL_NAME,
)
from poe2_rag.config import (
    DEFAULT_HTTP_REQUEST_INTERVAL,
    DEFAULT_HTTP_TIMEOUT,
    LLM_EFFORTS,
    AppConfig,
    ConfigurationError,
    load_config,
)

PROJECT_ROOT = Path("/srv/poe2-rag")


def resolve(**overrides: str) -> AppConfig:
    """Load the configuration from a mapping that only holds the overrides a case cares about."""
    return load_config({"POE2_RAG_PROJECT_ROOT": str(PROJECT_ROOT), **overrides})


def values(**overrides: object) -> dict[str, object]:
    """Build the keyword arguments of a valid `AppConfig`, so a case states only what it breaks."""
    return {
        "project_root": PROJECT_ROOT,
        "raw_dir": PROJECT_ROOT / "data/raw",
        "processed_dir": PROJECT_ROOT / "data/processed",
        "index_dir": PROJECT_ROOT / "data/index",
        "patch_catalog_path": PROJECT_ROOT / "config/patches.toml",
        "embedder_model_id": "BAAI/bge-base-en-v1.5",
        "reranker_model_id": "BAAI/bge-reranker-base",
        "llm_model": "claude-sonnet-5-5",
        "llm_max_tokens": 1024,
        "llm_effort": "medium",
        **overrides,
    }


def test_the_defaults_hang_from_the_project_root() -> None:
    config = resolve()

    assert config.project_root == PROJECT_ROOT
    assert config.raw_dir == PROJECT_ROOT / "data/raw"
    assert config.processed_dir == PROJECT_ROOT / "data/processed"
    assert config.index_dir == PROJECT_ROOT / "data/index"
    assert config.patch_catalog_path == PROJECT_ROOT / "config/patches.toml"


def test_the_default_project_root_is_the_working_directory() -> None:
    assert load_config({}).project_root == Path.cwd()


def test_the_default_model_ids_and_llm_budget_are_the_adapters_own_defaults() -> None:
    """One value per model, not two: a drift between the config and an adapter must fail here."""
    config = resolve()

    assert config.embedder_model_id == DEFAULT_MODEL_ID
    assert config.reranker_model_id == DEFAULT_MODEL_NAME
    assert config.llm_model == DEFAULT_MODEL
    assert config.llm_max_tokens == DEFAULT_MAX_TOKENS
    assert config.llm_effort == DEFAULT_EFFORT


def test_every_documented_variable_overrides_its_value() -> None:
    config = resolve(
        POE2_RAG_RAW_DIR="snapshots",
        POE2_RAG_PROCESSED_DIR="corpus",
        POE2_RAG_INDEX_DIR="indexes",
        POE2_RAG_PATCH_CATALOG="config/other-patches.toml",
        POE2_RAG_EMBEDDER_MODEL="BAAI/bge-small-en-v1.5",
        POE2_RAG_RERANKER_MODEL="BAAI/bge-reranker-v2-m3",
        POE2_RAG_LLM_MODEL="claude-opus-5-5",
        POE2_RAG_LLM_MAX_TOKENS="2048",
        POE2_RAG_LLM_EFFORT="high",
        POE2_RAG_HTTP_CONTACT="ops@example.com",
        POE2_RAG_HTTP_REQUEST_INTERVAL="2.5",
        POE2_RAG_HTTP_TIMEOUT="15",
    )

    assert config.raw_dir == PROJECT_ROOT / "snapshots"
    assert config.processed_dir == PROJECT_ROOT / "corpus"
    assert config.index_dir == PROJECT_ROOT / "indexes"
    assert config.patch_catalog_path == PROJECT_ROOT / "config/other-patches.toml"
    assert config.embedder_model_id == "BAAI/bge-small-en-v1.5"
    assert config.reranker_model_id == "BAAI/bge-reranker-v2-m3"
    assert config.llm_model == "claude-opus-5-5"
    assert config.llm_max_tokens == 2048
    assert config.llm_effort == "high"
    assert config.http_contact == "ops@example.com"
    assert config.http_request_interval == 2.5
    assert config.http_timeout == 15.0


def test_an_absolute_override_stays_absolute() -> None:
    config = resolve(POE2_RAG_INDEX_DIR="/var/lib/poe2-rag/index")

    assert config.index_dir == Path("/var/lib/poe2-rag/index")


def test_a_blank_value_means_use_the_default() -> None:
    """A copied `.env.example` fills the empty entries; they must not become an empty path or id."""
    config = resolve(
        POE2_RAG_RAW_DIR="",
        POE2_RAG_LLM_MAX_TOKENS="",
        POE2_RAG_LLM_MODEL="   ",
    )

    assert config.raw_dir == PROJECT_ROOT / "data/raw"
    assert config.llm_max_tokens == DEFAULT_MAX_TOKENS
    assert config.llm_model == DEFAULT_MODEL


@pytest.mark.parametrize("value", ["zero", "1.5", "0", "-3", "1024k"])
def test_an_unusable_max_tokens_names_the_variable_and_the_value(value: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        resolve(POE2_RAG_LLM_MAX_TOKENS=value)

    assert "POE2_RAG_LLM_MAX_TOKENS" in str(raised.value)
    assert repr(value) in str(raised.value)


@pytest.mark.parametrize("field", ["embedder_model_id", "reranker_model_id", "llm_model"])
def test_a_blank_model_id_is_rejected_when_it_is_not_an_environment_default(field: str) -> None:
    """A blank override means "use the default"; a blank value built by hand is a bug."""
    assert resolve(POE2_RAG_LLM_MODEL="").llm_model != ""

    with pytest.raises(ConfigurationError) as raised:
        AppConfig(**values(**{field: " "}))  # type: ignore[arg-type]

    assert field in str(raised.value)


def test_direct_construction_resolves_relative_paths_against_the_project_root() -> None:
    config = AppConfig(
        **values(  # type: ignore[arg-type]
            raw_dir=Path("snapshots"),
            processed_dir=Path("corpus"),
            index_dir=Path("indexes"),
            patch_catalog_path=Path("config/patches.toml"),
        )
    )

    assert config.raw_dir == PROJECT_ROOT / "snapshots"
    assert config.processed_dir == PROJECT_ROOT / "corpus"
    assert config.index_dir == PROJECT_ROOT / "indexes"
    assert config.patch_catalog_path == PROJECT_ROOT / "config/patches.toml"


@pytest.mark.parametrize(
    ("field", "value"),
    [("llm_max_tokens", 0), ("llm_effort", ""), ("llm_effort", "medium-high")],
)
def test_direct_construction_validates_every_value_it_was_given(field: str, value: object) -> None:
    with pytest.raises(ConfigurationError) as raised:
        AppConfig(**values(**{field: value}))  # type: ignore[arg-type]

    assert field in str(raised.value)


def test_an_unknown_llm_effort_names_the_variable() -> None:
    """A typo in the depth must fail while the configuration is read, naming what was typed.

    The adapter would reject it later anyway, but by then the operator is three stages away from the
    variable they edited; validating here is what S1 asks for.
    """
    with pytest.raises(ConfigurationError) as raised:
        resolve(POE2_RAG_LLM_EFFORT="medium-high")

    assert "POE2_RAG_LLM_EFFORT" in str(raised.value)
    assert "medium-high" in str(raised.value)


def test_the_effort_set_matches_the_adapter_that_has_to_accept_it() -> None:
    """One closed set, two readers: the adapter owns it, this configuration must not drift from it."""
    from poe2_rag.answering.adapters.outbound.llm.claude_language_model import _EFFORTS

    assert LLM_EFFORTS == frozenset(_EFFORTS)


def test_the_configuration_is_frozen() -> None:
    config = resolve()

    with pytest.raises(FrozenInstanceError):
        config.llm_effort = "high"  # type: ignore[misc]


def test_the_api_key_is_never_read_into_the_configuration() -> None:
    """The key belongs to the SDK and to the composition root's presence check, nowhere else."""
    config = load_config(
        {"POE2_RAG_PROJECT_ROOT": str(PROJECT_ROOT), "ANTHROPIC_API_KEY": "sk-ant-secret"}
    )

    assert "sk-ant-secret" not in repr(config)
    assert "ANTHROPIC_API_KEY" not in repr(config)
    assert not any(
        isinstance(value, str) and value.startswith("sk-")
        for value in (getattr(config, field.name) for field in fields(config))
    )


def test_the_http_settings_default_to_no_contact_and_a_conservative_pace() -> None:
    """The contact has no committed default; the interval and timeout are safe values."""
    config = resolve()

    assert config.http_contact is None
    assert config.http_request_interval == DEFAULT_HTTP_REQUEST_INTERVAL
    assert config.http_timeout == DEFAULT_HTTP_TIMEOUT


def test_a_contact_override_is_kept() -> None:
    config = resolve(POE2_RAG_HTTP_CONTACT="ops@example.com")

    assert config.http_contact == "ops@example.com"


def test_a_blank_contact_means_unset() -> None:
    """The same rule as every other value: a copied `.env.example` leaves it empty, not blank."""
    assert resolve(POE2_RAG_HTTP_CONTACT="   ").http_contact is None


def test_a_blank_contact_built_by_hand_is_rejected() -> None:
    with pytest.raises(ConfigurationError) as raised:
        AppConfig(**values(http_contact=" "))  # type: ignore[arg-type]

    assert "http_contact" in str(raised.value)


@pytest.mark.parametrize("contact", ["line\nbreak", "paren)", "tab\there"])
def test_a_contact_that_would_break_the_user_agent_header_is_rejected(contact: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        AppConfig(**values(http_contact=contact))  # type: ignore[arg-type]

    assert "http_contact" in str(raised.value)


def test_the_http_seconds_can_be_overridden() -> None:
    config = resolve(
        POE2_RAG_HTTP_REQUEST_INTERVAL="2.5",
        POE2_RAG_HTTP_TIMEOUT="15",
    )

    assert config.http_request_interval == 2.5
    assert config.http_timeout == 15.0


@pytest.mark.parametrize("variable", ["POE2_RAG_HTTP_REQUEST_INTERVAL", "POE2_RAG_HTTP_TIMEOUT"])
@pytest.mark.parametrize("value", ["0", "-1", "abc", "inf", "nan"])
def test_an_unusable_http_seconds_value_names_the_variable(variable: str, value: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        resolve(**{variable: value})

    assert variable in str(raised.value)
    assert repr(value) in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("http_request_interval", 0),
        ("http_request_interval", float("inf")),
        ("http_timeout", -0.5),
        ("http_timeout", True),
    ],
)
def test_direct_construction_rejects_an_unusable_http_seconds_value(
    field: str, value: object
) -> None:
    with pytest.raises(ConfigurationError) as raised:
        AppConfig(**values(**{field: value}))  # type: ignore[arg-type]

    assert field in str(raised.value)
