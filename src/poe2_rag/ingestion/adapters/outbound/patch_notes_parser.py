"""Official Path of Exile 2 forum patch-notes parser.

One thread becomes one `patch_notes` entity and one chunk per top-level section, valid from the
detected patch onwards (S8). The page shape is the forum's, not a rendered article: the thread title
is the first `h1`, or `og:title`/`<title>` on the Content Update threads that have no `h1` at all, and
the notes are the `.content` body of the **first** staff post (`tr.staff`, or `tr.newsPost` on the
news-post layout); later staff posts are conversation (a reply by a community manager is a staff post
too) and are ignored. A saved snapshot loses its URL, so the parser recovers the thread URL from the
thread's own `/forum/post-reply/<id>` links; an absolute `locator` wins when there is one.

Sections with the same slug are merged in document order, so a repeated heading, two headings that
slugify alike, or a preamble next to an `Overview` heading never produce two chunks with the same
`chunk_id`. Empty or decorative headings are skipped instead of aborting the whole document. Hotfix
threads have no section headings, so their body becomes one `notes` chunk. Headings deeper than the
shallowest one stay inside their section as text lines, which is what "one chunk per top-level
section" means here. A section wider than the token budget becomes consecutive numbered blocks that
each repeat a context line, because the embedder and the reranker read one block at a time (S3).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from datetime import date
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import NavigableString, PageElement, Tag

from poe2_rag.ingestion.application import RawDocument
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    InvalidPatchVersionError,
    PatchVersion,
    SourceRef,
    Validity,
    is_blank,
)

SOURCE_TYPE = "patch_notes"
"""The source type this parser handles.

The one literal for the kind: the composition root hands it to the forum policy and to the local
adapter, so a policy can never declare a source type the parser refuses. `EntityRef("patch_notes", ...)`
below is the entity *kind*, a different concept that only coincides in text.
"""

DEFAULT_MAX_BLOCK_TOKENS = 400
"""Tokens kept per notes block by default, before splitting.

`bge-base-en-v1.5` and `bge-reranker-base` read 512 tokens, and `estimate_tokens` sits above the real
count on these notes, so 400 leaves more than the 64-token margin the poe2db blocks keep while still
filling most of the window. The numbers are measured by the integrated tokenizer test, not assumed
here.
"""

# The parser must stay offline, so it cannot load the embedder's tokenizer: it bounds the count from
# above instead, calibrated on the 2026-11-06 captures. The widest real block (418 tokens, 1,550 chars,
# 263 words) measures 3.71 chars per token, and the shortest one (42 tokens, 126 chars, 23 words)
# measures 1.83 tokens per word, so 3.6 characters per token and 1.9 tokens per word both stay above
# what the real tokenizer produces; taking the larger of the two covers prose, bullets and the short
# blocks of numbers the forum also emits.
_CHARS_PER_TOKEN = 3.6
_TOKENS_PER_WORD = 1.9

# A version token must end at a word boundary: without the lookahead, `0.5.5hotfix` would match
# `0.5.5h` and a glued word would be read as a hotfix, which is exactly the silent mapping the
# parser must not do. The trailing `[A-Za-z]*` keeps the whole glued run as one candidate, so the
# error can name it instead of reporting a prefix.
_VERSION_TOKEN = re.compile(r"\d+(?:\.\d+)*[A-Za-z]*(?![A-Za-z0-9])")
_HEADING_LEVELS = {"h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_HEADING_TAGS = frozenset(_HEADING_LEVELS)
_NON_CONTENT_TAGS = frozenset({"noscript", "script", "style"})
_NAVIGATION_SLUGS = frozenset({"table-of-contents", "report-forum-post"})
"""Sections the forum uses to navigate the page, so they never become notes chunks."""
_LAYOUT_WRAPPER_CLASSES = frozenset({"box-content", "lbox", "lbox-container"})
"""The forum's layout boxes, `div.lbox-container > div.lbox > div.box-content`, one per section."""
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "dd",
        "details",
        "dialog",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hgroup",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)


