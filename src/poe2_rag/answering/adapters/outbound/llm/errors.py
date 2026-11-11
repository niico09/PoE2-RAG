"""Shared error hierarchy for every language-model adapter (S1, S12).

Keeping the exceptions in one module lets new adapters reuse the same cross-adapter
contract without importing a sibling implementation. The Claude adapter re-exports these
names so existing imports keep working.
"""

from __future__ import annotations


class LanguageModelError(ValueError):
    """Base class for caller-provided invalid arguments to the language model adapter.

    Upstream failures do not derive from this class, exactly like `RerankerError` and
    `RerankScoringError`: a caller that captures `ValueError` for a bad argument gets the
    configuration and request errors, and never a refusal or an API failure dressed as one.
    """


class LanguageModelConfigurationError(LanguageModelError):
    """Raised when the adapter is configured with an unusable client, model, budget or effort."""


class LanguageModelRequestError(LanguageModelError):
    """Raised when a question or a context is unusable, before any request is sent."""


class LanguageModelRuntimeError(RuntimeError):
    """Base class for every upstream failure this adapter owns: refusal, truncation, response, API."""


class LanguageModelRefusedError(LanguageModelRuntimeError):
    """Raised when the model declined the request (`stop_reason: "refusal"`)."""

    def __init__(self, message: str, stop_details_category: str | None = None) -> None:
        super().__init__(message)
        self.stop_details_category = stop_details_category


class LanguageModelTruncatedError(LanguageModelRuntimeError):
    """Raised when the answer hit `max_tokens` (`stop_reason: "max_tokens"`) and is incomplete."""


class LanguageModelResponseError(LanguageModelRuntimeError):
    """Raised when a response cannot be turned into an answer with mappable citations."""


class LanguageModelAuthenticationError(LanguageModelRuntimeError):
    """Raised when the API rejects the credentials."""


class LanguageModelRateLimitError(LanguageModelRuntimeError):
    """Raised when the API rate-limits the request, after the SDK's own retries."""


class LanguageModelAPIError(LanguageModelRuntimeError):
    """Raised when the API answers with a status error that is neither authentication nor rate limit."""


class LanguageModelConnectionError(LanguageModelRuntimeError):
    """Raised when the request never reached the API, or the connection died on the way."""


class LanguageModelTimeoutError(LanguageModelRuntimeError):
    """Raised when a subprocess-based model does not finish before the configured timeout."""


__all__ = [
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
    "LanguageModelTimeoutError",
    "LanguageModelTruncatedError",
]
