"""Readable native Markdown, literal code and inert unsupported destinations."""

from dataclasses import replace
from itertools import chain

from assistant.rich_text import Span, changed_tail, formatted


def test_stream_edits_preserve_prefixes_and_reconcile_completed_markdown() -> None:
    sources = (
        "First paragraph stays.\n\n**Bold** and [a link](https://example.org/a).",
        "Åäö 👩🏽‍💻: a reply with combining e\u0301.\n\n- one\n- **two**",
        "## Heading\n\n```python\nprint('hello')\n```\n\nFinished.",
        "| A | B |\n| --- | --- |\n| first | **second** |",
    )
    for source in sources:
        previous: tuple[Span, ...] = ()
        for length in chain(range(1, len(source) + 1), range(len(source) - 1, -1, -1)):
            current = tuple(formatted(source[:length]))
            edit = changed_tail(previous, current)
            old_text = "".join(span.text for span in previous)
            assert old_text[:edit.offset] + "".join(span.text for span in edit.spans) == "".join(span.text for span in current)
            before = [(letter, replace(span, text="")) for span in previous for letter in span.text]
            after = [(letter, replace(span, text="")) for span in current for letter in span.text]
            assert before[:edit.offset] + [(letter, replace(span, text="")) for span in edit.spans for letter in span.text] == after
            previous = current


def test_appending_plain_stream_text_retains_the_entire_existing_run() -> None:
    existing = "An existing paragraph with Åäö and 👩🏽‍💻."
    edit = changed_tail((Span(existing),), (Span(existing + " More arrives."),))
    assert edit.offset == len(existing)
    assert edit.spans == (Span(" More arrives."),)


def test_fenced_code_preserves_indentation_markers_and_embedded_shorter_fences() -> None:
    source = '#### A heading\n\n````python\n  value = "**literal**"\n```\n````\nDone.'
    spans = list(formatted(source))
    assert ''.join(span.text for span in spans) == 'A heading\n\n  value = "**literal**"\n```\n\nDone.'
    code = [span for span in spans if "codeblock" in span.tags]
    assert ''.join(span.text for span in code) == '  value = "**literal**"\n```\n'
    assert all("bold" not in span.tags for span in code)


def test_common_reply_formatting_and_links_keep_their_text() -> None:
    spans = list(formatted('- **Done**: `budget.xlsx`\n\n[Docs](https://example.com/help)\n\n[Bad](javascript:alert(1))'))
    assert ''.join(span.text for span in spans) == '• Done: budget.xlsx\n\nDocs\n\n[Bad](javascript:alert(1))'
    assert next(span for span in spans if span.text == "Done").tags == ("bold",)
    assert next(span for span in spans if span.text == "budget.xlsx").tags == ("code",)
    assert [span.link for span in spans if span.link] == ['https://example.com/help']


def test_incomplete_code_fence_does_not_drop_the_end_of_a_reply() -> None:
    spans = list(formatted('~~~text\nUnfinished code\nKeep the entire ending.'))
    assert ''.join(span.text for span in spans) == 'Unfinished code\nKeep the entire ending.'
    assert all("codeblock" in span.tags for span in spans)


def test_nested_emphasis_headings_and_escapes_preserve_each_style() -> None:
    spans = list(formatted('# **Bold _and italic_**\n\n*Italic **and bold***; ~~old~~; `*literal*`; \\*escaped\\*; snake_case.'))
    assert ''.join(span.text for span in spans) == 'Bold and italic\n\nItalic and bold; old; *literal*; *escaped*; snake_case.'
    assert set(next(span for span in spans if span.text == "and italic").tags) == {"heading", "h1", "bold", "italic"}
    assert set(next(span for span in spans if span.text == "and bold").tags) == {"bold", "italic"}
    assert next(span for span in spans if span.text == "old").tags == ("strike",)
    assert next(span for span in spans if span.text == "*literal*").tags == ("code",)


def test_nested_lists_keep_numbering_checkboxes_and_continuation_indents() -> None:
    spans = list(formatted('7. First\n   - [x] **Nested** task\n   - [ ] Next task\n8. Second\n\n   Another paragraph.'))
    assert ''.join(span.text for span in spans) == '7. First\n☑ Nested task\n☐ Next task\n\n8. Second\n\nAnother paragraph.'
    nested = next(span for span in spans if span.text == "Nested")
    assert nested.indent == 1 and nested.hanging == "☑ " and "bold" in nested.tags
    assert next(span for span in spans if span.text == "First").indent == 0
    assert next(span for span in spans if span.text == "Another paragraph.").indent == 1