class PatchNotesParseError(RuntimeError):
    """Raised when a patch-notes document cannot be parsed."""


class InvalidTokenBudgetError(ValueError):
    """Raised when the parser is built with a block budget below one token.

    A configuration error, not a document error: the composition root builds the parser, so a bad
    budget must reach the operator as a bug instead of as `PatchNotesParseError`, which
    `bootstrap.EXPECTED_ERRORS` reports as a document that could not be parsed.
    """


class UnsupportedSourceTypeError(PatchNotesParseError):
    """Raised when the document was fetched from a source this parser does not handle."""


class PatchVersionNotFoundError(PatchNotesParseError):
    """Raised when the thread title contains no version token."""


class PatchVersionUnparseableError(PatchNotesParseError):
    """Raised when no version token in the title is a known patch format."""


class MissingPatchNotesBodyError(PatchNotesParseError):
    """Raised when the thread has no staff post body."""


class ThreadUrlNotFoundError(PatchNotesParseError):
    """Raised when the thread URL cannot be recovered from the document."""


def estimate_tokens(text: str) -> int:
    """Return a token count for `text` that stays above `bge-base-en-v1.5` on these notes.

    The parser cannot load the embedder's tokenizer without making the offline suite depend on
    `transformers` and its weights, so it estimates the count instead, calibrated on the measured
    ratios of the widest and of the shortest block of the 2026-11-06 captures. The integrated budget
    test measures the same texts with the real tokenizer and pins the margin.
    """
    characters = math.ceil(len(text) / _CHARS_PER_TOKEN)
    words = math.ceil(len(text.split()) * _TOKENS_PER_WORD)
    return max(characters, words)


class PatchNotesParser:
    """Turn an official forum patch-notes thread into domain chunks."""

    def __init__(self, max_block_tokens: int = DEFAULT_MAX_BLOCK_TOKENS) -> None:
        if max_block_tokens < 1:
            raise InvalidTokenBudgetError(
                f"max_block_tokens must be at least 1, got {max_block_tokens}"
            )
        self._max_block_tokens = max_block_tokens

    def parse(self, raw: RawDocument) -> tuple[Chunk, ...]:
        """Parse `raw` into chunks, one per top-level section.

        Raises a specific `PatchNotesParseError` subclass when the document does not match the
        expected forum thread shape or comes from another source type.
        """
        if raw.source_type != SOURCE_TYPE:
            raise UnsupportedSourceTypeError(
                f"the parser handles {SOURCE_TYPE!r}, got {raw.source_type!r}"
            )
        if is_blank(raw.content):
            raise PatchNotesParseError("document is blank")
        soup = BeautifulSoup(raw.content, "html.parser")
        title = _extract_title(soup)
        version = _extract_version(title)
        thread_url = _extract_thread_url(raw, soup)
        body = _extract_body(soup)
        chunks = _build_chunks(
            body,
            title,
            version,
            thread_url,
            raw.fetched_at.date(),
            self._max_block_tokens,
        )
        if not chunks:
            raise PatchNotesParseError("document has no notes text")
        return chunks


def _extract_title(soup: BeautifulSoup) -> str:
    """Return the normalized thread title.

    The `topBar` heading wins, then any `h1`, then the page metadata: a Content Update thread has no
    `h1` at all and carries its title only in `og:title` and `<title>`.
    """
    h1 = soup.find("h1", class_="topBar") or soup.find("h1")
    if h1 is not None:
        return _normalize_text(h1.get_text())
    meta = soup.find("meta", attrs={"property": "og:title"}) or soup.find(
        "meta", attrs={"name": "og:title"}
    )
    if meta is not None and not is_blank(str(meta.get("content", ""))):
        return _normalize_text(str(meta["content"]))
    if soup.title is not None and not is_blank(soup.title.get_text()):
        return _normalize_text(soup.title.get_text())
    raise PatchVersionNotFoundError("no thread title found")


