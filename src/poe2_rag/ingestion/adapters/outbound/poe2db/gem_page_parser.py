"""parser for one poe2db gem page.

The captured page (2026-11-01) duplicates its gem block for responsive layouts: the first
`.GemPopup` is the gem and the rest are copies, so only that one is read. The gem name lives in
`.itemHeader .itemName` (the second `.itemName` holds the type line, `Spell` for an active gem and
`Support` for a support gem, which is what decides the entity kind). The first `.Stats` block holds
the tags, the properties and `.secDescrText` (the description); the modifier block is the one after
the first `gemTabs` header (`Base`), and later variant tabs (`Cold-Infused`) are intentionally not
mixed into the base entity. `.implicitMod` is read from inside the popup only, so the page's cosmetic
effect list is never mistaken for gem implicits. The per-level data is the first table whose first
header is `Level`; the page has one table per gem tab and the main tab is the first.

The page has no canonical link and no `h1`, so the source URL is the fetcher's locator and a locator
that is not an absolute http(s) URL is rejected instead of inventing one. Text is rendered with
`<br>` as a space and whitespace collapsed, which keeps the page's exact numbers and wording while
removing formatting spaces the browser does not show.

`valid_from` is the patch the catalog says was current at the capture instant:
`current_at(captured_on)`, where `captured_on` is `fetched_at` normalized to a UTC date. The page
carries no patch version, so the capture time is the only evidence available; a naive `fetched_at` is
rejected instead of guessing a timezone, and a capture older than the first known release raises
`PatchNotReleasedError` rather than inventing an earlier patch.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import NavigableString, PageElement, Tag

from poe2_rag.ingestion.application import RawDocument
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    Modifier,
    ModifierKind,
    PatchCatalog,
    SourceRef,
    Validity,
    is_blank,
)

_SOURCE_TYPE = "poe2db"
_SUPPORT_MARKER = "support"
_BASE_TAB_CLASS = "gemTabs"
_PERCENT_MODIFIER = re.compile(
    r"^(?:.*?)(\d+(?:\.\d+)?)% (increased|reduced|more|less) (.+)$"
)
_FLAT_MODIFIER = re.compile(r"^\+(\d+(?:\.\d+)?) (?:to )?(.+)$")
_PERCENT_KINDS = {
    "increased": ModifierKind.INCREASED,
    "reduced": ModifierKind.REDUCED,
    "more": ModifierKind.MORE,
    "less": ModifierKind.LESS,
}
_PROPERTY_CLASSES = frozenset({"property", "requirements", "hybridProperty"})
_MODIFIER_SECTIONS = frozenset({"explicit-modifiers", "implicit-modifiers"})
_PUNCTUATION_SPACE = re.compile(r"\s+([,.;:])")


class Poe2dbParseError(RuntimeError):
    """Base class for every failure the poe2db gem-page parser owns."""


class Poe2dbSourceTypeError(Poe2dbParseError):
    """Raised when the document was fetched from a source this parser does not handle."""


class NotAGemPageError(Poe2dbParseError):
    """Raised when the document is not a gem page (no gem block, name or type line)."""


class MissingPageUrlError(Poe2dbParseError):
    """Raised when the locator is not the absolute page URL `SourceRef` needs."""


class NaiveFetchedAtError(Poe2dbParseError):
    """Raised when the capture time has no timezone, so its UTC day cannot be known."""


class EmptyGemPageError(Poe2dbParseError):
    """Raised when a gem page has a name but no content this parser can turn into a chunk."""


class Poe2dbGemPageParser:
    """Turn one poe2db gem page into chunks: description, properties, modifiers and levels."""

    __slots__ = ("_catalog",)

    def __init__(self, catalog: PatchCatalog) -> None:
        self._catalog = catalog

    def parse(self, raw: RawDocument) -> tuple[Chunk, ...]:
        """Parse one gem page, valid from the patch current at the capture instant onwards (S4)."""
        if raw.source_type != _SOURCE_TYPE:
            raise Poe2dbSourceTypeError(
                f"the parser handles {_SOURCE_TYPE!r}, got {raw.source_type!r}"
            )
        if is_blank(raw.content):
            raise Poe2dbParseError("document is blank")
        captured_on = _capture_date(raw.fetched_at)
        url = _page_url(raw.locator)

        soup = BeautifulSoup(raw.content, "html.parser")
        popup = soup.find(class_="GemPopup")
        if not isinstance(popup, Tag):
            raise NotAGemPageError("no gem block found")
        name = _gem_name(popup)
        kind = _gem_kind(popup)

        entity = EntityRef(kind, _slugify(name), name)
        validity = Validity(self._catalog.current_at(captured_on))
        source = SourceRef(_SOURCE_TYPE, url, captured_on)
        sections = _sections(popup)
        if not _has_core_sections(sections):
            raise EmptyGemPageError(
                f"gem page {name!r} has no properties and no description or modifiers"
            )
        return tuple(
            Chunk(entity, validity, section, text, source, tags, modifiers)
            for section, text, tags, modifiers in sections
        )


def _capture_date(fetched_at: datetime) -> date:
    """Return the UTC day `fetched_at` fell on, rejecting a naive instant instead of guessing a zone.

    A `datetime` without a timezone cannot be converted: assuming UTC or the machine's local zone
    would move the capture across a release boundary and label the page with the wrong patch. The
    exception includes the offset, which the fetcher asserts is present before it reaches here.
    """
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise NaiveFetchedAtError(
            f"fetched_at must be timezone-aware, got {fetched_at!r}: "
            "the capture day decides which patch the page is valid from"
        )
    return fetched_at.astimezone(UTC).date()


def _page_url(locator: str) -> str:
    """Return the page URL, rejecting a locator that cannot become a `SourceRef.url`."""
    try:
        parsed = urlparse(locator)
    except ValueError as exc:
        raise MissingPageUrlError(f"locator is not a URL: {locator!r}") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise MissingPageUrlError(f"locator must be an absolute http(s) URL, got {locator!r}")
    return locator.strip()


def _gem_name(popup: Tag) -> str:
    """Return the gem's own name, never the type line that follows it."""
    header = popup.find(class_="itemHeader")
    if not isinstance(header, Tag):
        raise NotAGemPageError("gem block has no header")
    for item_name in header.find_all(class_="itemName"):
        classes = item_name.get("class") or ()
        if "typeLine" in classes:
            continue
        name = _clean_text(_inline(item_name))
        if name:
            return name
    raise NotAGemPageError("gem block has no name")