def test_task_markers_are_only_removed_from_tasks_and_can_precede_a_line_break() -> None:
    spans = list(formatted('- [X]\n  **Ready**\n- \\[x] Literal\n\n[x] Outside a list'))
    assert ''.join(span.text for span in spans) == '☑ Ready\n• [x] Literal\n\n[x] Outside a list'


def test_quotes_setext_headings_and_rules_have_block_layout() -> None:
    spans = list(formatted('Title\n=====\n\nSection\n-------\n\n> A **quote**\n>\n> > Nested\n\n---\n\nAfter.'))
    assert ''.join(span.text for span in spans) == 'Title\n\nSection\n\nA quote\n\nNested\n\n────────\n\nAfter.'
    assert "h1" in spans[0].tags
    assert "h2" in next(span for span in spans if span.text == "Section").tags
    quote = next(span for span in spans if span.text == "quote")
    assert quote.indent == 1 and set(quote.tags) == {"quote", "bold"}
    assert next(span for span in spans if span.text == "Nested").indent == 2
    assert next(span for span in spans if span.text == "After.").indent == 0


def test_tables_keep_all_cells_and_inline_formatting_in_narrow_labelled_rows() -> None:
    source = (
        '| File | Result |\n| :--- | ---: |\n'
        '| `a.py` | **Ready** \\| tested |\n'
        '| Åäö.py | [Docs](https://example.com/a_(b)) |\n'
        '| Empty.py | |'
    )
    spans = list(formatted(source))
    assert ''.join(span.text for span in spans) == (
        'File  a.py\nResult  Ready | tested\n\nFile  Åäö.py\nResult  Docs\n\nFile  Empty.py\nResult  —'
    )
    assert "code" in next(span for span in spans if span.text == "a.py").tags
    assert "bold" in next(span for span in spans if span.text == "Ready").tags
    assert [span.link for span in spans if span.link] == ['https://example.com/a_(b)']


def test_links_support_references_autolinks_nested_labels_and_balanced_destinations() -> None:
    source = '[**Docs** and `API`][guide]\n\n<https://example.org/help>\n\n[guide]: https://example.com/a_(b) "Guide"'
    spans = list(formatted(source))
    assert ''.join(span.text for span in spans) == 'Docs and API\n\nhttps://example.org/help'
    docs = next(span for span in spans if span.text == "Docs")
    assert set(docs.tags) == {"link", "bold"} and docs.link == 'https://example.com/a_(b)'
    api = next(span for span in spans if span.text == "API")
    assert set(api.tags) == {"link", "code"} and api.link == docs.link
    assert spans[-1].link == 'https://example.org/help'


def test_html_and_unsupported_urls_never_become_active_content() -> None:
    source = (
        '<script>alert(1)</script>\n\n'
        '[Bad](javascript:alert(1)) [Data](data:text/html,hello) '
        '[Local](file:///C:/private.txt) [Relative](private.txt) [Mail](mailto:user@example.com)\n\n'
        '![Diagram](https://example.com/diagram.png)'
    )
    spans = list(formatted(source))
    text = ''.join(span.text for span in spans)
    assert '<script>alert(1)</script>' in text
    assert '[Bad](javascript:alert(1))' in text
    assert 'Local' in text and 'Relative' in text and 'Mail' in text
    assert text.endswith('Diagram')
    assert [(span.text, span.link) for span in spans if span.link] == [('Diagram', 'https://example.com/diagram.png')]


def test_paragraphs_reflow_but_explicit_breaks_and_indented_code_stay_literal() -> None:
    spans = list(formatted('One line\nflows here.  \nAn explicit break.\n\n    **literal**\n      indented\n\nLast paragraph.'))
    assert ''.join(span.text for span in spans) == 'One line flows here.\nAn explicit break.\n\n**literal**\n  indented\n\nLast paragraph.'
    code = [span for span in spans if "codeblock" in span.tags]
    assert ''.join(span.text for span in code) == '**literal**\n  indented\n'
    assert all("bold" not in span.tags for span in code)


def test_unfinished_inline_markup_and_empty_blocks_keep_the_visible_ending() -> None:
    for source in ('', '**unfinished', '[Docs](https://example.com', '`unclosed', '-\n- - Nested', '| A | B |\n|---|---|'):
        text = ''.join(span.text for span in formatted(source))
        if source.startswith(('*', '[', '`')):
            assert text == source
        elif 'Nested' in source:
            assert text.endswith('Nested')
        elif source.startswith('|'):
            assert text == 'A\n\nB'
        else:
            assert text == ''