def _extract_version(title: str) -> PatchVersion:
    """Return the first token in the title that parses as a patch version.

    A title can carry other numbers before the patch version (`Path of Exile 2 - 0.5.0 Patch
    Notes`), so every candidate is tried. A hotfix such as `0.5.5e` parses as its own version, not
    as its base patch: a hotfix changes balance numbers, so mapping it to `0.5.5` would answer with
    numbers that patch never had. When none parses, the most version-like candidate is reported,
    which keeps an unknown version format an explicit unparseable value instead of a silent map.
    """
    candidates = list(_VERSION_TOKEN.finditer(title))
    if not candidates:
        raise PatchVersionNotFoundError(f"no version token in title {title!r}")
    for candidate in candidates:
        try:
            return PatchVersion.parse(candidate.group(0))
        except InvalidPatchVersionError:
            continue
    # The title can hold other numbers (`Path of Exile 2`), so the reported candidate is the most
    # version-like one: the most dotted token, and the first among equals.
    unparsed = max(candidates, key=lambda match: match.group(0).count(".")).group(0)
    raise PatchVersionUnparseableError(
        f"cannot parse a version from title {title!r} (candidate {unparsed!r})"
    )


def _extract_thread_url(raw: RawDocument, soup: BeautifulSoup) -> str:
    """Return the absolute thread URL, preferring an absolute locator."""
    if _is_absolute_http_url(raw.locator):
        return raw.locator.strip()

    for pattern in (r"/forum/post-reply/(\d+)", r"/forum/view-thread/(\d+)"):
        for link in soup.find_all("a", href=True):
            match = re.search(pattern, str(link["href"]))
            if match is not None:
                return f"https://www.pathofexile.com/forum/view-thread/{match.group(1)}"

    raise ThreadUrlNotFoundError("no thread URL found in document")


def _extract_body(soup: BeautifulSoup) -> Tag:
    """Return the content div of the first post, in document order.

    Two layouts exist and either can appear in a thread. A normal thread marks its post with
    `tr.staff` and keeps the body in `td.content-container`; the forum's news-post layout uses
    `tr.newsPost`, leaves the body cell unclassed and may put a metadata cell in front of it. Both
    are scanned in one document-order pass, so the earliest post wins and a later reply in either
    layout is conversation.

    The body is always a `div.content` that is a *direct* child of the cell: a metadata block and a
    nested quote carry that class too, and reading one of those would index the wrong text with no
    error to notice. A staff row without its anchored cell is therefore not a post body.
    """
    for row in soup.find_all("tr"):
        classes = row.get("class") or []
        if "staff" in classes:
            body = _cell_body(row.find("td", class_="content-container"))
            if body is not None:
                return body
        elif "newsPost" in classes:
            for cell in row.find_all("td", recursive=False):
                body = _cell_body(cell)
                if body is not None:
                    return body
    raise MissingPatchNotesBodyError("no staff post body found")


def _cell_body(cell: PageElement | None) -> Tag | None:
    """Return the `div.content` that a table cell holds directly, if any."""
    if not isinstance(cell, Tag):
        return None
    content = cell.find("div", class_="content", recursive=False)
    return content if isinstance(content, Tag) else None