def _gem_kind(popup: Tag) -> str:
    """Decide the entity kind from the page's own type line."""
    type_line = popup.find(class_="typeLine")
    if not isinstance(type_line, Tag):
        raise NotAGemPageError("gem block has no type line")
    text = _clean_text(_inline(type_line)).casefold()
    return "support_gem" if _SUPPORT_MARKER in text else "active_gem"


def _sections(popup: Tag) -> list[tuple[str, str, tuple, tuple]]:
    """Build every section the page offers, in a stable order."""
    content = popup.find(class_="content")
    scope = content if isinstance(content, Tag) else popup
    blocks = _direct_tags(scope, "Stats")
    properties = blocks[0] if blocks else None
    modifiers = _base_modifier_block(scope)

    sections: list[tuple[str, str, tuple, tuple]] = []
    description = (
        properties.find(class_="secDescrText") if isinstance(properties, Tag) else None
    )
    if isinstance(description, Tag):
        text = _clean_text(_inline(description))
        if text:
            sections.append(("description", text, (), ()))

    if isinstance(properties, Tag):
        lines = _property_lines(properties)
        if lines:
            sections.append(
                ("properties", "\n".join(lines), _tags(properties), ())
            )

    implicit = _implicit_modifiers(properties, modifiers)
    if implicit:
        sections.append(("implicit-modifiers", "\n".join(implicit), (), _parse_modifiers(implicit)))

    explicit = (
        _modifier_lines(modifiers, "explicitMod") if isinstance(modifiers, Tag) else []
    )
    if explicit:
        sections.append(("explicit-modifiers", "\n".join(explicit), (), _parse_modifiers(explicit)))

    levels = _level_table(popup)
    if levels:
        sections.append(("levels", levels, (), ()))
    return sections


def _has_core_sections(sections: Sequence[tuple[str, str, tuple, tuple]]) -> bool:
    """Require the gem's properties plus something citable; a lone level table is not a gem page."""
    names = {section for section, _, _, _ in sections}
    has_modifiers = bool(names & _MODIFIER_SECTIONS)
    return "properties" in names and (has_modifiers or "description" in names)


def _implicit_modifiers(properties: Tag | None, modifiers: Tag | None) -> list[str]:
    """Collect implicit mods once even when the fallback made one block serve both roles."""
    blocks: list[Tag] = []
    for block in (properties, modifiers):
        if isinstance(block, Tag) and not any(block is seen for seen in blocks):
            blocks.append(block)
    lines = [
        line for block in blocks for line in _nested_modifier_lines(block, "implicitMod")
    ]
    return list(dict.fromkeys(lines))


def _base_modifier_block(scope: Tag) -> Tag | None:
    """Return the `Stats` block of the `Base` gem tab, which holds the base mods.

    The tab label is checked, not assumed: a page that renders a variant tab first must not have its
    mods attributed to the base gem. The `Stats` block may be separated from its header by other
    elements, so the scan walks forward until the next tab header instead of requiring an immediate
    sibling.
    """
    tabs = [child for child in _direct_tags(scope) if _BASE_TAB_CLASS in _classes(child)]
    if tabs:
        base = next((tab for tab in tabs if _tab_label(tab) == "Base"), None)
        if base is None:
            return None
        sibling = _next_sibling_tag(base)
        while isinstance(sibling, Tag) and _BASE_TAB_CLASS not in _classes(sibling):
            if "Stats" in _classes(sibling):
                return sibling
            nested = sibling.find(class_="Stats")
            if isinstance(nested, Tag):
                return nested
            sibling = _next_sibling_tag(sibling)
        return None
    for block in _direct_tags(scope, "Stats"):
        if _modifier_lines(block, "explicitMod"):
            return block
    return None


