# Session Lifecycle (Terminal Model) + Full Transcripts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Teams button reliably open/close a voice session that sits quietly through silence like a CLI prompt (one "Ready." on start, one "Goodbye." on end, no perpetual chatter), and persist a complete per-session JSONL transcript (tool calls + results) to `.logs/`.

**Architecture:** Approach A — an explicit `SessionController` state machine (`IDLE → STARTING → ACTIVE → ENDING`) owns the single source of truth for session state, with debounce + a post-teardown cooldown to kill the restart race. The in-flight agent turn runs in a daemon thread joined while watching `stop_event`, so end is responsive even mid-"thinking". Silence (no speech / blank transcription) is a no-op. A standalone `SessionTranscript` writes full-fidelity JSONL per turn, independent of telemetry. Message-walking and turn-result types live in a pure `agent_io` module.

**Tech Stack:** Python 3.11 (`.venv`), pytest (added in Task 1), `langchain_core` messages, stdlib `threading`/`json`/`pathlib`. Hardware-bound code (arecord/aplay/NeMo/Piper/evdev/hidraw) is untouched; new logic is isolated into pure, unit-tested modules.

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `pytest.ini` | Create | pytest config (`pythonpath = .`, `testpaths = tests`) |
| `tests/` | Create | unit tests for the pure modules |
| `.gitignore` | Modify | ignore `/.logs/` |
| `local_tts/transcript.py` | Create | `SessionTranscript`: per-session JSONL (header/turn/footer), durable, best-effort, per-record flush |
| `local_tts/agent_io.py` | Create | `TurnResult`, `ToolInvocation`, `extract_tool_calls_with_results()` — pure message walking |
| `local_tts/session_control.py` | Create | `SessionState`, `SessionController` (state machine + debounce + cooldown), `run_cancellable()`, `is_actionable_transcript()` |
| `local_tts/voice_loop.py` | Modify | refactor `agent_respond` → `TurnResult`; rewrite `_pipe_reader`/`main`/`_session_loop` to use the controller; remove idle auto-end + "Sorry…" chatter; add "Goodbye." |
| `local_tts/button_listener.py` | Modify | debounce BTN_0; write `toggle\n` |
| `README.md`, `CLAUDE.md`, `AGENTS.md` | Modify | document terminal model + transcripts + env vars |

**Conventions to follow:** stdlib-only for new modules (matches `telemetry.py`); deferred heavy imports inside functions (matches `voice_loop.py`); best-effort/never-raise I/O posture (matches `telemetry.py`).

---

## Task 1: Test scaffolding

**Files:**
- Create: `pytest.ini`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Install pytest into the venv**

Run: `.venv/bin/pip install pytest`
Expected: installs pytest (no error).

- [ ] **Step 2: Create `pytest.ini`**

```ini
[pytest]
pythonpath = .
testpaths = tests
addopts = -q
```

- [ ] **Step 3: Create a trivial test to prove discovery works**

`tests/test_smoke.py`:

```python
def test_imports_pure_modules():
    # These modules must import without touching hardware.
    import importlib
    for mod in ("local_tts.telemetry",):
        importlib.import_module(mod)
    assert True
```

- [ ] **Step 4: Run it**

Run: `.venv/bin/pytest tests/test_smoke.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add pytest.ini tests/test_smoke.py
git commit -m "test: add pytest scaffolding"
```

---

## Task 2: Ignore `/.logs/`

**Files:**
- Modify: `.gitignore` (the "# Project specific" block at the end)

- [ ] **Step 1: Add the ignore rule**

Append under the existing `# Project specific` section (after `/tmp/local-tts-telemetry/`):

```gitignore
# Session transcripts (durable local logs, never committed)
/.logs/
```

- [ ] **Step 2: Verify git ignores the dir**

Run: `mkdir -p .logs/transcripts && touch .logs/transcripts/x.jsonl && git status --porcelain .logs/`
Expected: no output (ignored). Then: `rm -rf .logs`

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore /.logs/ session transcripts"
```

---

## Task 3: Transcript module (`local_tts/transcript.py`)

**Files:**
- Create: `tests/test_transcript.py`
- Create: `local_tts/transcript.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_transcript.py`:

```python
import json
from types import SimpleNamespace

from local_tts.transcript import SessionTranscript


