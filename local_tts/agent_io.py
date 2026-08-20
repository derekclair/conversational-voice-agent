"""Structured agent-turn results and tool-call/result pairing.

Kept pure (no hardware, no network) so the message-walking logic is unit
testable independently of the voice loop. voice_loop.agent_respond builds a
TurnResult using extract_tool_calls_with_results.
"""

from dataclasses import dataclass, field


@dataclass
class ToolInvocation:
    name: str
    args: dict
    result: str = ""
    tool_call_id: str = ""


@dataclass
class TurnResult:
    reply: str
    tool_calls: list = field(default_factory=list)


def _content_str(value) -> str:
    return value if isinstance(value, str) else str(value)


def extract_tool_calls_with_results(messages):
    """Walk LangChain-style messages, pairing each tool call with its result.

    Pairing is by tool_call_id: AIMessage.tool_calls (list of dicts with
    name/args/id) are matched to ToolMessage objects (.tool_call_id/.content).
    Duck-typed so tests can pass simple namespaces. Returns list[ToolInvocation].
    """
    results_by_id = {}
    for m in messages:
        tcid = getattr(m, "tool_call_id", None)
        if tcid:
            results_by_id[tcid] = _content_str(getattr(m, "content", ""))

    invocations = []
    for m in messages:
        tcs = getattr(m, "tool_calls", None)
        if not tcs:
            continue
        for tc in tcs:
            if isinstance(tc, dict):
                name = tc.get("name", "")
                args = tc.get("args", {}) or {}
                tcid = tc.get("id", "") or ""
            else:
                name = getattr(tc, "name", "")
                args = getattr(tc, "args", {}) or {}
                tcid = getattr(tc, "id", "") or ""
            invocations.append(
                ToolInvocation(
                    name=name,
                    args=args,
                    result=results_by_id.get(tcid, ""),
                    tool_call_id=tcid,
                )
            )
    return invocations