def _build_chunks(
    body: Tag,
    title: str,
    version: PatchVersion,
    thread_url: str,
    captured_at: date,
    max_block_tokens: int,
) -> tuple[Chunk, ...]:
    """Split the post body into one chunk per top-level section, merging equal slugs.

    The entity key is `<major>-<minor>-<patch><hotfix>` (`0-5-5`, `0-5-5e`): every forum thread,
    hotfix threads included, is a separate delta that only lists its own changes. A `0.5.5e`
    thread therefore never supersedes the `0.5.5` thread; both stay open side by side. Only a
    re-capture of the same thread supersedes the earlier one.
    """
    entity = EntityRef(
        "patch_notes",
        f"{version.major}-{version.minor}-{version.patch}{version.hotfix}",
        f"Patch {version}",
    )
    source = SourceRef(SOURCE_TYPE, thread_url, captured_at)
    validity = Validity(version)
    nodes, split_level = _notes_nodes(body, title)
    if split_level is None:
        text = _render(nodes)
        if is_blank(text):
            return ()
        return _section_chunks(
            entity, validity, source, "notes", "Notes", text, max_block_tokens
        )

    sections: list[tuple[str, str, str]] = []
    current_slug: str | None = None
    current_heading = ""
    current_nodes: list[PageElement] = []
    skipping = False

    def flush() -> None:
        nonlocal current_slug, current_heading, current_nodes
        if current_nodes:
            text = _render(current_nodes)
            if not is_blank(text):
                sections.append(
                    (current_slug or "overview", current_heading or "Overview", text)
                )
        current_slug = None
        current_heading = ""
        current_nodes = []

    for node in nodes:
        if isinstance(node, Tag) and node.name == split_level:
            flush()
            heading = _normalize_text(node.get_text())
            slug = _slugify(heading)
            skipping = slug in _NAVIGATION_SLUGS
            if not skipping:
                current_slug, current_heading = slug, heading
            continue
        if not skipping:
            current_nodes.append(node)
    flush()

    headings: dict[str, str] = {}
    bodies: dict[str, list[str]] = {}
    for slug, heading, text in sections:
        headings.setdefault(slug, heading)
        bodies.setdefault(slug, []).append(text)
    return tuple(
        chunk
        for slug, texts in bodies.items()
        for chunk in _section_chunks(
            entity,
            validity,
            source,
            slug,
            headings[slug],
            "\n".join(texts),
            max_block_tokens,
        )
    )


def _section_chunks(
    entity: EntityRef,
    validity: Validity,
    source: SourceRef,
    slug: str,
    heading: str,
    text: str,
    max_block_tokens: int,
) -> tuple[Chunk, ...]:
    """Return the section as one chunk, or as consecutive blocks when it exceeds the budget.

    A section that fits keeps its own id, so an unchanged section keeps its `chunk_id` across
    captures. A section that does not fit is cut at line boundaries, never mid-sentence, into blocks
    that each repeat the context line: the embedder and the reranker see one block's text alone, so a
    block without it would be unsearchable.
    """
    if estimate_tokens(text) <= max_block_tokens:
        return (Chunk(entity, validity, slug, text, source, (), ()),)

    blocks = _pack_blocks(
        text.split("\n"), max_block_tokens, f"{entity.name} — {heading}"
    )
    width = max(2, len(str(len(blocks))))
    return tuple(
        Chunk(entity, validity, f"{slug}-{index:0{width}d}", block, source, (), ())
        for index, block in enumerate(blocks, 1)
    )


def _pack_blocks(
    lines: Sequence[str],
    max_block_tokens: int,
    prefix: str,
) -> list[str]:
    """Group consecutive lines into blocks that fit the budget, each with its context line.

    `prefix` is the context line without its counters (`Patch 0.5.5 — Endgame Changes`), so every
    block starts with `prefix (part N/M)`. The number of blocks is only known once the grouping is
    done, so the grouping reserves the widest counter it could need plus the one token the newline
    joining the context line to the body can add; a real counter is never wider and the join never
    costs more, so every block stays inside the budget. A single line wider than the budget still
    becomes its own block: cutting a sentence would break the citation.
    """
    width = len(str(len(lines)))
    widest = 10**width - 1
    reserve = estimate_tokens(f"{prefix} (part {widest}/{widest})") + 1
    limit = max(1, max_block_tokens - reserve)

    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        candidate = [*current, line]
        if current and estimate_tokens("\n".join(candidate)) > limit:
            groups.append(current)
            current = [line]
        else:
            current = candidate
    if current:
        groups.append(current)

    total = len(groups)
    return [
        "\n".join([f"{prefix} (part {part}/{total})", *group])
        for part, group in enumerate(groups, 1)
    ]