def _read(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_writes_header_turn_footer(tmp_path):
    t = SessionTranscript("sess1", base_dir=tmp_path)
    t.start(thread_id="sess1", user_id="u", llm_provider="xai",
            llm_model="grok-3-mini", device_id="dgx-spark-01")
    tool = SimpleNamespace(name="get_time", args={"tz": "UTC"},
                           result="noon", tool_call_id="c1")
    t.record_turn(user_text="hi", reply="hello there", tool_calls=[tool],
                  timing_ms={"asr": 1, "agent": 2, "tts": 3, "total": 6})
    t.end(turns=1, duration_s=12.34, end_reason="button")

    records = _read(t.path)
    assert [r["type"] for r in records] == ["header", "turn", "footer"]
    assert records[0]["llm_model"] == "grok-3-mini"
    assert records[1]["index"] == 1
    assert records[1]["user_text"] == "hi"
    assert records[1]["tool_calls"][0]["name"] == "get_time"
    assert records[1]["tool_calls"][0]["result"] == "noon"
    assert records[2]["end_reason"] == "button"
    assert records[2]["turns"] == 1


def test_reply_is_untruncated(tmp_path):
    long_reply = "x" * 2000
    t = SessionTranscript("sess2", base_dir=tmp_path)
    t.start(thread_id="sess2", user_id="u", llm_provider="p",
            llm_model="m", device_id="d")
    t.record_turn(user_text="q", reply=long_reply, tool_calls=[],
                  timing_ms={"asr": 0, "agent": 0, "tts": 0, "total": 0})
    records = _read(t.path)
    assert len(records[1]["reply"]) == 2000


def test_write_failure_does_not_raise(tmp_path, monkeypatch):
    t = SessionTranscript("sess3", base_dir=tmp_path)

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr("builtins.open", boom)
    # Must not raise even though writing fails.
    t.start(thread_id="x", user_id="u", llm_provider="p", llm_model="m", device_id="d")
    t.record_turn(user_text="q", reply="r", tool_calls=[],
                  timing_ms={"asr": 0, "agent": 0, "tts": 0, "total": 0})
    t.end(turns=0, duration_s=0.0, end_reason="error")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_transcript.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'local_tts.transcript'`).

- [ ] **Step 3: Implement the module**

`local_tts/transcript.py`:

```python
"""Durable, full-fidelity per-session transcripts (JSONL).

One file per session under TRANSCRIPT_DIR (default <repo>/.logs/transcripts/).
Captures full user text, untruncated agent replies, and every tool call with
its result. Best-effort: never raises into the voice pipeline. Each record is
flushed as written, so a crash leaves a usable partial transcript.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Repo root = parent of the local_tts package dir, resolved from this file so
# it works under systemd where the working directory may differ.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DIR = _REPO_ROOT / ".logs" / "transcripts"


def _transcript_dir() -> Path:
    override = os.environ.get("TRANSCRIPT_DIR")
    return Path(override) if override else _DEFAULT_DIR


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class SessionTranscript:
    """Writes one JSONL transcript file for a single session."""

    def __init__(self, session_id: str, base_dir=None):
        self.session_id = session_id
        self._dir = Path(base_dir) if base_dir is not None else _transcript_dir()
        self._path = self._dir / f"{_utc_stamp()}-{session_id}.jsonl"
        self._turn_index = 0
        self._ok = True

    @property
    def path(self) -> Path:
        return self._path

    def _write(self, record: dict) -> None:
        if not self._ok:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
                f.flush()
        except Exception:
            self._ok = False  # best-effort; stop trying, never raise

    def start(self, *, thread_id, user_id, llm_provider, llm_model, device_id):
        self._write({
            "type": "header",
            "session_id": self.session_id,
            "thread_id": thread_id,
            "started_at": _utc_now_iso(),
            "user_id": user_id,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "device_id": device_id,
        })

    def record_turn(self, *, user_text, reply, tool_calls, timing_ms):
        self._turn_index += 1
        self._write({
            "type": "turn",
            "index": self._turn_index,
            "started_at": _utc_now_iso(),
            "user_text": user_text,
            "reply": reply,
            "tool_calls": [
                {
                    "name": t.name,
                    "args": t.args,
                    "result": t.result,
                    "tool_call_id": t.tool_call_id,
                }
                for t in tool_calls
            ],
            "timing_ms": timing_ms,
        })

    def end(self, *, turns, duration_s, end_reason):
        self._write({
            "type": "footer",
            "ended_at": _utc_now_iso(),
            "turns": turns,
            "duration_s": round(duration_s, 1),
            "end_reason": end_reason,
        })
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_transcript.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add local_tts/transcript.py tests/test_transcript.py
git commit -m "feat: per-session JSONL transcript writer"
```

---

## Task 4: Agent turn types + tool-result pairing (`local_tts/agent_io.py`)

**Files:**
- Create: `tests/test_agent_io.py`
- Create: `local_tts/agent_io.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_agent_io.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_agent_io.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'local_tts.agent_io'`).

- [ ] **Step 3: Implement the module**

`local_tts/agent_io.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_agent_io.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add local_tts/agent_io.py tests/test_agent_io.py
git commit -m "feat: agent turn result + tool-call/result pairing"
```

---

## Task 5: Session controller + helpers (`local_tts/session_control.py`)

**Files:**
- Create: `tests/test_session_control.py`
- Create: `local_tts/session_control.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_session_control.py`:

```python
import threading

import pytest

from local_tts.session_control import (
    SessionController,
    SessionState,
    run_cancellable,
    is_actionable_transcript,
)


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _controller(clock):
    return SessionController(debounce_s=0.4, cooldown_s=1.0, now=clock)


def test_idle_toggle_starts():
    c = _controller(_Clock())
    assert c.on_toggle() == "start"
    assert c.state is SessionState.STARTING
    assert c.wait_for_start(timeout=0) is True


def test_active_toggle_ends_and_sets_stop():
    clock = _Clock()
    c = _controller(clock)
    c.on_toggle()             # -> STARTING
    c.confirm_active()        # -> ACTIVE
    clock.t += 1.0            # past debounce
    assert c.on_toggle() == "end"
    assert c.state is SessionState.ENDING
    assert c.stop_event.is_set()
    assert c.end_reason == "button"


def test_debounce_ignores_rapid_second_press():
    clock = _Clock()
    c = _controller(clock)
    assert c.on_toggle() == "start"
    clock.t += 0.1           # within debounce window
    assert c.on_toggle() == "ignored"


def test_toggle_ignored_while_starting_or_ending():
    clock = _Clock()
    c = _controller(clock)
    c.on_toggle()            # STARTING
    clock.t += 1.0
    assert c.on_toggle() == "ignored"   # STARTING ignores
    c.confirm_active()
    clock.t += 1.0
    c.on_toggle()            # -> ENDING
    clock.t += 1.0
    assert c.on_toggle() == "ignored"   # ENDING ignores


def test_cooldown_blocks_restart_after_teardown():
    clock = _Clock()
    c = _controller(clock)
    c.on_toggle(); c.confirm_active()
    clock.t += 1.0
    c.on_toggle()            # -> ENDING
    c.finish_session()       # -> IDLE, cooldown_until = now + 1.0
    clock.t += 0.5           # still inside cooldown
    assert c.on_toggle() == "ignored"
    assert c.state is SessionState.IDLE
    clock.t += 1.0           # past cooldown (and debounce)
    assert c.on_toggle() == "start"


def test_request_end_sets_reason():
    clock = _Clock()
    c = _controller(clock)
    c.on_toggle(); c.confirm_active()
    c.request_end("safety_cap")
    assert c.state is SessionState.ENDING
    assert c.stop_event.is_set()
    assert c.end_reason == "safety_cap"


def test_run_cancellable_returns_value():
    stop = threading.Event()
    assert run_cancellable(lambda: 42, stop, poll_interval=0.01) == 42


def test_run_cancellable_returns_none_when_stopped():
    stop = threading.Event()
    gate = threading.Event()
    stop.set()

    def target():
        gate.wait(timeout=2)
        return "late"

    assert run_cancellable(target, stop, poll_interval=0.01) is None


def test_run_cancellable_propagates_exception():
    stop = threading.Event()

    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        run_cancellable(boom, stop, poll_interval=0.01)


@pytest.mark.parametrize("text,expected", [
    ("", False),
    ("   ", False),
    ("a", False),         # below min_chars=2
    ("??", False),        # no letters
    ("ok", True),
    ("hello there", True),
    (None, False),
])
def test_is_actionable_transcript(text, expected):
    assert is_actionable_transcript(text) is expected
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_session_control.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'local_tts.session_control'`).

- [ ] **Step 3: Implement the module**

`local_tts/session_control.py`:

```python
"""Session lifecycle for the voice agent — the terminal/CLI model.

The session is a long-lived process: the Teams button opens it (start) and
closes it (exit). Between turns it sits quietly at the prompt — silence is a
no-op, not something to react to. This module owns the single source of truth
for session state, with press debounce and a post-teardown cooldown so a stray
press during teardown cannot restart the session ("Ready again" bug).

Pure-logic + threading.Event based, with an injectable clock, so the state
machine is unit-testable without hardware.
"""

import threading
import time
from enum import Enum


class SessionState(Enum):
    IDLE = "idle"
    STARTING = "starting"
    ACTIVE = "active"
    ENDING = "ending"


class SessionController:
    """State machine + signals coordinating start/end across threads."""

    def __init__(self, *, debounce_s=0.4, cooldown_s=1.0, now=time.monotonic):
        self._state = SessionState.IDLE
        self._lock = threading.Lock()
        self.stop_event = threading.Event()     # cancels the current session
        self._start_event = threading.Event()   # wakes the main loop to start
        self._now = now
        self._debounce_s = debounce_s
        self._cooldown_s = cooldown_s
        self._last_toggle = float("-inf")
        self._cooldown_until = 0.0
        self.end_reason = "button"

    @property
    def state(self):
        with self._lock:
            return self._state

    def on_toggle(self):
        """Apply one button toggle. Returns 'start', 'end', or 'ignored'."""
        with self._lock:
            t = self._now()
            if t - self._last_toggle < self._debounce_s:
                return "ignored"
            self._last_toggle = t
            if self._state is SessionState.IDLE:
                if t < self._cooldown_until:
                    return "ignored"
                self._state = SessionState.STARTING
                self._start_event.set()
                return "start"
            if self._state is SessionState.ACTIVE:
                self._state = SessionState.ENDING
                self.end_reason = "button"
                self.stop_event.set()
                return "end"
            return "ignored"  # STARTING or ENDING — mid-transition

    def request_end(self, reason):
        """Programmatic end (e.g. safety cap). No-op unless ACTIVE."""
        with self._lock:
            if self._state is SessionState.ACTIVE:
                self._state = SessionState.ENDING
                self.end_reason = reason
                self.stop_event.set()

    def wait_for_start(self, timeout=None):
        return self._start_event.wait(timeout=timeout)

    def confirm_active(self):
        """Main loop calls this as it begins running a session."""
        with self._lock:
            self._state = SessionState.ACTIVE
            self._start_event.clear()
            self.stop_event.clear()
            self.end_reason = "button"

    def finish_session(self):
        """Main loop calls this after teardown; arms the cooldown."""
        with self._lock:
            self._state = SessionState.IDLE
            self._start_event.clear()  # drop any stray start signal
            self.stop_event.clear()
            self._cooldown_until = self._now() + self._cooldown_s


def run_cancellable(target, stop_event, poll_interval=0.05):
    """Run target() in a daemon thread; return its result, or None if
    stop_event is set before it finishes (the result is abandoned).

    Re-raises any exception target() raised. The synchronous work itself
    cannot be force-killed (e.g. a blocking graph.invoke), but its result is
    abandoned so the session can end promptly.
    """
    box = {}

    def _runner():
        try:
            box["result"] = target()
        except BaseException as e:  # noqa: BLE001 - re-raised in caller
            box["error"] = e

    th = threading.Thread(target=_runner, daemon=True)
    th.start()
    while th.is_alive():
        if stop_event.is_set():
            return None
        th.join(timeout=poll_interval)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def is_actionable_transcript(text, min_chars=2):
    """True if a transcription is worth sending to the agent.

    Terminal model: silence does nothing. Blank/whitespace/too-short/no-letter
    transcriptions (including ASR hallucinations on ambient noise) are silent
    no-ops.
    """
    if not text:
        return False
    s = text.strip()
    if len(s) < min_chars:
        return False
    if not any(c.isalpha() for c in s):
        return False
    return True
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_session_control.py -v`
Expected: PASS (all parametrized cases + the rest pass).

- [ ] **Step 5: Commit**

```bash
git add local_tts/session_control.py tests/test_session_control.py
git commit -m "feat: session controller state machine + cancellable turn + silence guard"
```

---

## Task 6: Refactor `agent_respond` to return `TurnResult`

**Files:**
- Modify: `local_tts/voice_loop.py:164-201` (`agent_respond`)
- Modify: `local_tts/voice_loop.py:478-480` (`_demo` reply usage)
- Create: `tests/test_agent_respond.py`

- [ ] **Step 1: Write the failing test**

`tests/test_agent_respond.py`:

```python
from types import SimpleNamespace

import local_tts.voice_loop as vl


def _ai(content, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _tool(content, tool_call_id):
    return SimpleNamespace(content=content, tool_call_id=tool_call_id)


class _FakeGraph:
    def invoke(self, state):
        sent = list(state["messages"])
        return {"messages": sent + [
            _ai("", [{"name": "get_time", "args": {"tz": "UTC"}, "id": "c1"}]),
            _tool("noon", "c1"),
            _ai("It is noon."),
        ]}


def test_agent_respond_returns_turnresult_with_tool_results(monkeypatch):
    from langchain_core.messages import HumanMessage
    monkeypatch.setattr(vl, "_get_agent", lambda: _FakeGraph())
    monkeypatch.setattr(vl, "telem", lambda *a, **k: None)

    res = vl.agent_respond([HumanMessage(content="time?")], "t1")

    assert res.reply == "It is noon."
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].name == "get_time"
    assert res.tool_calls[0].result == "noon"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_agent_respond.py -v`
Expected: FAIL (`agent_respond` returns a `str`, so `res.reply` raises `AttributeError`).

- [ ] **Step 3: Add the import**

In `local_tts/voice_loop.py`, after the existing `from local_tts.telemetry import ...` line (around line 34), add:

```python
from local_tts.agent_io import TurnResult, extract_tool_calls_with_results
```

- [ ] **Step 4: Replace `agent_respond`**

Replace the entire `agent_respond` function body (lines 164-201) with:

```python
def agent_respond(messages, thread_id):
    """Send message history to the agent; return a TurnResult (reply + tool
    calls with results)."""
    t0 = time.perf_counter()
    telem("agent_request", thread_id=thread_id, message_count=len(messages),
          user_text=getattr(messages[-1], "content", "") if messages else "")
    sent_count = len(messages)
    try:
        graph = _get_agent()
        result = graph.invoke({
            "messages": messages,
            "user_id": USER_ID,
            "thread_id": thread_id,
        })
    except Exception as e:
        telem("error", stage="agent", message=str(e))
        err = str(e).lower()
        if "connection" in err or "refused" in err:
            raise RuntimeError(
                "LLM server unreachable. Check LLM_BASE_URL in .env "
                "or switch LLM_PROVIDER to 'xai'."
            ) from None
        raise

    latency_ms = int((time.perf_counter() - t0) * 1000)
    if isinstance(result, dict) and "messages" in result:
        all_msgs = result["messages"]
        last = all_msgs[-1]
        reply = getattr(last, "content", str(last))
        # Only this turn's new messages (AI + tool) — slice past what we sent.
        new_msgs = all_msgs[sent_count:]
        tool_calls = extract_tool_calls_with_results(new_msgs)
        telem("agent_response", reply=reply[:500], latency_ms=latency_ms,
              tool_calls=[{"name": t.name, "args": t.args} for t in tool_calls],
              reply_length=len(reply))
        return TurnResult(reply=reply, tool_calls=tool_calls)
    reply = str(result)
    telem("agent_response", reply=reply[:500], latency_ms=latency_ms,
          reply_length=len(reply))
    return TurnResult(reply=reply, tool_calls=[])
```

- [ ] **Step 5: Update `_demo` to use `.reply`**

In `_demo` (around lines 478-480), replace:

```python
        reply = agent_respond([HumanMessage(content=text)], thread_id)
        print(f'[DEMO] Agent reply: "{reply}"')
        speak(reply)
```

with:

```python
        result = agent_respond([HumanMessage(content=text)], thread_id)
        print(f'[DEMO] Agent reply: "{result.reply}"')
        speak(result.reply)
```

- [ ] **Step 6: Run to verify pass**

Run: `.venv/bin/pytest tests/test_agent_respond.py -v`
Expected: PASS (1 passed). (Importing `voice_loop` runs a one-time `udevadm` LED scan — harmless.)

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: PASS (all tests so far).

- [ ] **Step 8: Commit**

```bash
git add local_tts/voice_loop.py tests/test_agent_respond.py
git commit -m "refactor: agent_respond returns TurnResult with tool results"
```

---

## Task 7: Wire the controller into the voice loop

This task has no unit test (it coordinates real hardware/threads). It is verified manually in Task 10. Apply the edits exactly; the building blocks it uses are already tested (Tasks 3–6).

**Files:**
- Modify: `local_tts/voice_loop.py` — config block (lines 36-46), imports (~line 34), `_pipe_reader` (300-317), `_session_loop` (320-422), `main` (425-463)

- [ ] **Step 1: Add imports**

After the `from local_tts.agent_io import ...` line added in Task 6, add:

```python
from local_tts.session_control import (
    SessionController,
    run_cancellable,
    is_actionable_transcript,
)
from local_tts.transcript import SessionTranscript
```

- [ ] **Step 2: Update the config block**

In the `# --- Config ---` block (lines 36-46), remove the `MAX_IDLE_TURNS` line and add a safety cap (default disabled). Replace:

```python
MAX_IDLE_TURNS = 3        # end session after N consecutive no-speech turns
```

with:

```python
# Terminal model: a session stays open through silence and ends only on the
# button. SESSION_MAX_SECONDS > 0 is an optional absolute safety cap (e.g. if
# the button listener dies); 0 disables it.
SESSION_MAX_SECONDS = int(os.getenv("SESSION_MAX_SECONDS", "0"))
```

- [ ] **Step 3: Replace `_pipe_reader`**

Replace the whole `_pipe_reader` function (lines 300-317) with:

```python
def _pipe_reader(controller):
    """Background thread: every pipe trigger is one button toggle, applied to
    the session controller (which owns start/end + debounce + cooldown)."""
    while True:
        try:
            with open(PIPE_PATH, "r") as pipe:
                for line in pipe:
                    if line.strip():
                        action = controller.on_toggle()
                        telem("button_toggle", action=action)
        except Exception:
            time.sleep(0.1)
```

- [ ] **Step 4: Replace `_session_loop`**

Replace the entire `_session_loop` function (lines 320-422) with:

```python
def _session_loop(controller):
    """Run a multi-turn conversation until the controller signals stop.

    Terminal model: silence is a no-op (no idle counter, no auto-end, no
    'sorry' chatter). One 'Ready.' on start; the caller speaks 'Goodbye.'
    after this returns. Writes a full JSONL transcript of the session.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    thread_id = f"lenovo-go-{uuid.uuid4().hex[:8]}"
    messages = []
    turns = 0
    session_start_time = time.perf_counter()
    stop_event = controller.stop_event

    telem_session(session_id=thread_id, user_id=USER_ID)
    telem("session_start", thread_id=thread_id)
    print(f"\n--- Session started (thread: {thread_id}) ---")

    transcript = SessionTranscript(thread_id)
    transcript.start(
        thread_id=thread_id,
        user_id=USER_ID,
        llm_provider=os.getenv("LLM_PROVIDER", "xai"),
        llm_model=os.getenv("LLM_MODEL", os.getenv("OLLAMA_MODEL", "grok-3-mini")),
        device_id=os.getenv("DEVICE_ID", "dgx-spark-01"),
    )

    # Greeting — signals the session is live (spoken exactly once).
    speak("Ready.")
    time.sleep(SETTLE_TIME)

    try:
        while not stop_event.is_set():
            if SESSION_MAX_SECONDS and (
                time.perf_counter() - session_start_time > SESSION_MAX_SECONDS
            ):
                controller.request_end("safety_cap")
                break

            print("[MIC] Listening...")
            pcm = record_until_silence(stop_event=stop_event)

            if stop_event.is_set():
                break
            if not pcm:
                continue  # silence — terminal model: do nothing, stay open

            duration = len(pcm) / (SAMPLE_RATE * 2)
            print(f"[MIC] Captured {duration:.1f}s of audio")
            telem("recording_end", duration_s=round(duration, 2), audio_bytes=len(pcm))
            t_record = time.perf_counter()
            time.sleep(SETTLE_TIME)

            print("[ASR] Transcribing...")
            text = transcribe(pcm)
            t_asr = time.perf_counter()
            asr_ms = int((t_asr - t_record) * 1000)
            telem("asr_result", text=text or "", latency_ms=asr_ms,
                  audio_duration_s=round(duration, 2))

            if not is_actionable_transcript(text):
                print("[ASR] No actionable speech — ignoring (silent).")
                continue  # blank/garbled/hallucinated — silent no-op

            print(f'[ASR] "{text}"')
            messages.append(HumanMessage(content=text))

            print("[AGENT] Thinking...")
            result = run_cancellable(
                lambda: agent_respond(list(messages), thread_id), stop_event
            )
            if result is None or stop_event.is_set():
                print("[SESSION] Ended during agent turn — abandoning reply.")
                break
            t_agent = time.perf_counter()
            messages.append(AIMessage(content=result.reply))
            print(f'[AGENT] "{result.reply[:120]}{"..." if len(result.reply) > 120 else ""}"')

            t_tts_start = time.perf_counter()
            speak(result.reply, stop_event=stop_event)
            t_tts = time.perf_counter()
            if stop_event.is_set():
                print("[SESSION] Speech cancelled by button press.")
                telem("speech_cancelled", reply_length=len(result.reply))
                break
            tts_ms = int((t_tts - t_tts_start) * 1000)
            telem("tts_end", latency_ms=tts_ms, text_length=len(result.reply))

            agent_ms = int((t_agent - t_asr) * 1000)
            total_ms = int((t_tts - t_record) * 1000)
            print(f"[TURN] asr={asr_ms}ms agent={agent_ms}ms tts={tts_ms}ms total={total_ms}ms")
            telem("turn_complete", asr_ms=asr_ms, agent_ms=agent_ms, tts_ms=tts_ms,
                  total_ms=total_ms, user_text=text, agent_reply=result.reply[:500])

            turns += 1
            transcript.record_turn(
                user_text=text,
                reply=result.reply,
                tool_calls=result.tool_calls,
                timing_ms={"asr": asr_ms, "agent": agent_ms, "tts": tts_ms, "total": total_ms},
            )
            time.sleep(SETTLE_TIME)

    except Exception as e:
        print(f"[SESSION] Error: {e}")
        telem("error", stage="session", message=str(e))
        controller.end_reason = "error"
    finally:
        session_duration = time.perf_counter() - session_start_time
        transcript.end(turns=turns, duration_s=session_duration,
                       end_reason=controller.end_reason)
        telem("session_end", turns=turns, duration_s=round(session_duration, 1))
        telem_session()  # clear session context
        print(f"--- Session ended ({turns} turn{'s' if turns != 1 else ''}) ---\n")
```

- [ ] **Step 5: Replace `main`**

Replace the entire `main` function (lines 425-463) with:

```python
def main():
    _ensure_pipe()

    # Pre-load models so the first session is instant.
    print("Loading ASR model (one-time)...")
    _load_asr()
    print("Loading TTS voice...")
    _load_tts()

    # LED on = services ready, waiting for a session.
    set_teams_light(True)
    telem("service_ready")

    controller = SessionController()
    threading.Thread(
        target=_pipe_reader, args=(controller,), daemon=True
    ).start()

    print("\nVoice agent ready. LED on = awaiting session.")
    print("  Press Teams button to start a session; press again to end it.")
    print("  The session stays open through silence and ends with 'Goodbye.'")
    print("  Ctrl+C to shut down.\n")

    try:
        while True:
            controller.wait_for_start()
            controller.confirm_active()
            set_teams_light(True)  # solid = in session

            _session_loop(controller)

            # Closure greeting — spoken once, NOT cancellable (device is free
            # now that recording has stopped).
            time.sleep(SETTLE_TIME)
            speak("Goodbye.")

            # Blink to confirm end, then restore solid (services still running).
            set_teams_light(False); time.sleep(0.15)
            set_teams_light(True);  time.sleep(0.15)
            set_teams_light(False); time.sleep(0.15)
            set_teams_light(True)

            controller.finish_session()  # arms cooldown; stray presses ignored
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        set_teams_light(False)
        telem("service_shutdown")
```

- [ ] **Step 6: Sanity-check import + full suite**

Run: `.venv/bin/python -c "import local_tts.voice_loop"`
Expected: imports cleanly (a one-time `[LED]`/`udevadm` line may print; no traceback).

Run: `.venv/bin/pytest -v`
Expected: PASS (all existing tests still green).

- [ ] **Step 7: Commit**

```bash
git add local_tts/voice_loop.py
git commit -m "feat: terminal-model session lifecycle (controller, silence no-op, Goodbye, transcripts)"
```

---

## Task 8: Debounce the button listener

**Files:**
- Modify: `local_tts/button_listener.py` — config (lines 25-29), main loop BTN_0 branch (157-180)

- [ ] **Step 1: Add a debounce constant**

In the config block (after `VOLUME_STEP_PCT = 4`, line 29), add:

```python
BTN_DEBOUNCE_S = 0.4  # ignore Teams-button presses within this window (bounce/double-tap)
```

- [ ] **Step 2: Add the debounce tracker**

In `main`, where `last_tick = 0` is initialized (line 158), add alongside it:

```python
    last_btn = 0.0
```

- [ ] **Step 3: Replace the BTN_0 handler**

Replace the BTN_0 branch (lines 173-180) with a debounced version that writes `toggle`:

```python
                    if event.code == ecodes.BTN_0:
                        now = time.monotonic()
                        if now - last_btn < BTN_DEBOUNCE_S:
                            continue  # debounce bounce / rapid double-tap
                        last_btn = now
                        try:
                            with open(PIPE_PATH, "w") as pipe:
                                pipe.write("toggle\n")
                            print("[BTN] Teams button → session toggle")
                            telem("button_press", action="session_toggle")
                        except Exception as ex:
                            print(f"[BTN] Pipe write failed: {ex}")
```

(Note: `last_btn` is assigned inside the loop; since it's read and written in the same `main` scope this works without `nonlocal`. The voice loop's `_pipe_reader` treats any non-blank line as a toggle, so `echo toggle > /tmp/voice_trigger` — or the legacy `echo start` — both still work.)

- [ ] **Step 4: Sanity-check import**

Run: `.venv/bin/python -c "import local_tts.button_listener"`
Expected: imports cleanly, no traceback.

- [ ] **Step 5: Commit**

```bash
git add local_tts/button_listener.py
git commit -m "feat: debounce Teams button; write 'toggle' trigger"
```

---

## Task 9: Documentation

**Files:**
- Modify: `README.md` (Architecture section, lines 5-11; Key Files block, lines 106-116)
- Modify: `CLAUDE.md` (Session model note, line 26; Key Files table, lines 34-52)
- Modify: `AGENTS.md` (if it mirrors session behavior — update to match)

- [ ] **Step 1: Update README Architecture**

Replace the README "Architecture" intro (lines 7-11) with text describing the terminal model:

```markdown
Press the Teams button to start a session. The agent greets with "Ready."
Speak naturally back and forth across multiple turns. Between turns the session
stays open and quiet — like a CLI prompt — through arbitrary silence; the agent
speaks only when spoken to. Press the button again to end the session; the agent
says "Goodbye." and the LED blinks then returns to solid.

Every session is recorded in full to `.logs/transcripts/<utc>-<session_id>.jsonl`
(user text, untruncated replies, tool calls and their results). Override the
location with `TRANSCRIPT_DIR`.

- **LED solid** = services running / session active
- **LED blinks** = session ended
```

- [ ] **Step 2: Update README Key Files**

In the Key Files code block (lines 106-116), add three lines under `local_tts/voice_loop.py`:

```
local_tts/session_control.py   Session state machine, cancellable turn, silence guard
local_tts/transcript.py        Per-session full JSONL transcript writer
local_tts/agent_io.py          TurnResult + tool-call/result pairing
```

- [ ] **Step 3: Update CLAUDE.md session model**

Replace the **Session model** sentence (line 26) with:

```markdown
**Session model:** Button press starts a session; pressing again ends it
(terminal/CLI model). The session stays open through silence (no idle auto-end,
no chatter) and the agent speaks only when spoken to. Start says "Ready." once;
end says "Goodbye." once. A `SessionController` (`session_control.py`) owns
session state with press debounce + a post-teardown cooldown so a stray press
can't restart the session. End is responsive even mid-turn: the agent call runs
in a worker thread joined while watching `stop_event`, and its result is
abandoned on end. There is no network socket in the voice loop to close.
```

- [ ] **Step 4: Update CLAUDE.md Key Files table**

Add these rows to the Key Files table (after the `voice_loop.py` row, line 36):

```markdown
| `local_tts/session_control.py` | Session state machine (IDLE/STARTING/ACTIVE/ENDING), debounce, cooldown, `run_cancellable`, `is_actionable_transcript`. |
| `local_tts/transcript.py` | Per-session full-fidelity JSONL transcript (header/turn/footer) in `.logs/transcripts/`. Best-effort, flushed per turn. |
| `local_tts/agent_io.py` | `TurnResult` + `extract_tool_calls_with_results` (pairs tool calls with results by id). |
```

- [ ] **Step 5: Update CLAUDE.md env vars / gotchas**

Add to the "Code Conventions" or constraints section:

```markdown
- Transcripts: full JSONL per session in `.logs/transcripts/` (gitignored).
  Override dir via `TRANSCRIPT_DIR`. Optional `SESSION_MAX_SECONDS` safety cap
  (default 0 = no idle auto-end; the button is the natural end).
```

- [ ] **Step 6: Reconcile AGENTS.md**

Run: `grep -n "MAX_IDLE_TURNS\|Ending session\|session" AGENTS.md`
If AGENTS.md describes the old toggle/idle-end behavior, update those lines to match the terminal model wording from Step 3. If it has no session-behavior section, leave it.

- [ ] **Step 7: Commit**

```bash
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs: document terminal-model session lifecycle + transcripts"
```

---

## Task 10: End-to-end manual verification

No code changes — this validates the integration on the real device. Use the project's run flow (`make`, `make button`) from `CLAUDE.md`.

- [ ] **Step 1: Full unit suite is green**

Run: `.venv/bin/pytest -v`
Expected: all tests pass.

- [ ] **Step 2: Smoke + demo (no mic)**

Run: `make smoke`
Expected: LED toggles, "Voice agent smoke test passed." spoken (or tone fallback).

Run: `make demo`
Expected: agent replies and speaks; no crash. (Exercises the new `TurnResult` path.)

- [ ] **Step 3: Transcript is produced and complete**

After `make demo`, the demo path does not open a session transcript (it bypasses `_session_loop`), so verify transcripts via a real/dry-run session below. After Step 4/5, inspect:

Run: `ls -t .logs/transcripts/ | head` then `cat .logs/transcripts/<newest>.jsonl`
Expected: a `header` line, one `turn` line per spoken exchange with full `reply` and any `tool_calls` (name/args/result), and a `footer` with `end_reason`.

- [ ] **Step 4: Live session — silence stays open (User Story 2)**

Start the agent (`make`) and the listener (`make button`) per CLAUDE.md. Press the button to start (hear "Ready."). Say nothing for ~60s.
Expected: **no** spoken output during silence; the session does not end; logs show `[MIC] Listening...` looping with no agent calls.

- [ ] **Step 5: Live session — reliable end + no restart (User Story 1)**

While the agent is speaking a reply, press the button once.
Expected: playback stops, "Goodbye." is spoken once, LED blinks → solid, and **no** new "Ready." follows. Try pressing again immediately during the blink — it is ignored (cooldown). Press once more after ~1s — a new session starts ("Ready.") exactly once.

Also test ending while the agent is "Thinking..." (press during a long reply generation):
Expected: the in-flight reply is abandoned (not spoken), "Goodbye." once, clean return to idle.

- [ ] **Step 6: Dry-run trigger via pipe (optional, no voice)**

Run (with `make` running): `echo toggle > /tmp/voice_trigger` (start), wait, `echo toggle > /tmp/voice_trigger` (end).
Expected: "Ready." then (after silence) "Goodbye."; a transcript file appears in `.logs/transcripts/`.

- [ ] **Step 7: Final verification note + commit (if any docs tweaks)**

Confirm against the spec's Success Criteria:
- Reliable end within ~1s, one "Goodbye.", no self-restart ✅
- Survives arbitrary silence with no speech / no auto-end ✅
- Complete durable JSONL transcript with replies + tool calls/results ✅

If verification surfaced any doc inaccuracies, fix and commit:

```bash
git add -A && git commit -m "docs: verification fixups"
```

---

## Self-Review (completed during planning)

- **Spec coverage:** FR-1–FR-9 (lifecycle) → Tasks 5 + 7 + 8; FR-10–FR-17 (transcripts) → Tasks 2 + 3 + 6 + 7. User Stories 1/2/3 → Task 10 Steps 5/4/3. "Goodbye." → Task 7 Step 5. Silence no-op + hallucination guard → Tasks 5 + 7. No socket-to-close (FR-9) → documented in Task 9 Step 3.
- **Placeholder scan:** every code/test step contains complete code; no TBD/TODO. The only conditional step (Task 9 Step 6) is a grep-guarded doc reconciliation, not a placeholder.
- **Type consistency:** `SessionController`, `SessionState`, `run_cancellable`, `is_actionable_transcript`, `TurnResult`, `ToolInvocation`, `extract_tool_calls_with_results`, `SessionTranscript.{start,record_turn,end,path}` are defined in Tasks 3–5 and used with identical signatures in Tasks 6–7. `end_reason` values (`button`/`safety_cap`/`error`) are set consistently by the controller and read by the transcript footer (the spec's `cancelled`/`shutdown` collapse into `button`/process exit — noted intentionally).
```
