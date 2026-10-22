"""Action payloads read as labels and lists in both notifications and history."""

from assistant.action_text import readable
from assistant.agent_session import Failed, Succeeded, ToolActivity, ToolStatus
from assistant.notifications import Entry, body


def test_action_arguments_and_results_are_readable_through_the_conversation_views() -> None:
    detail = '{"path":"C:\\\\Documents\\\\budget.xlsx","read_only":true}'
    for outcome in (ToolStatus.RUNNING, Succeeded('{"rows":24,"saved":false}'), Failed('{"error":"File unavailable"}')):
        message = ToolActivity("read", "read_file", detail, 1, outcome)
        text = body(Entry(0, message))
        assert text == message.text
        assert 'Path: C:\\Documents\\budget.xlsx\nRead only: Yes' in text
        assert '{"' not in text and 'false' not in text
        if isinstance(outcome, Succeeded):
            assert "Rows: 24\nSaved: No" in text
        elif isinstance(outcome, Failed):
            assert "Error: File unavailable" in text


def test_nested_results_show_labeled_values_and_list_items() -> None:
    text = readable('{"controls":[{"name":"Save","actions":["invoke","focus"]}],"next":null}')
    assert "Controls:" in text and "• Name: Save" in text and "Actions: invoke, focus" in text and "Next: Not set" in text
    assert readable('{}') == "" and readable('[]') == "No items"
    assert readable("A normal result with **literal Markdown**.") == "A normal result with **literal Markdown**."
    assert readable('{"unfinished":') == '{"unfinished":'


def test_control_actions_explain_the_target_without_exposing_transport_details() -> None:
    message = ToolActivity("inspect", "inspect_ui", '{"handle":42,"depth":3}', 1,
                           Succeeded('{"controls":[{"name":"Save","id":"1.2.3","actions":["invoke"]}]}'))
    assert message.text == "Inspect the selected window.\nControls found:\n• Save"
    assert "1.2.3" not in message.text and "42" not in message.text and "invoke" not in message.text


def test_control_and_window_commands_name_the_operation_and_target() -> None:
    for name, detail, expected in (
        ("ui_action", '{"action":"invoke","id":"1.2.3"}', "Activate the selected control."),
        ("ui_action", '{"action":"set_value","id":"1.2.3","value":"Budget"}', "Fill in the selected control."),
        ("ui_action", '{"id":"1.2.3"}', "Use the selected control."),
        ("manage_window", '{"action":"minimize","handle":42}', "Minimize the selected window."),
        ("manage_window", '{"action":"snap_left","handle":42}', "Snap the selected window to the left."),
        ("manage_window", '{"action":"snap_right","handle":42}', "Snap the selected window to the right."),
        ("focus_window", '{"handle":42}', "Bring the selected window forward."),
    ):
        message = ToolActivity("action", name, detail, 1)
        assert body(Entry(0, message)) == expected


def test_empty_control_search_explains_that_nothing_matched() -> None:
    message = ToolActivity("inspect", "inspect_ui", '{"query":"","handle":42}', 1, Succeeded('{"controls":[]}'))
    assert message.text == "Inspect the selected window.\nNo matching controls."


def test_control_summary_counts_unnamed_and_omitted_controls() -> None:
    result = '{"controls":[{"name":"Save"},{"name":""},{"name":"Print"},{"role":"button"},{"name":"Close"}]}'
    message = ToolActivity("inspect", "inspect_ui", '{"query":"Budget"}', 1, Succeeded(result))
    assert message.text == "Find controls matching “Budget”\nControls found:\n• Save\n• Print\n… 3 more controls"


def test_large_result_keeps_its_leading_fields_and_reports_only_omitted_values() -> None:
    fields = '"file":"budget.xlsx","rows":24,"sheet":"Revenue","saved":true,"format":"xlsx","size":2048'
    preview = "File: budget.xlsx\nRows: 24\nSheet: Revenue\nSaved: Yes\nFormat: xlsx\nSize: 2048"
    assert readable("{" + fields + "}") == preview
    assert readable("{" + fields + ',"owner":"Jim"}') == preview + "\n… 1 more fields"
    items = '"Budget","Forecast","Revenue","Costs"'
    preview = "Budget, Forecast, Revenue, Costs"
    assert readable("[" + items + "]") == preview
    assert readable("[" + items + ',{"name":"Details"}]') == preview + "\n… 1 more items"


def test_deep_control_details_stay_compact_while_the_control_name_remains_visible() -> None:
    text = readable('{"controls":[{"name":"Budget","bounds":{"x":10,"y":20},"children":[{"name":"Save"},{"name":"Print"}]}]}')
    assert "• Name: Budget" in text and "Bounds: 2 fields" in text and "Children: 2 items" in text
    assert "Save" not in text and "Print" not in text and "X: 10" not in text


def test_payload_is_formatted_before_the_preview_is_shortened() -> None:
    text = readable('{"query":"' + "å" * 400 + '"}', limit=80)
    assert text.startswith("Query: å") and text.endswith("…") and len(text) == 80
    assert "{" not in text and "\\u" not in text
    text = readable('[' + ','.join(str(number) for number in range(1000)) + ']')
    assert text == "0, 1, 2, 3\n… 996 more items"
    assert readable('{"path":"budget.xlsx"}', limit=len("Path: budget.xlsx")) == "Path: budget.xlsx"
