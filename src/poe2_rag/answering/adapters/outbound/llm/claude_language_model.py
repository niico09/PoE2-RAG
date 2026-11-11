"""Claude generation with native citations: one document per chunk, one citation per claim (§5.1 step 7).

The model must answer only from the retrieved context and name the chunk behind every claim, so the
request uses the API's own citation feature instead of asking nicely in the prompt: every delivered
chunk becomes a `document` block with `citations: {enabled: true}`, and the API returns which document
and which text block backs each part of the answer. The adapter only translates that answer into
`GeneratedAnswer`; `Answer.build` in `AnswerQuestion` is what validates the citations against the
delivered set (§5.1 step 8), so nothing here drops or repairs one.

Each chunk's text is split into one text block per non-empty line, because a text block is the minimal
citable unit: a line is exactly the granularity a citation needs, and `cited_text` then arrives as the
line itself. `context` carries the entity, the patch validity and the source URL — the model reads it,
the API never cites it.

Sonnet 5.5 accepts no sampling controls, no assistant prefill and no `thinking: disabled`, so the
adapter sends none of them and controls depth with `output_config.effort` instead (ADR-0005). It also
never combines citations with `output_config.format`: the API rejects both together, and citations
are the ones that sustain D3.

The `anthropic` client is injected, built in the composition root: this module never reads an
environment variable, never retries a request (the SDK already retries 408/409/429/5xx) and never logs
a request body or a key.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import anthropic

from poe2_rag.answering.adapters.outbound.llm.contract import (
    quotes_chunk,
    require_context,
    require_question,
)
from poe2_rag.answering.adapters.outbound.llm.errors import (
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
from poe2_rag.answering.application import NO_INFORMATION_TEXT, GeneratedAnswer
from poe2_rag.answering.domain import Citation, RetrievedChunk
from poe2_rag.shared.domain import Chunk, is_blank

if TYPE_CHECKING:
    from anthropic.types.beta import BetaMessage

__all__ = [
    "ClaudeLanguageModel",
    "LanguageModelAPIError",
    "LanguageModelAuthenticationError",
    "LanguageModelConfigurationError",
    "LanguageModelConnectionError",
    "LanguageModelError",
    "LanguageModelRateLimitError",
    "LanguageModelRefusedError",
    "LanguageModelRequestError",
    "LanguageModelResponseError",
    "LanguageModelRuntimeError",
    "LanguageModelTruncatedError",
]

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_EFFORT = "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
"""Beta header that enables the server-side fallback on Claude Sonnet 5.5 (ADR-0005, decision 4)."""

_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
_CITATION_TYPE = "content_block_location"

SYSTEM_PROMPT = (
    "Answer only from the documents provided in this request.\n"
    "Cite every claim: a citation must point to the chunk that backs it.\n"
    "If the documents do not contain the answer, reply exactly with this sentence and cite nothing: "
    f"{NO_INFORMATION_TEXT}\n"
    "Never use outside knowledge about Path of Exile 2, even when you already know it."
)
"""Frozen generation rules; the fixed sentence is the one `AnswerQuestion` returns when it delivers
nothing, so a bare "no information" answer reads the same on both paths."""


class ClaudeLanguageModel:
    """Answer from delivered chunks with Claude, mapping its native citations to domain citations."""

    __slots__ = ("_client", "_effort", "_max_tokens", "_model")

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str = DEFAULT_EFFORT,
    ) -> None:
        if getattr(client, "beta", None) is None:
            raise LanguageModelConfigurationError(
                "client must be an anthropic client with a beta resource, built by the composition root"
            )
        if is_blank(model):
            raise LanguageModelConfigurationError(f"model must be a non-blank string, got {model!r}")
        if type(max_tokens) is not int or max_tokens <= 0:
            raise LanguageModelConfigurationError(
                f"max_tokens must be a positive int, got {max_tokens!r}"
            )
        if effort not in _EFFORTS:
            raise LanguageModelConfigurationError(
                f"effort must be one of {sorted(_EFFORTS)}, got {effort!r}"
            )
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._effort = effort

    @property
    def model(self) -> str:
        """The model this adapter answers with."""
        return self._model

    def generate(self, question: str, context: Sequence[RetrievedChunk]) -> GeneratedAnswer:
        """Answer `question` from `context` only, returning the text and the citations it claims.

        Raises `LanguageModelRefusedError` when the model declines and
        `LanguageModelTruncatedError` when the budget ran out, both checked before the content is
        read: a refusal or a half sentence is never an answer. A citation that is not a content-block
        location, that names a document index outside `context`, or whose `cited_text` does not belong
        to the chunk that index names raises `LanguageModelResponseError`: an in-range index alone would
        let a citation lend one chunk's words to another. The quoted text itself is never rewritten or
        dropped, and `Answer.build` still decides whether every citation is backed.
        """
        require_question(question)
        delivered = require_context(context)
        request = self._request(question, delivered)
        response = self._send(request)
        return _generated_answer(response, delivered)

    def _request(self, question: str, delivered: tuple[RetrievedChunk, ...]) -> dict[str, Any]:
        """Build the request body: one citable document per chunk, then the question."""
        documents = [_document(retrieved) for retrieved in delivered]
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": [*documents, {"type": "text", "text": question}]}
            ],
            "output_config": {"effort": self._effort},
            "fallbacks": "default",
        }

    def _send(self, request: dict[str, Any]) -> BetaMessage:
        """Send the request through the beta client, mapping every SDK failure to its adapter error."""
        try:
            return self._client.beta.messages.create(
                betas=[FALLBACK_BETA],
                **request,
            )
        except anthropic.AuthenticationError as exc:
            raise LanguageModelAuthenticationError(
                "the Claude API rejected the credentials"
            ) from exc
        except anthropic.RateLimitError as exc:
            raise LanguageModelRateLimitError(
                "the Claude API rate-limited the request"
            ) from exc
        except anthropic.APIStatusError as exc:
            raise LanguageModelAPIError(
                f"the Claude API answered {exc.status_code}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LanguageModelConnectionError(
                "the request to the Claude API could not be completed"
            ) from exc
        except anthropic.APIResponseValidationError as exc:
            raise LanguageModelResponseError(
                "the Claude API returned a body the SDK could not validate"
            ) from exc
        except anthropic.APIError as exc:
            # `APIResponseValidationError` and the bare `APIError` are not `APIStatusError`, so
            # without this handler a raw SDK error would escape the adapter (L4).
            raise LanguageModelAPIError("the request to the Claude API failed") from exc
        except (TypeError, AttributeError) as exc:
            # The SDK iterates the body while parsing it, so a malformed `content` (a missing array,
            # say) raises before any block reaches this adapter. That is still a response the adapter
            # cannot use, and it must surface as its own error, never as a raw `TypeError`.
            raise LanguageModelResponseError(
                "the Claude API returned a body the SDK could not read"
            ) from exc


def _document(retrieved: RetrievedChunk) -> dict[str, Any]:
    """Build the citable document block of one delivered chunk."""
    chunk = retrieved.chunk
    return {
        "type": "document",
        "source": {
            "type": "content",
            "content": [{"type": "text", "text": line} for line in _lines(chunk)],
        },
        "title": chunk.chunk_id,
        "context": _document_context(chunk),
        "citations": {"enabled": True},
    }


def _lines(chunk: Chunk) -> tuple[str, ...]:
    """Split a chunk into one text block per non-blank line, keeping each line exactly as stored.

    Only whitespace-only lines are dropped: stripping a line would send text the chunk does not hold,
    and then `cited_text` would no longer be a substring of the chunk that names it.
    """
    return tuple(line for line in chunk.text.splitlines() if line.strip())


def _document_context(chunk: Chunk) -> str:
    """Describe the entity, its patch validity and its source: shown to the model, never cited."""
    validity = f"valid from patch {chunk.validity.valid_from}"
    if chunk.validity.valid_until is not None:
        validity = f"{validity} until patch {chunk.validity.valid_until}"
    return f"{chunk.entity.name}; {validity}; source: {chunk.source.url}"


def _generated_answer(
    response: BetaMessage, delivered: tuple[RetrievedChunk, ...]
) -> GeneratedAnswer:
    """Turn the response into an answer, in the order ADR-0005 requires."""
    if response.stop_reason == "refusal":
        raise LanguageModelRefusedError(
            "the model declined to answer",
            _refusal_category(response.stop_details),
        )
    if response.stop_reason == "max_tokens":
        raise LanguageModelTruncatedError("the answer hit max_tokens and is incomplete")
    texts: list[str] = []
    citations: list[Citation] = []
    for block in _content_blocks(response):
        block_type = _block_type(block)
        # Thinking and fallback blocks are part of the payload and never part of the answer.
        if block_type != "text":
            continue
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            raise LanguageModelResponseError("a text block carried no text")
        texts.append(text)
        for citation in _block_citations(block):
            citations.append(_citation(citation, delivered))
    text = "".join(texts)
    if is_blank(text):
        raise LanguageModelResponseError("the response carried no answer text")
    return GeneratedAnswer(text=text, citations=tuple(citations))


def _content_blocks(response: object) -> Sequence[Any]:
    """Return the response content as a sequence of blocks, rejecting any other shape.

    The SDK does not validate the body of a 200 response, so a gateway, a proxy or a future API change
    can hand back a string, a bare object or nothing at all. Reading it blindly would leak an
    `AttributeError` or a `TypeError` from an adapter that promises its own errors (L4).
    """
    content = getattr(response, "content", None)
    if isinstance(content, (str, bytes)) or not isinstance(content, Sequence):
        raise LanguageModelResponseError(
            f"the response content must be a sequence of blocks, got {type(content).__name__}"
        )
    return content


def _block_type(block: object) -> str:
    """Return a block's type, rejecting a block that is not one.

    The SDK leaves a non-object entry of the `content` array untouched (an int, a string), so a block
    with no `type` is a malformed response and not a block the adapter can skip.
    """
    block_type = getattr(block, "type", None)
    if not isinstance(block_type, str) or is_blank(block_type):
        raise LanguageModelResponseError(
            f"the response carried a content block without a type: {type(block).__name__}"
        )
    return block_type


def _block_citations(block: object) -> Sequence[Any]:
    """Return a text block's citations, rejecting anything that is not a sequence."""
    citations = getattr(block, "citations", None)
    if citations is None:
        return ()
    if isinstance(citations, (str, bytes)) or not isinstance(citations, Sequence):
        raise LanguageModelResponseError("a text block carried citations that are not a sequence")
    return citations


def _refusal_category(stop_details: object) -> str | None:
    """Return the refusal category when the API reported one."""
    category = getattr(stop_details, "category", None)
    return category if isinstance(category, str) else None


def _citation(citation: object, delivered: tuple[RetrievedChunk, ...]) -> Citation:
    """Map one API citation to the chunk it names, rejecting every shape the adapter cannot map."""
    citation_type = getattr(citation, "type", None)
    if citation_type != _CITATION_TYPE:
        raise LanguageModelResponseError(
            f"unsupported citation type {citation_type!r}; only {_CITATION_TYPE!r} is citable here"
        )
    index = getattr(citation, "document_index", None)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(delivered):
        raise LanguageModelResponseError(
            f"citation document_index {index!r} is outside the {len(delivered)} delivered documents"
        )
    quoted_text = getattr(citation, "cited_text", None)
    if not isinstance(quoted_text, str) or is_blank(quoted_text):
        raise LanguageModelResponseError("a citation must quote a non-blank cited_text")
    chunk = delivered[index].chunk
    if not quotes_chunk(quoted_text, chunk.text):
        raise LanguageModelResponseError(
            f"citation {index} quotes text that is not part of the chunk it names"
        )
    return Citation(chunk.chunk_id, quoted_text)
