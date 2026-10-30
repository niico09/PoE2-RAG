"""The Claude adapter over the real SDK, with only the HTTP layer replaced (§5.1 steps 7-8, ADR-0005).

The `anthropic` client is real and so is its request serialization: the only fake is
`httpx2.MockTransport`, which returns recorded-shape JSON, so these cases pin the bytes the adapter
actually sends (documents, fallbacks, beta header, absence of sampling controls) and the values it
actually returns, without a network call, an API key or a token of spend.

The suite also pins what the adapter promises about a body it cannot use: the SDK does not validate a 200
response, so a malformed `content` (a missing array, a string, a block without a type, a text that is not
a string) must come back as `LanguageModelResponseError`, and a citation must quote the chunk its
`document_index` names, not merely point at an in-range index.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import anthropic
import httpx2
import pytest

from poe2_rag.answering.adapters.outbound.llm.claude_language_model import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    FALLBACK_BETA,
    SYSTEM_PROMPT,
    ClaudeLanguageModel,
    LanguageModelAPIError,
    LanguageModelAuthenticationError,
    LanguageModelConfigurationError,
    LanguageModelConnectionError,
    LanguageModelError,
    LanguageModelRateLimitError,
    LanguageModelRefusedError,
    LanguageModelRequestError,
    LanguageModelResponseError,
    LanguageModelRuntimeError,
    LanguageModelTruncatedError,
)
from poe2_rag.answering.application import (
    NO_INFORMATION_TEXT,
    GeneratedAnswer,
    LanguageModel,
)
from poe2_rag.answering.domain import Citation, RetrievedChunk, StageScores
from poe2_rag.shared.domain import Chunk, EntityRef, PatchVersion, SourceRef, Validity

THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
CAPTURED_AT = date(2026, 10, 29)
TARGET = PatchVersion.parse("0.2.0")
QUESTION = "how much damage does spark deal"
SCORES = StageScores(lexical=0.5, dense=0.25, fused=0.125, rerank=0.75)


@dataclass(frozen=True)
class Recorded:
    """One request the SDK serialized, kept as the bytes the server would have received."""

    body: dict[str, Any]
    headers: dict[str, str]


class Gateway:
    """`httpx2.MockTransport` handler: records every request and replays queued responses."""

    def __init__(self, *responses: httpx2.Response | BaseException) -> None:
        self._responses = list(responses)
        self.requests: list[Recorded] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(Recorded(json_body(request), dict(request.headers)))
        if not self._responses:
            raise AssertionError(f"the adapter sent an unexpected request to {request.url}")
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    @property
    def body(self) -> dict[str, Any]:
        """The body of the single request the adapter made."""
        assert len(self.requests) == 1, f"expected one request, got {len(self.requests)}"
        return self.requests[0].body

    @property
    def headers(self) -> dict[str, str]:
        """The headers of the single request the adapter made."""
        return self.requests[0].headers


def json_body(request: httpx2.Request) -> dict[str, Any]:
    """Decode the request body the SDK serialized."""
    import json

    return json.loads(request.content)


def make_client(gateway: Gateway) -> anthropic.Anthropic:
    """Build a real client with retries off, so each case makes exactly the requests it queued."""
    return anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(gateway)),
    )


def make_chunk(
    section: str,
    text: str,
    *,
    valid_until: PatchVersion | None = None,
) -> Chunk:
    """Build one chunk with a readable section, so `chunk_id` ends in it."""
    return Chunk(
        entity=EntityRef("patch_notes", "0-2-0", "Patch 0.2.0"),
        validity=Validity(TARGET, valid_until),
        section=section,
        text=text,
        source=SourceRef("patch_notes", THREAD_URL, CAPTURED_AT),
    )


def delivered(chunk: Chunk) -> RetrievedChunk:
    """Pair a chunk with the scores retrieval gave it."""
    return RetrievedChunk(chunk, SCORES)


SPARK = delivered(make_chunk("spark", "Spark deals 10 damage.\n\n  Chains to 2 enemies.  \n"))
CASCADE = delivered(make_chunk("cascade", "Glacial Cascade freezes enemies."))
CONTEXT = (SPARK, CASCADE)


def text_block(text: str, *citations: dict[str, Any]) -> dict[str, Any]:
    """Build one `text` content block, with citations when it has any."""
    block: dict[str, Any] = {"type": "text", "text": text}
    if citations:
        block["citations"] = list(citations)
    return block


def location_citation(
    cited_text: str, document_index: int, *, title: str | None = None
) -> dict[str, Any]:
    """Build one `content_block_location` citation, the only type the adapter accepts."""
    return {
        "type": "content_block_location",
        "cited_text": cited_text,
        "document_index": document_index,
        "document_title": title,
        "start_block_index": 0,
        "end_block_index": 1,
    }


def message(
    *blocks: dict[str, Any],
    stop_reason: str = "end_turn",
    stop_details: dict[str, Any] | None = None,
) -> httpx2.Response:
    """Build the JSON body of a Messages response."""
    return httpx2.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": DEFAULT_MODEL,
            "content": list(blocks),
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "stop_details": stop_details,
            "usage": {"input_tokens": 12, "output_tokens": 8},
        },
    )


def raw_response(**overrides: Any) -> httpx2.Response:
    """Build a 200 response whose body can be malformed in exactly one place."""
    body: dict[str, Any] = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL,
        "content": [],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 8},
    }
    body.update(overrides)
    return httpx2.Response(200, json=body)


def error_response(status: int, error_type: str) -> httpx2.Response:
    """Build the JSON body the API returns for a failed request."""
    return httpx2.Response(
        status,
        json={"type": "error", "error": {"type": error_type, "message": "upstream said no"}},
    )


def generate(
    gateway: Gateway, context: Sequence[RetrievedChunk] = CONTEXT, *, question: str = QUESTION
) -> GeneratedAnswer:
    """Run one `generate` through a real client wired to `gateway`."""
    model = ClaudeLanguageModel(make_client(gateway))
    return model.generate(question, context)


def documents_of(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the document blocks of the single user message."""
    content = body["messages"][0]["content"]
    return [block for block in content if block["type"] == "document"]