def _notes_nodes(body: Tag, title: str) -> tuple[list[PageElement], str | None]:
    """Return the post body as flat, banner-free nodes and the level to split them on."""
    flattened = _flatten_layout(body.children)
    kept = [node for node in flattened if not _is_ignorable_heading(node, title)]
    return _drop_banner_heading(kept)


def _flatten_layout(nodes: Iterable[PageElement]) -> list[PageElement]:
    """Unwrap the forum's layout boxes so every heading sits next to the text it owns.

    The news-post layout wraps a section in `div.lbox-container > div.lbox > div.box-content`, so its
    headings are never direct children of the post body. Only those boxes are unwrapped: a `ul`, a
    spoiler or a quote that happens to carry a heading is content, and promoting its heading would
    split or rename a section of a normal thread.
    """
    flat: list[PageElement] = []
    for node in nodes:
        if isinstance(node, Tag) and _is_layout_wrapper(node):
            flat.extend(_flatten_layout(node.children))
        else:
            flat.append(node)
    return flat


def _is_layout_wrapper(node: Tag) -> bool:
    """True for one of the forum's layout boxes, which carry no text of their own."""
    return bool(_LAYOUT_WRAPPER_CLASSES.intersection(node.get("class") or ()))


def _drop_banner_heading(
    nodes: Sequence[PageElement],
) -> tuple[list[PageElement], str | None]:
    """Drop the lone leading heading that banners the thread, so the split moves one level down.

    A Content Update thread opens with a single `h2` ("Content Update 0.5.5 — Path of Exile 2:
    Forbidden Rites") and lists its sections as `h3`. Splitting on the shallowest level would turn
    the whole thread into one chunk, so a heading that is alone at that level and precedes every
    deeper heading is treated as a banner: it is removed like the title heading, and the nodes before
    the first real heading then form the synthetic `overview` section (on the real page that is the
    banner's stylesheet, which the renderer drops anyway). A page whose shallowest level holds
    several headings, or a page with nothing deeper to split on, keeps the current split.

    Returns the nodes to split and the level to split on, so the caller does not compute it twice.
    """
    level = _shallowest_heading_level(nodes)
    if level is None:
        return list(nodes), None
    at_level = [
        index for index, node in enumerate(nodes) if isinstance(node, Tag) and node.name == level
    ]
    if len(at_level) != 1:
        return list(nodes), level
    banner = at_level[0]
    deeper = [
        index
        for index, node in enumerate(nodes)
        if index != banner and isinstance(node, Tag) and node.name in _HEADING_TAGS
    ]
    if not deeper or deeper[0] < banner:
        return list(nodes), level
    remaining = [node for index, node in enumerate(nodes) if index != banner]
    return remaining, _shallowest_heading_level(remaining)


def _is_ignorable_heading(node: PageElement, title: str) -> bool:
    """True for a heading that repeats the thread title or has no usable slug."""
    if not (isinstance(node, Tag) and node.name in _HEADING_TAGS):
        return False
    normalized = _normalize_text(node.get_text())
    return normalized == title or not _slugify(normalized)


def _shallowest_heading_level(nodes: Sequence[PageElement]) -> str | None:
    """Return the lowest-numbered heading tag (h2 < h3) left in the body."""
    levels: list[int] = []
    for node in nodes:
        if not isinstance(node, Tag) or node.name is None:
            continue
        level = _HEADING_LEVELS.get(node.name)
        if level is not None:
            levels.append(level)
    if not levels:
        return None
    return f"h{min(levels)}"


def _render(nodes: Sequence[PageElement]) -> str:
    """Render nodes to clean text, one line per paragraph, list item or table row."""
    lines: list[str] = []
    for node in nodes:
        lines.extend(_node_lines(node))
    cleaned = [" ".join(line.split()) for line in lines]
    return "\n".join(line for line in cleaned if line)


