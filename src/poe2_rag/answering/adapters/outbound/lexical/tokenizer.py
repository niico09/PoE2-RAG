"""Deterministic tokenization shared by the lexical index's write and read paths (driver D2).

One tokenizer serves both sides on purpose: if indexing and querying disagreed about what a token is,
a term would be stored under one form and looked up under another, and the index would miss documents
it actually contains. Nothing here is learned, configured or locale dependent, so one text always
produces one tuple.

The rules:

- lower-case every token, so `Spark` and `spark` meet;
- split on whitespace and on punctuation, which carries no meaning in this corpus;
- keep numbers, decimals and signed or percent values whole (`"+10%"`, `"1.5"`, `"-20%"`): a damage
  value or a percentage is one fact, and splitting `+10%` into `+`, `10` and `%` loses it;
- keep a hyphenated or underscored game term whole *and* add its parts (`"area-of-effect"` becomes
  `area-of-effect`, `area`, `of`, `effect`), so a query written in either form finds the chunk;
- no stemming, no stopword removal and no synonym mapping in v1: `more`, `less`, `increased` and
  `reduced` are distinct mechanics (D2), so `increases` must not collapse into `increas`, and `more`
  must not be dropped as a stopword when it is the modifier the question is about.

Punctuation itself is discarded rather than emitted: no query should be able to match it.
"""

from __future__ import annotations

import re

# A number keeps its sign, its decimal part and its percent sign: those three carry the meaning.
_NUMBER = r"[+-]?\d+(?:\.\d+)?%?"
# A word may join its pieces with `.`, `-` or `_`; the whole form is kept and split for its parts.
_WORD = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
_TOKEN = re.compile(rf"{_NUMBER}|{_WORD}")
_COMPOUND_SEPARATORS = re.compile(r"[-_]+")


def tokenize(text: str) -> tuple[str, ...]:
    """Return the tokens of `text`, in reading order, with repeats preserved.

    Repeats are kept because the index derives term frequency from them. A compound game term
    contributes its whole form first and then its parts, in the order the parts appear; a number is
    never split, because its sign, its decimal point and its percent sign are part of the value.
    Text with nothing to index returns an empty tuple.
    """
    tokens: list[str] = []
    for match in _TOKEN.finditer(text.lower()):
        token = match.group()
        tokens.append(token)
        parts = _COMPOUND_SEPARATORS.split(token)
        # A signed number starts with `-`, which the compound split also produces as an empty first
        # part; requiring every part to be non-empty keeps `-20%` whole while `area-of-effect`
        # still contributes `area`, `of` and `effect`.
        if len(parts) > 1 and all(parts):
            tokens.extend(parts)
    return tuple(tokens)