# --------------------------------------------------------------------------- request shape


def test_the_adapter_matches_the_port() -> None:
    assert isinstance(ClaudeLanguageModel(make_client(Gateway())), LanguageModel)


def test_the_request_carries_one_document_per_chunk_in_context_order() -> None:
    gateway = Gateway(message(text_block("ok")))

    generate(gateway)

    documents = documents_of(gateway.body)
    assert [document["title"] for document in documents] == [
        SPARK.chunk.chunk_id,
        CASCADE.chunk.chunk_id,
    ]
    assert all(document["type"] == "document" for document in documents)
    assert all(document["source"]["type"] == "content" for document in documents)


def test_each_chunk_line_becomes_its_own_citable_text_block() -> None:
    gateway = Gateway(message(text_block("ok")))

    generate(gateway)

    spark_document, cascade_document = documents_of(gateway.body)
    # Blank lines are dropped; the text a chunk does hold is sent exactly as stored, indentation
    # included, so `cited_text` stays a substring of the chunk (ADR-0005, §5.1 step 6).
    assert spark_document["source"]["content"] == [
        {"type": "text", "text": "Spark deals 10 damage."},
        {"type": "text", "text": "  Chains to 2 enemies.  "},
    ]
    assert cascade_document["source"]["content"] == [
        {"type": "text", "text": "Glacial Cascade freezes enemies."}
    ]


def test_every_document_enables_citations() -> None:
    gateway = Gateway(message(text_block("ok")))

    generate(gateway)

    assert [document["citations"] for document in documents_of(gateway.body)] == [
        {"enabled": True},
        {"enabled": True},
    ]


def test_the_document_context_names_entity_validity_and_source_url() -> None:
    closed = delivered(
        make_chunk("closed", "Old text.", valid_until=PatchVersion.parse("0.3.0"))
    )
    gateway = Gateway(message(text_block("ok")))

    generate(gateway, (SPARK, closed))

    spark_context, closed_context = [document["context"] for document in documents_of(gateway.body)]
    assert spark_context == f"Patch 0.2.0; valid from patch 0.2.0; source: {THREAD_URL}"
    assert closed_context == (
        f"Patch 0.2.0; valid from patch 0.2.0 until patch 0.3.0; source: {THREAD_URL}"
    )


