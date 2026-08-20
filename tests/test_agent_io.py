from types import SimpleNamespace

from local_tts.agent_io import (
    TurnResult,
    ToolInvocation,
    extract_tool_calls_with_results,
)


def _ai(content, tool_calls=None):
    # Mimics langchain AIMessage: has .content and .tool_calls (list of dicts).
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _tool(content, tool_call_id):
    # Mimics langchain ToolMessage: has .content and .tool_call_id.
    return SimpleNamespace(content=content, tool_call_id=tool_call_id)


def test_pairs_tool_call_with_result():
    msgs = [
        _ai("", [{"name": "get_x", "args": {"a": 1}, "id": "c1"}]),
        _tool("the answer", "c1"),
        _ai("Final reply."),
    ]
    invs = extract_tool_calls_with_results(msgs)
    assert len(invs) == 1
    assert invs[0].name == "get_x"
    assert invs[0].args == {"a": 1}
    assert invs[0].result == "the answer"
    assert invs[0].tool_call_id == "c1"


def test_no_tool_calls_returns_empty():
    assert extract_tool_calls_with_results([_ai("hi")]) == []


def test_tool_call_without_result_has_empty_result():
    msgs = [_ai("", [{"name": "get_x", "args": {}, "id": "c9"}])]
    invs = extract_tool_calls_with_results(msgs)
    assert len(invs) == 1
    assert invs[0].result == ""


def test_turnresult_defaults():
    r = TurnResult(reply="hello")
    assert r.reply == "hello"
    assert r.tool_calls == []
