"""Render Markdown as selectable native text, without an HTML surface.

The parser handles Markdown syntax; this adapter supplies the timeline's layout.
Tables become labelled rows so every cell remains readable in a narrow window.
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode


@dataclass(frozen=True, slots=True)
class Span:
    """A styled run, with nesting depth and a list marker for hanging indents."""

    text: str
    tags: tuple[str, ...] = ()
    link: str = ""
    indent: int = 0
    hanging: str = ""


@dataclass(frozen=True, slots=True)
class Tail:
    """The first changed character and the styled suffix that replaces it."""

    offset: int
    index: int
    spans: tuple[Span, ...]


def changed_tail(previous: tuple[Span, ...], current: tuple[Span, ...]) -> Tail:
    """Preserve unchanged text, including the prefix of an expanding span.

    Closing Markdown can restyle earlier characters. Comparing style as well
    as text keeps that correction while leaving completed paragraphs intact.
    """
    offset = 0
    for index, (before, after) in enumerate(zip(previous, current, strict=False)):
        if before == after:
            offset += len(before.text)
            continue
        shared = 0
        if (before.tags, before.link, before.indent, before.hanging) == (after.tags, after.link, after.indent, after.hanging):
            for left, right in zip(before.text, after.text, strict=False):
                if left != right:
                    break
                shared += 1
        return Tail(offset + shared, index, (replace(after, text=after.text[shared:]), *current[index + 1:]))
    index = min(len(previous), len(current))
    return Tail(offset, index, current[index:])


@dataclass(frozen=True, slots=True)
class _Block:
    spans: tuple[Span, ...]
    indent: int = 0
    prefix: str = ""
    gap: int = 2


_MARKDOWN = MarkdownIt("commonmark").disable(["html_inline", "html_block"]).enable(["table", "strikethrough"])
_TASK = re.compile(r"^\[([ xX])\]\s+")
_EMPHASIS = {"strong": "bold", "em": "italic", "s": "strike"}


def _web_link(target: object) -> str:
    if not isinstance(target, str):
        return ""
    try:
        url = urlsplit(target)
        return target if url.scheme.lower() in ("http", "https") and url.netloc else ""
    except ValueError:
        return ""


def _inline(nodes: Sequence[SyntaxTreeNode], tags: tuple[str, ...] = (), link: str = "") -> Iterator[Span]:
    for node in nodes:
        if node.type in ("text", "html_inline"):
            if node.content:
                yield Span(node.content, tags, link)
        elif node.type == "code_inline":
            yield Span(node.content, (*tags, "code"), link)
        elif node.type in ("softbreak", "hardbreak"):
            yield Span(" " if node.type == "softbreak" else "\n", tags, link)
        elif emphasis := _EMPHASIS.get(node.type):
            yield from _inline(node.children, (*tags, emphasis), link)
        elif node.type == "link":
            target = _web_link(node.attrs.get("href"))
            yield from _inline(node.children, (*tags, "link") if target else tags, target)
        elif node.type == "image":
            target = _web_link(node.attrs.get("src"))
            label = "".join(span.text for span in _inline(node.children)) or "Image"
            yield Span(label, (*tags, "link") if target else tags, target)
        else:
            yield from _inline(node.children, tags, link)


def _table(node: SyntaxTreeNode, indent: int, tags: tuple[str, ...]) -> Iterator[_Block]:
    rows = [row for section in node.children for row in section.children]
    headers = rows[0].children
    if len(rows) == 1:
        for header in headers:
            yield _Block(tuple(_inline(header.children, (*tags, "bold"))), indent)
    for row in rows[1:]:
        for column, cell in enumerate(row.children):
            label = tuple(_inline(headers[column].children, (*tags, "table_label", "bold")))
            value = tuple(_inline(cell.children, tags)) or (Span("—", (*tags, "muted")),)
            spans = (*label, Span("  ", tags), *value) if label else value
            yield _Block(spans, indent, gap=2 if column == 0 else 1)


def _list(node: SyntaxTreeNode, indent: int, tags: tuple[str, ...]) -> Iterator[_Block]:
    start = int(node.attrs.get("start", 1))
    for index, item in enumerate(node.children, start):
        blocks = list(_blocks(item.children, indent + 1, tags))
        prefix = f"{index}. " if node.type == "ordered_list" else "• "
        if not blocks:
            yield _Block((), indent, prefix, gap=1)
            continue
        first = blocks[0]
        paragraph = item.children[0]
        tight = paragraph.type == "paragraph" and paragraph.hidden
        if (paragraph.type == "paragraph" and _TASK.match(paragraph.children[0].content)
                and (task := _TASK.match("".join(span.text for span in first.spans)))):
            prefix = (prefix if node.type == "ordered_list" else "") + ("☐ " if task.string[1] == " " else "☑ ")
            first = replace(first, spans=tuple(_skip(first.spans, task.end())))
        gap = 1 if indent or (index > start and tight) else 2
        if first.prefix:
            yield _Block((), indent, prefix, gap=gap)
            yield from blocks
        else:
            yield replace(first, indent=indent, prefix=prefix, gap=gap)
            yield from blocks[1:]


def _skip(spans: Sequence[Span], count: int) -> Iterator[Span]:
    for span in spans:
        if count < len(span.text):
            yield replace(span, text=span.text[count:])
        count = max(0, count - len(span.text))


def _blocks(nodes: Sequence[SyntaxTreeNode], indent: int = 0, tags: tuple[str, ...] = ()) -> Iterator[_Block]:
    for node in nodes:
        if node.type in ("paragraph", "heading"):
            style = (*tags, "heading", node.tag) if node.type == "heading" else tags
            yield _Block(tuple(_inline(node.children, style)), indent, gap=1 if node.hidden else 2)
        elif node.type in ("bullet_list", "ordered_list"):
            yield from _list(node, indent, tags)
        elif node.type == "blockquote":
            yield from _blocks(node.children, indent + 1, (*tags, "quote"))
        elif node.type in ("fence", "code_block"):
            yield _Block((Span(node.content, (*tags, "code", "codeblock")),), indent)
        elif node.type == "hr":
            yield _Block((Span("────────", (*tags, "rule")),), indent)
        elif node.type == "table":
            yield from _table(node, indent, tags)


def formatted(text: str) -> Iterator[Span]:
    """CommonMark plus tables, strikethrough and task lists, with native layout.

    Soft line breaks flow with the window width. Code is literal, including
    unfinished fences. Only HTTP(S) destinations become clickable links.
    """
    tree = SyntaxTreeNode(_MARKDOWN.parse(text))
    trailing = 0
    started = False
    for block in _blocks(tree.children):
        if started and block.gap > trailing:
            yield Span("\n" * (block.gap - trailing))
        if block.prefix:
            yield Span(block.prefix, indent=block.indent, hanging=block.prefix)
            trailing = 0
        for span in block.spans:
            if span.text:
                yield replace(span, indent=block.indent, hanging=block.prefix)
                trailing = len(span.text) - len(span.text.rstrip("\n"))
        started = True