def test_the_question_is_the_last_block_of_the_request() -> None:
    gateway = Gateway(message(text_block("ok")))

    generate(gateway)

    content = gateway.body["messages"][0]["content"]
    assert content[-1] == {"type": "text", "text": QUESTION}
    assert gateway.body["messages"][0]["role"] == "user"


def test_the_request_asks_for_server_side_fallbacks_with_their_beta_header() -> None:
    gateway = Gateway(message(text_block("ok")))

    generate(gateway)

    assert gateway.body["fallbacks"] == "default"
    assert FALLBACK_BETA == "server-side-fallback-2026-07-01"
    assert FALLBACK_BETA in gateway.headers["anthropic-beta"]


def test_the_request_selects_the_model_its_budget_and_its_effort() -> None:
    gateway = Gateway(message(text_block("ok")))
    model = ClaudeLanguageModel(
        make_client(gateway), model="claude-sonnet-5-5-test", max_tokens=777, effort="high"
    )

    model.generate(QUESTION, CONTEXT)

    assert gateway.body["model"] == "claude-sonnet-5-5-test"
    assert gateway.body["max_tokens"] == 777
    assert gateway.body["output_config"] == {"effort": "high"}


def test_defaults_are_the_documented_ones() -> None:
    model = ClaudeLanguageModel(make_client(Gateway()))

    assert DEFAULT_MODEL == "claude-sonnet-5-5"
    assert (DEFAULT_MAX_TOKENS, DEFAULT_EFFORT) == (1024, "medium")
    assert model.model == DEFAULT_MODEL


def test_the_request_never_sends_sampling_controls_thinking_or_structured_output() -> None:
    gateway = Gateway(message(text_block("ok")))

    generate(gateway)

    for forbidden in ("temperature", "top_p", "top_k", "thinking"):
        assert forbidden not in gateway.body
    # Citations and structured outputs are mutually exclusive (ADR-0005, decision 3).
    assert "format" not in gateway.body["output_config"]
    assert gateway.body["system"] == SYSTEM_PROMPT


def test_the_system_prompt_is_frozen_and_states_the_generation_rules() -> None:
    assert SYSTEM_PROMPT.startswith("Answer only from the documents provided in this request.")
    assert "Cite every claim" in SYSTEM_PROMPT
    assert NO_INFORMATION_TEXT in SYSTEM_PROMPT
    assert "Never use outside knowledge about Path of Exile 2" in SYSTEM_PROMPT


# --------------------------------------------------------------------------- response mapping


def test_a_cited_answer_maps_document_index_to_chunk_id() -> None:
    gateway = Gateway(
        message(
            text_block("Spark deals "),
            text_block(
                "10 damage.",
                location_citation("Spark deals 10 damage.", 0, title=SPARK.chunk.chunk_id),
            ),
            text_block(
                "It chains.",
                location_citation("Chains to 2 enemies.", 0, title=SPARK.chunk.chunk_id),
                location_citation("Glacial Cascade freezes enemies.", 1),
            ),
        )
    )

    generated = generate(gateway)

    assert generated.citations == (
        Citation(SPARK.chunk.chunk_id, "Spark deals 10 damage."),
        Citation(SPARK.chunk.chunk_id, "Chains to 2 enemies."),
        Citation(CASCADE.chunk.chunk_id, "Glacial Cascade freezes enemies."),
    )


def test_multiple_text_blocks_are_concatenated_in_order() -> None:
    gateway = Gateway(
        message(
            text_block("Spark deals "),
            text_block("10 damage ", location_citation("Spark deals 10 damage.", 0)),
            text_block("and chains."),
        )
    )

    generated = generate(gateway)

    assert generated.text == "Spark deals 10 damage and chains."


def test_thinking_blocks_are_ignored_when_assembling_the_answer() -> None:
    gateway = Gateway(
        message(
            {"type": "thinking", "thinking": "the documents say 10", "signature": "sig"},
            text_block("Spark deals 10 damage.", location_citation("Spark deals 10 damage.", 0)),
            {"type": "redacted_thinking", "data": "opaque"},
        )
    )

    generated = generate(gateway)

    assert generated.text == "Spark deals 10 damage."
    assert generated.citations == (Citation(SPARK.chunk.chunk_id, "Spark deals 10 damage."),)


