"""Official Path of Exile 2 forum patch-notes parser.

One thread becomes one `patch_notes` entity and one chunk per top-level section, valid from the
detected patch onwards (S8). The page shape is the forum's, not a rendered article: the thread title
is the first `h1`, and the notes are the `.content` body of every staff row, so community replies are
never ingested. A saved snapshot loses its URL, so the parser recovers the thread URL from the
thread's own `/forum/post-reply/<id>` links; an absolute `locator` wins when there is one.

Hotfix threads have no section headings, so their body becomes one `notes` chunk. Text before the
first heading becomes an `overview` chunk. Headings deeper than the shallowest one stay inside their
section as text lines, which is what "one chunk per top-level section" means here.
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

_VERSION_TOKEN = re.compile(r"\d+(?:\.\d+)*[A-Za-z]?")
_HEADING_TAGS = frozenset({"h2", "h3", "h4", "h5", "h6"})


class PatchNotesParseError(RuntimeError):
    """Raised when a patch-notes document cannot be parsed."""


class PatchVersionNotFoundError(PatchNotesParseError):
    """Raised when the thread title contains no version token."""


class PatchVersionUnparseableError(PatchNotesParseError):
    """Raised when the version token in the title is not a known patch format."""


class MissingPatchNotesBodyError(PatchNotesParseError):
    """Raised when the thread has no staff post body."""


class ThreadUrlNotFoundError(PatchNotesParseError):
    """Raised when the thread URL cannot be recovered from the document."""


class PatchNotesParser:
    """Turn an official forum patch-notes thread into domain chunks."""

    def parse(self, raw: RawDocument) -> tuple[Chunk, ...]:
        """Parse `raw` into chunks, one per top-level section.

        Raises a specific `PatchNotesParseError` subclass when the document does not
        match the expected forum thread shape.
        """
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
    """Return the first patch-looking token from the title."""
    match = _VERSION_TOKEN.search(title)
    if match is None:
        raise PatchVersionNotFoundError(f"no version token in title {title!r}")
    token = match.group(0)
    try:
        return PatchVersion.parse(token)
    except InvalidPatchVersionError as exc:
        raise PatchVersionUnparseableError(
            f"cannot parse version {token!r} from title {title!r}"
        ) from exc


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
    """Return a wrapper containing every staff post content div, in document order."""
    contents: list[Tag] = []
    for row in soup.find_all("tr", class_="staff"):
        cell = row.find("td", class_="content-container")
        if not isinstance(cell, Tag):
            continue
        content = cell.find("div", class_="content")
        if isinstance(content, Tag):
            contents.append(content)

    if not contents:
        raise MissingPatchNotesBodyError("no staff post body found")

    wrapper_soup = BeautifulSoup("<div></div>", "html.parser")
    wrapper = wrapper_soup.div
    assert isinstance(wrapper, Tag)
    for content in contents:
        for child in list(content.contents):
            wrapper.append(child.extract())
    return wrapper


def _build_chunks(
    body: Tag,
    title: str,
    version: PatchVersion,
    thread_url: str,
    captured_at: date,
) -> tuple[Chunk, ...]:
    """Split the post body into one chunk per top-level section."""
    entity = EntityRef(
        "patch_notes",
        f"{version.major}-{version.minor}-{version.patch}",
        f"Patch {version}",
    )
    source = SourceRef("patch_notes", thread_url, captured_at)
    children = list(body.children)
    start_index = _skip_repeated_title(children, title)

    split_level = _shallowest_heading_level(children, start_index)
    if split_level is None:
        text = _render(children[start_index:])
        if is_blank(text):
            return ()
        return (Chunk(entity, Validity(version), "notes", text, source, (), ()),)

    chunks: list[Chunk] = []
    current_name: str | None = None
    current_nodes: list[PageElement] = []

    def flush() -> None:
        nonlocal current_name, current_nodes
        if not current_name and not current_nodes:
            return
        text = _render(current_nodes)
        if not is_blank(text):
            section = _slugify(current_name) if current_name is not None else "overview"
            chunks.append(
                Chunk(entity, Validity(version), section, text, source, (), ())
            )
        current_name = None
        current_nodes = []

    for child in children[start_index:]:
        if isinstance(child, Tag) and child.name == split_level:
            flush()
            current_name = _normalize_text(child.get_text())
            current_nodes = []
        else:
            current_nodes.append(child)
    flush()

    return tuple(chunks)


def _skip_repeated_title(children: Sequence[PageElement], title: str) -> int:
    """Drop the first heading when it repeats the thread title."""
    for index, child in enumerate(children):
        if isinstance(child, NavigableString):
            if not is_blank(str(child)):
                break
            continue
        if (
            isinstance(child, Tag)
            and child.name in _HEADING_TAGS
            and _normalize_text(child.get_text()) == title
        ):
            return index + 1
        break
    return 0


def _shallowest_heading_level(
    children: Sequence[PageElement], start_index: int
) -> str | None:
    """Return the lowest-numbered heading tag (h2 < h3) found after the title."""
    found: list[str] = []
    for child in children[start_index:]:
        if isinstance(child, Tag) and child.name in _HEADING_TAGS:
            found.append(child.name)
    if not found:
        return None
    return min(found, key=lambda tag: int(tag[1]))


def _render(nodes: Sequence[PageElement]) -> str:
    """Render nodes to a clean string, one line per paragraph and list item."""
    lines: list[str] = []
    for node in nodes:
        lines.extend(_node_lines(node))
    cleaned: list[str] = []
    for line in lines:
        collapsed = " ".join(line.split())
        if collapsed:
            cleaned.append(collapsed)
    return "\n".join(cleaned)


def _node_lines(node: PageElement) -> list[str]:
    """Turn one node into zero or more raw text lines."""
    if isinstance(node, NavigableString):
        return [str(node)]
    if isinstance(node, Tag):
        if node.name in ("ul", "ol"):
            return _list_lines(node)
        return [line for line in _inline_text(node).split("\n") if line.strip()]
    return []


def _list_lines(node: Tag) -> list[str]:
    """Render one list, giving every item (nested or not) its own bullet line."""
    lines: list[str] = []
    for item in node.find_all("li", recursive=False):
        lines.extend(_list_item_lines(item))
    return lines


def _list_item_lines(item: Tag) -> list[str]:
    """Render an item's own text first, then each nested list as its own lines."""
    own_text = "".join(
        _inline_text(child)
        for child in item.children
        if not (isinstance(child, Tag) and child.name in ("ul", "ol"))
    )
    lines: list[str] = []
    for line in own_text.split("\n"):
        collapsed = _normalize_text(line)
        if collapsed:
            lines.append(f"- {collapsed}")
    for nested in item.find_all(["ul", "ol"], recursive=False):
        lines.extend(_list_lines(nested))
    return lines


def _inline_text(node: PageElement) -> str:
    """Strip inline markup and treat `<br>` as a line break."""
    if isinstance(node, NavigableString):
        return str(node)
    if isinstance(node, Tag):
        if node.name == "br":
            return "\n"
        return "".join(_inline_text(child) for child in node.children)
    return ""


def _slugify(text: str) -> str:
    """Case-fold and replace non-alphanumeric runs with dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    if not slug:
        raise PatchNotesParseError(f"section slug is empty for text {text!r}")
    return slug


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
