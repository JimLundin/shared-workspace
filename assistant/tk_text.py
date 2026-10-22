"""Native Markdown styles shared by the conversation history and notifications."""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import tkinter as tk

from .rich_text import Span
from .theme import ACCENT, FONT, MONO, MUTED, SURFACE


def configure_markdown(text: tk.Text) -> None:
    text.tag_configure("entry", lmargin1=16, lmargin2=16, rmargin=6, spacing1=2)
    text.tag_configure("muted", foreground=MUTED)
    text.tag_configure("heading", spacing1=4, spacing3=4)
    text.tag_configure("code", background=SURFACE)
    text.tag_configure("codeblock", rmargin=8, spacing1=0, spacing3=0)
    text.tag_configure("strike", overstrike=True)
    text.tag_configure("quote", foreground=MUTED)
    text.tag_configure("table_label", foreground=MUTED)
    text.tag_configure("rule", foreground=MUTED, spacing1=2, spacing3=2)
    text.tag_configure("link", foreground=ACCENT, underline=True)


def span_tags(text: tk.Text, span: Span, styles: set[str]) -> tuple[str, ...]:
    """Resolve combined emphasis into one font; Tk font tags don't combine."""
    family, size = MONO if "code" in span.tags else FONT
    if "heading" in span.tags and "code" not in span.tags:
        size = 15 if "h1" in span.tags else 13 if "h2" in span.tags else 11
    weight = "bold" if "bold" in span.tags or "heading" in span.tags else "normal"
    slant = "italic" if "italic" in span.tags else "roman"
    font = f"font-{family}-{size}-{weight}-{slant}"
    if font not in styles:
        text.tag_configure(font, font=(family, size, weight, slant))
        styles.add(font)
    margin = 16 + min(span.indent, 6) * 16 + (4 if "codeblock" in span.tags else 0)
    hanging = int(text.tk.call("font", "measure", FONT, "-displayof", text, span.hanging)) if span.hanging else 0
    layout = f"margin-{margin}-{hanging}"
    if layout not in styles:
        text.tag_configure(layout, lmargin1=margin, lmargin2=margin + hanging)
        styles.add(layout)
    return (*span.tags, font, layout)