def test_fallback_blocks_are_ignored_when_assembling_the_answer() -> None:
    gateway = Gateway(
        message(
            {"type": "thinking", "thinking": "declining", "signature": "sig"},
            {
                "type": "fallback",
                "from": {"model": "claude-sonnet-5-5"},
                "to": {"model": "claude-opus-5-5"},
            },
            text_block("Spark deals 10 damage.", location_citation("Spark deals 10 damage.", 0)),
        )
    )

    generated = generate(gateway)

    assert generated.text == "Spark deals 10 damage."
    assert generated.citations == (Citation(SPARK.chunk.chunk_id, "Spark deals 10 damage."),)


def test_a_citation_that_quotes_nothing_raises() -> None:
    gateway = Gateway(message(text_block("Spark deals 10 damage.", location_citation("   ", 0))))

    with pytest.raises(LanguageModelResponseError):
        generate(gateway)


def test_an_answer_without_citations_comes_back_unsupported() -> None:
    gateway = Gateway(message(text_block(NO_INFORMATION_TEXT)))

    generated = generate(gateway)

    assert generated.text == NO_INFORMATION_TEXT
    assert generated.citations == ()
    assert GeneratedAnswer(generated.text, generated.citations) == generated


def test_a_refusal_raises_with_the_stop_details_category() -> None:
    gateway = Gateway(
        message(
            text_block("I can't help with that."),
            stop_reason="refusal",
            stop_details={"type": "refusal", "category": "cyber"},
        )
    )

    with pytest.raises(LanguageModelRefusedError) as raised:
        generate(gateway)

    assert raised.value.stop_details_category == "cyber"


def test_a_refusal_without_stop_details_still_raises() -> None:
    gateway = Gateway(message(stop_reason="refusal"))

    with pytest.raises(LanguageModelRefusedError) as raised:
        generate(gateway)

    assert raised.value.stop_details_category is None


def test_a_truncated_answer_raises_instead_of_returning_half_a_sentence() -> None:
    gateway = Gateway(
        message(
            text_block("Spark deals 10 "),
            stop_reason="max_tokens",
        )
    )

    with pytest.raises(LanguageModelTruncatedError):
        generate(gateway)


def test_an_unknown_citation_type_raises() -> None:
    gateway = Gateway(
        message(
            text_block(
                "Spark deals 10 damage.",
                {
                    "type": "char_location",
                    "cited_text": "Spark",
                    "document_index": 0,
                    "document_title": SPARK.chunk.chunk_id,
                    "start_char_index": 0,
                    "end_char_index": 5,
                },
            )
        )
    )

    with pytest.raises(LanguageModelResponseError):
        generate(gateway)


@pytest.mark.parametrize("document_index", [2, 7, -1])
def test_a_citation_outside_the_delivered_documents_raises(document_index: int) -> None:
    gateway = Gateway(
        message(text_block("Spark deals 10 damage.", location_citation("Spark", document_index)))
    )

    with pytest.raises(LanguageModelResponseError):
        generate(gateway)


def test_a_citation_whose_text_belongs_to_another_chunk_raises() -> None:
    """An in-range index is not enough: the quoted text must belong to the chunk it names."""
    gateway = Gateway(
        message(
            text_block(
                "Cold damage.",
                location_citation("Glacial Cascade freezes enemies.", 0, title=SPARK.chunk.chunk_id),
            )
        )
    )

    with pytest.raises(LanguageModelResponseError):
        generate(gateway)


def test_a_citation_spanning_several_lines_of_the_same_chunk_is_accepted() -> None:
    gateway = Gateway(
        message(
            text_block(
                "Both lines.",
                # The API joins the cited block range; the adapter compares ignoring whitespace,
                # because the separators between blocks are the API's, not the chunk's.
                location_citation("Spark deals 10 damage.\nChains to 2 enemies.", 0),
            )
        )
    )

    generated = generate(gateway)

    assert generated.citations == (Citation(SPARK.chunk.chunk_id, "Spark deals 10 damage.\nChains to 2 enemies."),)


