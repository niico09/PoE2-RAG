"""Deterministic tokenization shared by the lexical index's write and read paths (driver D2).

One tokenizer serves both sides on purpose: if indexing and querying disagreed about what a token is,
a term would be stored under one form and looked up under another, and the index would miss documents
it actually contains. Nothing here is learned, configured or locale dependent, so one text always
produces one tuple.

The rules:

- lower-case every token, so `Spark` and `spark` meet;
- split on whitespace and on punctuation, which carries no meaning in this corpus; a letter is a letter in
  any script, so an accent is never a separator (`naïve` stays one token, it does not become `na` + `ve`);
- keep numbers, decimals and signed or percent values whole (`"+10%"`, `"1.5"`, `"-20%"`): a damage
  value or a percentage is one fact, and splitting `+10%` into `+`, `10` and `%` loses it;
- keep a hyphenated or underscored game term whole *and* add its parts (`"area-of-effect"` becomes
  `area-of-effect`, `area`, `of`, `effect`), so a query written in either form finds the chunk;
- no stemming, no stopword removal and no synonym mapping in v1: `more`, `less`, `increased` and
  `reduced` are distinct mechanics (D2), so `increases` must not collapse into `increas`, and `more`
  must not be dropped as a stopword when it is the modifier the question is about.

Punctuation itself is discarded rather than emitted: no query should be able to match it.

`TOKENIZER_VERSION` identifies these rules. It is bumped whenever tokenization changes, because a
persisted index stores terms produced by one version and a query tokenized by another would silently match
the wrong thing or nothing at all; the index records the version it was built with and refuses to answer a
query tokenized under other rules.
"""

from __future__ import annotations

import re

TOKENIZER_VERSION = 1

# A number keeps its sign, its decimal part and its percent sign: those three carry the meaning.
_NUMBER = r"[+-]?\d+(?:\.\d+)?%?"
# A word character is any Unicode letter or digit: `[^\W_]` is `\w` without the underscore, so an accented
# letter joins its word instead of splitting it, while the underscore stays a compound separator below.
_LETTER_OR_DIGIT = r"[^\W_]"
# A word may join its pieces with `.`, `-` or `_`; the whole form is kept and split for its parts.
_WORD = rf"{_LETTER_OR_DIGIT}+(?:[._-]{_LETTER_OR_DIGIT}+)*"
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
