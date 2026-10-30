"""Local BGE embedder: one adapter for both embedder ports, model loaded on first use (ADR-0004).

`BAAI/bge-base-en-v1.5` is a retrieval-oriented model of the `bge` family, so it was trained with an
instruction on the query side only. Embedding documents with the instruction and queries without it (or
the reverse) puts the two in different regions of the vector space, which is why the two halves live
behind two different ports: `Embedder.embed` is the query side and applies the instruction,
`DocumentEmbedder.embed_documents` is the corpus side and never does.

The model is a multi-hundred-megabyte download and a slow import, so nothing here touches it at
construction time: `model_id` and `dimension` are configuration, and the model loads on the first
`embed`, `embed_documents` or `overflowing_indices` call. That keeps the composition root cheap and lets
the default test suite run offline against a stub loader.

Batching is the adapter's concern, not the caller's: the batch size is a property of the model and the
device, and the corpus port receives the whole batch so the use case never has to know how the model
consumes it.

The model truncates its input silently, so `overflowing_indices` counts tokens with truncation off and
reports what the dense index cannot see. It does not raise: the lexical index still reads the whole
text, and losing the dense path for a long chunk would be worse than a reported blind spot.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, cast

from poe2_rag.shared.domain import is_blank

DEFAULT_MODEL_ID = "BAAI/bge-base-en-v1.5"
DEFAULT_DEVICE = "cpu"
DEFAULT_BATCH_SIZE = 32
DEFAULT_DIMENSION = 768
DEFAULT_MAX_SEQUENCE_LENGTH = 512
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
"""The English retrieval instruction the `bge` v1.5 family expects on queries (ADR-0004)."""


class EmbedderError(ValueError):
    """Base class for caller-provided invalid arguments to the embedder."""


class BlankTextError(EmbedderError):
    """Raised when a text to embed is blank or not a string."""


class EmbedderConfigurationError(EmbedderError):
    """Raised when the embedder is configured with an unusable value.

    Also covers a loaded model whose output width is not the configured `dimension`: that mismatch
    would store vectors the index cannot compare, so it fails before any vector is produced.
    """


class _Model(Protocol):
    """The slice of `SentenceTransformer` this adapter uses, so a stub can stand in for it."""

    tokenizer: Any
    max_seq_length: int

    def encode(
        self,
        sentences: list[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> Any:
        """Return one vector per sentence, in order."""
        ...

    def get_embedding_dimension(self) -> int | None:
        """Return the width of the vectors this model produces."""
        ...


def _load_model(model_id: str, device: str) -> _Model:
    """Build the real model; imported here so importing this module never imports torch.

    `SentenceTransformer` accepts every keyword `_Model.encode` declares, but its overloads are wider
    than the subset this adapter uses, so the structural match needs a cast.
    """
    from sentence_transformers import SentenceTransformer

    return cast(_Model, SentenceTransformer(model_id, device=device))


class BgeEmbedder:
    """Embed queries and documents with a local BGE model, applying the instruction to queries only."""

    __slots__ = (
        "_batch_size",
        "_device",
        "_dimension",
        "_max_sequence_length",
        "_model_id",
        "_model_instance",
        "_query_instruction",
    )

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        *,
        device: str = DEFAULT_DEVICE,
        batch_size: int = DEFAULT_BATCH_SIZE,
        dimension: int = DEFAULT_DIMENSION,
        query_instruction: str = QUERY_INSTRUCTION,
        max_sequence_length: int = DEFAULT_MAX_SEQUENCE_LENGTH,
    ) -> None:
        if is_blank(model_id):
            raise EmbedderConfigurationError(f"model_id must be a non-blank string, got {model_id!r}")
        if is_blank(device):
            raise EmbedderConfigurationError(f"device must be a non-blank string, got {device!r}")
        if not isinstance(query_instruction, str):
            raise EmbedderConfigurationError(
                f"query_instruction must be a string, got {query_instruction!r}"
            )
        for name, value in (
            ("batch_size", batch_size),
            ("dimension", dimension),
            ("max_sequence_length", max_sequence_length),
        ):
            if type(value) is not int or value <= 0:
                raise EmbedderConfigurationError(f"{name} must be a positive int, got {value!r}")
        self._model_id = model_id
        self._device = device
        self._batch_size = batch_size
        self._dimension = dimension
        self._query_instruction = query_instruction
        self._max_sequence_length = max_sequence_length
        self._model_instance: _Model | None = None

    @property
    def model_id(self) -> str:
        """The model that built (or will build) the index this embedder feeds."""
        return self._model_id

    @property
    def dimension(self) -> int:
        """The width of every vector this embedder returns."""
        return self._dimension

    def embed(self, text: str) -> tuple[float, ...]:
        """Return the normalized embedding of one query, with the model's retrieval instruction."""
        _require_text(text)
        return self._encode((f"{self._query_instruction}{text}",))[0]

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Return one normalized vector per corpus text, in the same order and without instruction."""
        materialized = _require_texts(texts)
        if not materialized:
            return ()
        return self._encode(materialized)

    def overflowing_indices(self, texts: Sequence[str]) -> tuple[int, ...]:
        """Return the positions of the texts whose token count exceeds the model's sequence limit."""
        materialized = _require_texts(texts)
        if not materialized:
            return ()
        encoded = self._model().tokenizer(
            list(materialized),
            truncation=False,
            add_special_tokens=True,
            padding=False,
        )["input_ids"]
        return tuple(
            index for index, token_ids in enumerate(encoded) if len(token_ids) > self._max_sequence_length
        )

    def _encode(self, sentences: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Run the model over `sentences` and return plain tuples of floats."""
        encoded = self._model().encode(
            list(sentences),
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return tuple(tuple(float(value) for value in row) for row in encoded)

    def _model(self) -> _Model:
        """Return the loaded model, loading it once and checking its width against the configuration."""
        if self._model_instance is None:
            model = _load_model(self._model_id, self._device)
            # The model truncates at its own window, so the configured limit has to reach it:
            # otherwise the overflow report compares against a number the model never used.
            model.max_seq_length = self._max_sequence_length
            produced = model.get_embedding_dimension()
            if produced is not None and int(produced) != self._dimension:
                raise EmbedderConfigurationError(
                    f"model {self._model_id!r} produces {produced}-dimensional vectors, but this "
                    f"embedder is configured for {self._dimension}; set the dimension the model "
                    "actually produces"
                )
            self._model_instance = model
        return self._model_instance


def _require_text(text: object) -> None:
    """Reject a blank or non-string text before the model is loaded."""
    if is_blank(text):
        raise BlankTextError(f"text must be a non-blank string, got {text!r}")


def _require_texts(texts: Sequence[str]) -> tuple[str, ...]:
    """Materialize a batch and validate every text, so a stray string is not read as characters."""
    if isinstance(texts, str):
        raise BlankTextError("texts must be a sequence of strings, not a string")
    materialized = tuple(texts)
    for text in materialized:
        _require_text(text)
    return materialized