def test_a_response_without_any_text_raises() -> None:
    gateway = Gateway(
        message({"type": "thinking", "thinking": "nothing to say", "signature": "sig"})
    )

    with pytest.raises(LanguageModelResponseError):
        generate(gateway)


@pytest.mark.parametrize(
    "content",
    [
        "oops",
        None,
        [{"type": "text", "text": "ok"}, 42],
        [{"type": "text", "text": ["not", "a", "string"]}],
        [42],
    ],
    ids=["string", "missing", "int-block", "list-text", "sole-int"],
)
def test_a_malformed_content_shape_raises_a_response_error(content: Any) -> None:
    gateway = Gateway(raw_response(content=content))

    with pytest.raises(LanguageModelResponseError):
        generate(gateway)


# --------------------------------------------------------------------------- SDK error mapping


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (error_response(401, "authentication_error"), LanguageModelAuthenticationError),
        (error_response(429, "rate_limit_error"), LanguageModelRateLimitError),
        (error_response(400, "invalid_request_error"), LanguageModelAPIError),
        (error_response(500, "api_error"), LanguageModelAPIError),
    ],
)
def test_sdk_status_errors_map_to_specific_adapter_errors(
    response: httpx2.Response, expected: type[LanguageModelError]
) -> None:
    gateway = Gateway(response)

    with pytest.raises(expected) as raised:
        generate(gateway)

    assert isinstance(raised.value, LanguageModelRuntimeError)
    assert isinstance(raised.value.__cause__, anthropic.APIStatusError)


def test_a_connection_error_maps_to_its_own_adapter_error() -> None:
    gateway = Gateway(httpx2.ConnectError("connection refused"))

    with pytest.raises(LanguageModelConnectionError) as raised:
        generate(gateway)

    assert isinstance(raised.value.__cause__, anthropic.APIConnectionError)


def test_the_adapter_never_retries_a_failed_request() -> None:
    gateway = Gateway(error_response(500, "api_error"))

    with pytest.raises(LanguageModelAPIError):
        generate(gateway)

    assert len(gateway.requests) == 1


# --------------------------------------------------------------------------- rejected calls


@pytest.mark.parametrize("question", ["", "   ", None])
def test_a_blank_question_is_rejected_before_any_request(question: object) -> None:
    gateway = Gateway(message(text_block("ok")))

    with pytest.raises(LanguageModelRequestError):
        generate(gateway, question=question)  # type: ignore[arg-type]
    assert gateway.requests == []


def test_an_empty_context_is_rejected_before_any_request() -> None:
    gateway = Gateway(message(text_block("ok")))

    with pytest.raises(LanguageModelRequestError):
        generate(gateway, ())
    assert gateway.requests == []


def test_context_items_must_be_retrieved_chunks() -> None:
    gateway = Gateway(message(text_block("ok")))

    with pytest.raises(LanguageModelRequestError):
        ClaudeLanguageModel(make_client(gateway)).generate(QUESTION, ("cascade",))  # type: ignore[arg-type]
    assert gateway.requests == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": ""},
        {"model": "   "},
        {"max_tokens": 0},
        {"max_tokens": -1},
        {"max_tokens": 1.5},
        {"effort": ""},
        {"effort": "extreme"},
    ],
)
def test_invalid_configuration_raises(kwargs: dict[str, Any]) -> None:
    with pytest.raises(LanguageModelConfigurationError):
        ClaudeLanguageModel(make_client(Gateway()), **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "error",
    [
        LanguageModelConfigurationError,
        LanguageModelRequestError,
    ],
)
def test_caller_errors_are_value_errors(error: type[Exception]) -> None:
    assert issubclass(error, ValueError)


@pytest.mark.parametrize(
    "error",
    [
        LanguageModelRefusedError,
        LanguageModelTruncatedError,
        LanguageModelResponseError,
        LanguageModelAuthenticationError,
        LanguageModelRateLimitError,
        LanguageModelAPIError,
        LanguageModelConnectionError,
    ],
)
def test_upstream_errors_are_runtime_errors(error: type[Exception]) -> None:
    assert issubclass(error, RuntimeError)


def test_the_client_is_required_and_never_read_from_the_environment() -> None:
    with pytest.raises(LanguageModelConfigurationError):
        ClaudeLanguageModel(None)  # type: ignore[arg-type]