def _node_lines(node: PageElement) -> list[str]:
    """Turn one node into zero or more raw text lines."""
    if isinstance(node, NavigableString):
        return [str(node)]
    if not isinstance(node, Tag):
        return []
    if node.name in _NON_CONTENT_TAGS:
        return []
    if node.name == "br":
        return []
    if node.name in ("ul", "ol"):
        return _list_lines(node)
    if node.name == "table":
        return _table_lines(node)
    if node.name == "tr":
        return _table_row_lines(node)
    if node.name == "li":
        return _list_item_lines(node)
    if node.name in _BLOCK_TAGS:
        if _has_block_child(node):
            return _children_lines(node)
        return _split_inline_lines(node)
    return _split_inline_lines(node)


def _has_block_child(node: Tag) -> bool:
    """True when `node` contains a nested block, so it must be rendered recursively."""
    return any(
        isinstance(child, Tag) and child.name in _BLOCK_TAGS for child in node.children
    )


def _children_lines(node: Tag) -> list[str]:
    lines: list[str] = []
    for child in node.children:
        lines.extend(_node_lines(child))
    return lines


def _split_inline_lines(node: PageElement) -> list[str]:
    return _inline_text(node).split("\n")


def _inline_text(node: PageElement) -> str:
    """Flatten inline markup; block tags and `<br>` become line breaks."""
    if isinstance(node, NavigableString):
        return str(node)
    if isinstance(node, Tag):
        if node.name in _NON_CONTENT_TAGS:
            return ""
        if node.name == "br":
            return "\n"
        text = "".join(_inline_text(child) for child in node.children)
        if node.name in _BLOCK_TAGS:
            return f"\n{text}\n"
        return text
    return ""


def _list_lines(node: Tag) -> list[str]:
    """Render one list, giving every item (nested or not) its own bullet line."""
    lines: list[str] = []
    for item in node.find_all("li", recursive=False):
        lines.extend(_list_item_lines(item))
    return lines


def _list_item_lines(item: Tag) -> list[str]:
    """Render an item's own lines, prefix the first with a bullet and append nested lists."""
    lines: list[str] = []
    nested: list[Tag] = []
    buffer: list[str] = []

    def flush_inline() -> None:
        text = "".join(buffer)
        buffer.clear()
        for line in text.split("\n"):
            collapsed = _normalize_text(line)
            if collapsed:
                lines.append(collapsed)

    for child in item.children:
        if isinstance(child, Tag) and child.name in ("ul", "ol"):
            flush_inline()
            nested.append(child)
        elif isinstance(child, Tag) and child.name in _BLOCK_TAGS:
            flush_inline()
            for line in _node_lines(child):
                collapsed = _normalize_text(line)
                if collapsed:
                    lines.append(collapsed)
        else:
            buffer.append(_inline_text(child))
    flush_inline()

    prefixed: list[str] = []
    for line in lines:
        prefixed.append(f"- {line}" if not prefixed else line)
    for nested_list in nested:
        prefixed.extend(_list_lines(nested_list))
    return prefixed


def _table_lines(table: Tag) -> list[str]:
    """Render each table row as one line, cells joined by ` | ` to keep their association."""
    lines: list[str] = []
    for row in table.find_all("tr"):
        lines.extend(_table_row_lines(row))
    return lines


def _table_row_lines(row: Tag) -> list[str]:
    cells = row.find_all(["th", "td"], recursive=False)
    values = [_normalize_text(_inline_text(cell)) for cell in cells]
    values = [value for value in values if value]
    return [" | ".join(values)] if values else []


def _slugify(text: str) -> str:
    """Case-fold and replace non-alphanumeric runs with dashes; empty when nothing is usable."""
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


def _normalize_text(text: str) -> str:
    """Collapse whitespace to a single space and trim."""
    return " ".join(text.split())


def _is_absolute_http_url(value: str) -> bool:
    """Return True when `value` is an absolute http(s) URL."""
    try:
        parts = urlparse(value)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)
