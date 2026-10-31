from shared.contracts import StreamEvent

from sse import format_sse


def test_format_sse_has_id_event_and_json_data():
    text = format_sse("1730000000000-0", StreamEvent(step=1, node="plan", type="node_enter", content="hi"))
    lines = text.split("\n")
    assert lines[0] == "id: 1730000000000-0"
    assert lines[1] == "event: node_enter"
    assert lines[2].startswith("data: {") and '"step":1' in lines[2]
    assert text.endswith("\n\n")
