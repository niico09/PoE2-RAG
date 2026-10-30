"""Official Path of Exile 2 forum patch-notes parser.

One thread becomes one `patch_notes` entity and one chunk per top-level section, valid from the
detected patch onwards (S8). The page shape is the forum's, not a rendered article: the thread title
is the first `h1`, and the notes are the `.content` body of the **first** staff post; later staff
posts are conversation (a reply by a community manager is a staff post too) and are ignored. A saved
snapshot loses its URL, so the parser recovers the thread URL from the thread's own
`/forum/post-reply/<id>` links; an absolute `locator` wins when there is one.

Sections with the same slug are merged in document order, so a repeated heading, two headings that
slugify alike, or a preamble next to an `Overview` heading never produce two chunks with the same
`chunk_id`. Empty or decorative headings are skipped instead of aborting the whole document. Hotfix
threads have no section headings, so their body becomes one `notes` chunk. Headings deeper than the
shallowest one stay inside their section as text lines, which is what "one chunk per top-level
section" means here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
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

_SOURCE_TYPE = "patch_notes"
_VERSION_TOKEN = re.compile(r"\d+(?:\.\d+)*[A-Za-z]?")
_HEADING_TAGS = frozenset({"h2", "h3", "h4", "h5", "h6"})
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


class PatchNotesParser:
    """Turn an official forum patch-notes thread into domain chunks."""

    def parse(self, raw: RawDocument) -> tuple[Chunk, ...]:
        """Parse `raw` into chunks, one per top-level section.

        Raises a specific `PatchNotesParseError` subclass when the document does not match the
        expected forum thread shape or comes from another source type.
        """
        if raw.source_type != _SOURCE_TYPE:
            raise UnsupportedSourceTypeError(
                f"the parser handles {_SOURCE_TYPE!r}, got {raw.source_type!r}"
            )
        if is_blank(raw.content):
            raise PatchNotesParseError("document is blank")
        soup = BeautifulSoup(raw.content, "html.parser")
        title = _extract_title(soup)
        version = _extract_version(title)
        thread_url = _extract_thread_url(raw, soup)
        body = _extract_body(soup)
        chunks = _build_chunks(body, title, version, thread_url, raw.fetched_at.date())
        if not chunks:
            raise PatchNotesParseError("document has no notes text")
        return chunks


def _extract_title(soup: BeautifulSoup) -> str:
    """Return the normalized thread title, preferring the `topBar` heading."""
    h1 = soup.find("h1", class_="topBar") or soup.find("h1")
    if h1 is None:
        raise PatchVersionNotFoundError("no thread title found")
    return _normalize_text(h1.get_text())


def _extract_version(title: str) -> PatchVersion:
    """Return the first token in the title that parses as a patch version.

    A title can carry other numbers before the patch version (`Path of Exile 2 - 0.5.0 Patch
    Notes`), so every candidate is tried. When none parses, the first candidate is reported, which
    keeps a suffixed patch such as `0.5.5e` an explicit unparseable value instead of a silent map.
    """
    candidates = list(_VERSION_TOKEN.finditer(title))
    if not candidates:
        raise PatchVersionNotFoundError(f"no version token in title {title!r}")
    for candidate in candidates:
        try:
            return PatchVersion.parse(candidate.group(0))
        except InvalidPatchVersionError:
            continue
    first = candidates[0].group(0)
    raise PatchVersionUnparseableError(
        f"cannot parse a version from title {title!r} (first candidate {first!r})"
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
    """Return the content div of the first staff post; later staff posts are conversation."""
    for row in soup.find_all("tr", class_="staff"):
        cell = row.find("td", class_="content-container")
        if not isinstance(cell, Tag):
            continue
        content = cell.find("div", class_="content")
        if isinstance(content, Tag):
            return content
    raise MissingPatchNotesBodyError("no staff post body found")


def _build_chunks(
    body: Tag,
    title: str,
    version: PatchVersion,
    thread_url: str,
    captured_at: date,
) -> tuple[Chunk, ...]:
    """Split the post body into one chunk per top-level section, merging equal slugs."""
    entity = EntityRef(
        "patch_notes",
        f"{version.major}-{version.minor}-{version.patch}",
        f"Patch {version}",
    )
    source = SourceRef("patch_notes", thread_url, captured_at)
    nodes = [node for node in body.children if not _is_ignorable_heading(node, title)]
    split_level = _shallowest_heading_level(nodes)
    if split_level is None:
        text = _render(nodes)
        if is_blank(text):
            return ()
        return (Chunk(entity, Validity(version), "notes", text, source, (), ()),)

    sections: list[tuple[str, str]] = []
    current_slug: str | None = None
    current_nodes: list[PageElement] = []

    def flush() -> None:
        nonlocal current_slug, current_nodes
        if current_nodes:
            text = _render(current_nodes)
            if not is_blank(text):
                sections.append((current_slug or "overview", text))
        current_slug = None
        current_nodes = []

    for node in nodes:
        if isinstance(node, Tag) and node.name == split_level:
            flush()
            current_slug = _slugify(_normalize_text(node.get_text()))
            current_nodes = []
        else:
            current_nodes.append(node)
    flush()

    merged: dict[str, list[str]] = {}
    for slug, text in sections:
        merged.setdefault(slug, []).append(text)
    return tuple(
        Chunk(entity, Validity(version), slug, "\n".join(texts), source, (), ())
        for slug, texts in merged.items()
    )


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
        if isinstance(node, Tag) and node.name in _HEADING_TAGS and node.name is not None:
            levels.append(int(node.name[1]))
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