def _tab_label(tab: Tag) -> str:
    """Return the gem tab header's own label (`Base`, `Cold-Infused`)."""
    return _clean_text(_inline(tab))


def _next_sibling_tag(node: PageElement) -> PageElement | None:
    for sibling in node.next_siblings:
        if isinstance(sibling, Tag):
            return sibling
    return None


def _property_lines(block: Tag) -> list[str]:
    """Render the property and requirement rows; separators and description are not properties."""
    lines: list[str] = []
    for child in block.find_all(recursive=False):
        if not _classes(child) & _PROPERTY_CLASSES:
            continue
        text = _clean_text(_inline(child))
        if text:
            lines.append(text)
    return lines


def _tags(block: Tag) -> tuple[str, ...]:
    """Read the first property row as the gem's tags, from its `GemTags` links when present.

    The links are the authoritative markup and keep a single-tag gem (`Attack`) from losing its
    tuple; the comma split is only the fallback for a page that rendered the tags as plain text.
    """
    for child in _direct_tags(block, "property"):
        anchors = child.find_all(class_="GemTags")
        if anchors:
            tags = tuple(
                tag
                for anchor in anchors
                for tag in [_clean_text(_inline(anchor))]
                if tag
            )
            if tags:
                return tags
        text = _clean_text(_inline(child))
        if ":" in text:
            return ()
        return tuple(tag.strip() for tag in text.split(",") if tag.strip())
    return ()


def _modifier_lines(block: Tag, class_name: str) -> list[str]:
    """Return the text of every direct modifier of `class_name` inside `block`."""
    return [
        text
        for child in block.find_all(recursive=False)
        if class_name in _classes(child)
        for text in [_clean_text(_inline(child))]
        if text
    ]


def _nested_modifier_lines(block: Tag | None, class_name: str) -> list[str]:
    """Return the text of every nested modifier of `class_name` inside `block`."""
    if not isinstance(block, Tag):
        return []
    return [
        text
        for tag in block.find_all(class_=class_name)
        for text in [_clean_text(_inline(tag))]
        if text
    ]


def _parse_modifiers(lines: Sequence[str]) -> tuple[Modifier, ...]:
    """Map only the clean shapes to `Modifier`; a range keeps its text and stays out (D2)."""
    parsed: list[Modifier] = []
    for line in lines:
        modifier = _parse_modifier(line)
        if modifier is not None:
            parsed.append(modifier)
    return tuple(parsed)


def _parse_modifier(line: str) -> Modifier | None:
    percent = _PERCENT_MODIFIER.fullmatch(line)
    if percent is not None:
        return Modifier(
            _stat(percent.group(3)),
            float(percent.group(1)),
            _PERCENT_KINDS[percent.group(2)],
        )
    flat = _FLAT_MODIFIER.fullmatch(line)
    if flat is not None:
        return Modifier(_stat(flat.group(2)), float(flat.group(1)), ModifierKind.FLAT)
    return None


def _stat(text: str) -> str:
    return text.strip().rstrip(".")


def _level_table(popup: Tag) -> str:
    """Render the gem's per-level table as one section: header plus one line per row (D1).

    Only tables inside or after the gem block are considered; an unrelated `Level` table earlier in
    the page must not be cited as this gem's. Empty cells are kept so a value never slides under the
    wrong column header.
    """
    tables = list(popup.find_all("table")) + list(popup.find_all_next("table"))
    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue
        header = [_cell(cell) for cell in rows[0].find_all(["th", "td"], recursive=False)]
        if not header or header[0] != "Level":
            continue
        lines = []
        for row in rows:
            values = [_cell(cell) for cell in row.find_all(["th", "td"], recursive=False)]
            if values and any(values):
                lines.append(" | ".join(values))
        return "\n".join(lines)
    return ""


def _cell(cell: Tag) -> str:
    return _clean_text(_inline(cell))


def _inline(node: PageElement) -> str:
    """Flatten inline markup, turning `<br>` into a space and adding no other separators."""
    if isinstance(node, NavigableString):
        return str(node)
    if isinstance(node, Tag):
        if node.name == "br":
            return " "
        return "".join(_inline(child) for child in node.children)
    return ""


def _clean_text(text: str) -> str:
    """Collapse whitespace and drop the formatting space the browser does not show before `,`."""
    return _PUNCTUATION_SPACE.sub(r"\1", " ".join(text.split()))


def _classes(tag: PageElement) -> set[str]:
    if not isinstance(tag, Tag):
        return set()
    return set(tag.get("class") or ())


def _direct_tags(scope: Tag, class_name: str | None = None) -> list[Tag]:
    """Return the direct child tags, optionally only those carrying `class_name`."""
    return [
        child
        for child in scope.find_all(recursive=False)
        if isinstance(child, Tag) and (class_name is None or class_name in _classes(child))
    ]


def _slugify(text: str) -> str:
    """Case-fold and replace non-alphanumeric runs with dashes, like the patch-notes parser."""
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
