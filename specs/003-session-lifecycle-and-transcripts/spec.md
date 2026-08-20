# Feature Specification: Session Lifecycle (Terminal Model) + Full Transcripts

**Feature Branch**: `003-session-lifecycle-and-transcripts`

**Created**: 2026-06-15

**Status**: Draft

**Owner**: Derek Clair

**Input**: The "end call" button should reliably close/reset the session so the agent
stops greeting perpetually ("I'm here", "Ready", "how can I help you"). Separately,
keep a full transcript of every session — tool calls and results included — in durable,
low-processing local files, until the flow/UX is better understood.

**Related**: `specs/001-interim-lenovo-go-voice-spike.md`, README.md,
`local_tts/voice_loop.py`, `local_tts/button_listener.py`, `local_tts/telemetry.py`

---

## Overview

Two related changes to the voice agent's session layer:

1. **Make a session behave like a CLI/terminal process** — the central mental model
   for this spec. The session is a long-lived process. The Teams button is the only
   thing that opens it (`start`) and closes it (`exit` / `Ctrl-D`). Between spoken
   turns the session simply *sits at the prompt*: silence is the normal idle state,
   not something to react to. The agent speaks **only** when actually spoken to.

2. **Persist a complete transcript of every session** to durable local files (JSONL),
   including full user text, full agent replies (untruncated), and every tool call
   **with its result**.

The Nemotron 3 migration is explicitly **out of scope** for this spec (deferred).

### The Mental Model (why the current behavior is wrong)

A terminal session does not greet you every few seconds. You open it, it shows a
prompt, and then it waits — quietly, indefinitely — until you type. It only says
something when you give it something to act on, and it closes when you tell it to.

The current voice loop violates this in three ways:

- It **auto-ends** after `MAX_IDLE_TURNS = 3` silent turns and announces "Ending session."
- It **chatters on non-input**: an empty transcription triggers
  `speak("Sorry, I couldn't understand that.")`.
- Its **end-call is unreliable**, so sessions appear to restart (you hear "Ready."
  again) or ignore the button (the in-flight turn keeps going).

This spec makes silence a no-op, makes the button the single authoritative open/close
control, and ends each session with a single clean **"Goodbye."**

---

## Problem Statement (current failure modes)

The session lifecycle is an implicit toggle driven by shared mutable state across
threads, with no debounce and no explicit "ending" state.

1. **"Says Ready again" (session restart race).** `_pipe_reader` decides start-vs-end
   by reading `session_event.is_set()`. During the ~0.6 s LED-blink teardown in
   `_session_loop`'s `finally` block, `session_event` is already cleared, so a button
   press (or a stale queued press) in that window is interpreted as **start** → a new
   `_session_loop` runs → `speak("Ready.")`. Result: the session appears to restart on
   its own.

2. **"Button seems ignored" (uninterruptible turn).** `agent_respond()` is a blocking
   `graph.invoke()`. Pressing end sets `stop_event`, but the current turn finishes the
   LLM call before anything checks it. There is **no socket or connection to close** —
   the agent is a synchronous in-process call — so "reset the session" means: abandon
   the in-flight turn's result and tear the session down promptly.

3. **Chatter on silence / non-input.** Empty transcriptions and idle turns produce
   spoken output, and the session auto-terminates — both contrary to the terminal model.

4. **Transcripts are incomplete and ephemeral.** Telemetry (`telemetry.py`) captures
   `asr_result`, `agent_response`, and `turn_complete`, but: replies are truncated to
   500 chars, tool **results** are not captured, and output lands in `/tmp` (wiped on
   reboot). There is no durable, complete, per-session record.

---

## Goals

- Pressing the button during an active session **reliably and promptly** ends it,
  cancelling any in-flight turn, with no possibility of an immediate self-restart.
- A session **stays open through arbitrary silence** (terminal model); it does not
  auto-end on idle and does not speak unless spoken to.
- Each session ends with exactly one spoken **"Goodbye."**
- Every session produces a **complete, durable transcript** (JSONL) including tool
  calls and their results, written live (per turn) so a crash still leaves a usable
  partial.

## Non-Goals

- Nemotron 3 / LLM migration (deferred to a future spec).
- Long-press or multi-gesture button semantics (rejected — single press toggles).
- Markdown/HTML transcript rendering (the JSONL holds everything; a renderer can be
  added later as a pure, separate post-processing step if ever wanted).
- Barge-in / true mid-utterance interruption of the LLM call (the synchronous
  `graph.invoke()` cannot be force-killed; its result is abandoned instead).
- Changing the STT/TTS engines or the half-duplex audio handling.

---

## User Scenarios & Testing

### User Story 1 — Reliable close (Priority: P1)

The user presses the Teams button to end an active session. The session ends promptly,
the agent says "Goodbye." once, and it does **not** start talking again.

**Why this priority**: This is the core complaint — the agent greeting perpetually.

**Independent Test**: During an active session (mid-reply or mid-"thinking"), press the
button once. Within ~1 second the agent says "Goodbye.", the LED blinks then returns to
solid, and no further speech occurs.

**Acceptance Scenarios**:

1. **Given** an active session where the agent is speaking a reply, **When** the user
   presses the button, **Then** playback stops, the agent says "Goodbye." once, and the
   session returns to idle (LED solid) with no restart.
2. **Given** an active session where the agent is mid-`graph.invoke()` ("thinking"),
   **When** the user presses the button, **Then** the in-flight reply is abandoned (not
   spoken), the agent says "Goodbye." once, and the session ends.
3. **Given** a session that just ended, **When** a stray/queued button press lands
   during the teardown/cooldown window, **Then** it is ignored — no new session starts.
4. **Given** an idle (no active session) state, **When** the user presses the button,
   **Then** exactly one new session starts and the agent says "Ready." once.

### User Story 2 — Sit quietly at the prompt (Priority: P1)

During an active session, the user stops talking for a long time. The agent stays
silent and the session stays open, like a terminal waiting at its prompt.

**Why this priority**: This *is* the terminal model; it eliminates the perpetual
"how can I help you" chatter.

**Independent Test**: Start a session, say nothing for several minutes. The agent emits
no speech, the session does not end, and a later spoken turn is handled normally.

**Acceptance Scenarios**:

1. **Given** an active session, **When** no speech is detected for an extended period,
   **Then** the agent produces no speech and the session remains open.
2. **Given** an active session, **When** a capture yields an empty/blank transcription,
   **Then** the agent says nothing and simply listens again (no "Sorry…").
3. **Given** an active session, **When** the user speaks after a long silence, **Then**
   the turn is transcribed, answered, and spoken normally.

### User Story 3 — Complete session transcript (Priority: P1)

After a session, a complete record exists on disk: every user utterance, every agent
reply in full, and every tool call with its arguments and result.

**Why this priority**: Needed to understand flow/procedure and UX before iterating.

**Independent Test**: Run a session with at least one tool-calling turn. Confirm a JSONL
file exists under `.logs/transcripts/` containing the header, each turn (full text +
tool calls + tool results), and a footer with the end reason.

**Acceptance Scenarios**:

1. **Given** a completed session, **When** the transcript file is inspected, **Then** it
   contains a header (session id, start time, user id, LLM provider/model, device id),
   one record per turn, and a footer (turn count, duration, end reason).
2. **Given** a turn where the agent called a tool, **When** that turn's record is
   inspected, **Then** it includes the tool name, full arguments, and the full tool
   result.
3. **Given** an agent reply longer than 500 characters, **When** the transcript is
   inspected, **Then** the reply is stored **untruncated** (telemetry may still
   truncate; the transcript does not).
4. **Given** the process crashes mid-session, **When** the transcript file is inspected,
   **Then** the header and all turns completed before the crash are present (per-turn
   flush).

### Edge Cases

- **Half-duplex audio**: "Goodbye." must play after recording stops; the existing
  `SETTLE_TIME` / `_robust_aplay` retry path applies. The closure greeting is **not**
  cancellable by the same `stop_event` that ended the session (it must complete).
- **ASR hallucination on ambient noise**: VAD may trip on noise and Parakeet may emit
  spurious text, which would make the agent "respond to nothing." Mitigation: drop
  transcriptions that are empty or below a minimum-content threshold before calling the
  agent (silent no-op). This keeps the prompt quiet without an auto-end.
- **Button bounce / rapid double-press**: debounce at the listener and ignore toggles
  during transition states (`STARTING`/`ENDING`) and the post-teardown cooldown.
- **Safety cap (optional)**: a generous absolute `SESSION_MAX_SECONDS` cap (default
  disabled or large) prevents a session from holding the audio device forever if the
  button listener dies. The natural end is always the button.
- **Transcript I/O failure**: transcript writing is best-effort and must never block or
  crash the voice pipeline (mirrors telemetry's failure posture).

---

## Functional Requirements

### Session lifecycle (Approach A — explicit state machine)

- **FR-1**: Session state MUST be an explicit machine with states `IDLE`, `STARTING`,
  `ACTIVE`, `ENDING`, guarded by a lock, as the single source of truth (replacing the
  implicit `session_event.is_set()` toggle).
- **FR-2**: A button toggle in `IDLE` MUST start exactly one session; a toggle in
  `ACTIVE` MUST begin ending it; a toggle in `STARTING` or `ENDING` MUST be ignored.
- **FR-3**: On end, the system MUST set `stop_event` to cancel the current turn, and the
  in-flight `graph.invoke()` result MUST be abandoned (not spoken, not appended as a
  spoken turn). The agent call runs in a worker thread the session loop joins while
  polling `stop_event`, so end is responsive even mid-"thinking."
- **FR-4**: After teardown, the system MUST enforce a **cooldown** window during which
  toggles are ignored, and MUST discard any button triggers that arrived during
  `ENDING`/teardown — preventing the "Ready again" restart.
- **FR-5**: The button listener MUST debounce presses (ignore presses within a short
  window of the previous one).
- **FR-6**: A session MUST NOT auto-end on idle/silence. `MAX_IDLE_TURNS` auto-end is
  removed. (An optional absolute `SESSION_MAX_SECONDS` safety cap MAY exist, default
  disabled/large.)
- **FR-7**: On a no-speech capture or an empty/below-threshold transcription, the system
  MUST produce no speech and simply listen again (removes "Sorry, I couldn't understand
  that.").
- **FR-8**: Session start MUST speak "Ready." exactly once; session end MUST speak
  "Goodbye." exactly once. The "Goodbye." utterance MUST complete (not be cancelled by
  the ending `stop_event`).
- **FR-9**: There is no network socket/connection in the voice loop to close; "reset the
  session" is satisfied by FR-1–FR-4 plus starting each new session with a fresh
  `thread_id` (already the case) and cleared message history.

### Transcripts

- **FR-10**: Each session MUST write one JSONL transcript file to a durable local
  directory, default `<repo>/.logs/transcripts/`, overridable via the `TRANSCRIPT_DIR`
  environment variable. The repo root MUST be resolved from the package location (not
  CWD) so it works under systemd.
- **FR-11**: `/.logs/` MUST be added to `.gitignore` (transcripts are never version
  controlled).
- **FR-12**: The transcript MUST capture, per turn: the full user text, the full
  agent reply (**untruncated**), every tool call (name + full arguments) **paired with
  its full result**, and per-turn timing (asr/agent/tts/total ms).
- **FR-13**: Tool results MUST be extracted by walking `result["messages"]` from the
  agent and pairing `AIMessage.tool_calls` with their `ToolMessage` outputs via
  `tool_call_id`. This requires refactoring `agent_respond()` to return a structured
  turn result (reply + tool_calls-with-results + the turn's delta messages) instead of a
  bare reply string.
- **FR-14**: The transcript file MUST include a header record (session id / thread id,
  UTC start time, user id, LLM provider + model, device id) and a footer record (turn
  count, duration, **end reason**: `button` | `cancelled` | `safety_cap` | `error` |
  `shutdown`).
- **FR-15**: Each record MUST be flushed to disk as it is written (per turn), so a crash
  leaves a usable partial transcript.
- **FR-16**: Transcript writing MUST be best-effort: any I/O error is swallowed and MUST
  NOT block or crash the voice pipeline.
- **FR-17**: Existing telemetry behavior (including its 500-char reply truncation and
  `/tmp` JSONL) is unchanged; the transcript is an independent, additional sink.

---

## Design / Approach

### Approach A: explicit state machine + cancellable turn + cooldown

A small session controller owns the state machine and the lifecycle events. Sketch:

```
button_listener (debounced)  ──"toggle"──▶  /tmp/voice_trigger (FIFO)
                                                  │
                                          _pipe_reader thread
                                                  │ (applies toggle to controller, under lock)
                                                  ▼
        IDLE ──toggle──▶ STARTING ──▶ ACTIVE ──toggle──▶ ENDING ──▶ (cooldown) ──▶ IDLE
                                          │                  ▲
                                          │ stop_event.set() │
                                   abandon in-flight turn ───┘
```

- **Toggle handler** (under lock): respects a `cooldown_until` timestamp; `IDLE`→start,
  `ACTIVE`→set `stop_event` + go `ENDING`, `STARTING`/`ENDING`→ignore.
- **Main loop**: waits for a start signal, runs `_session_loop`, then on return speaks
  "Goodbye.", blinks the LED, sets `IDLE`, drains stale triggers, sets `cooldown_until`,
  restores LED solid.
- **Cancellable turn**: `agent_respond` runs in a daemon worker thread; `_session_loop`
  joins it in a poll loop watching `stop_event`. If end fires, the result is discarded
  and the loop breaks to teardown. (The LLM call itself isn't killed — documented
  limitation; its effects are abandoned and the next session uses a fresh `thread_id`.)
- **Silence is a no-op**: `record_until_silence` returning `None`, or a blank/
  below-threshold transcription, loops again with no speech and no idle counter.

### Transcript module (`local_tts/transcript.py`)

A single-purpose `SessionTranscript` object, independent of `telemetry.py`:

- `start(session_id, thread_id, user_id, llm_provider, llm_model, device_id)` → opens
  the file and writes the header record.
- `record_turn(user_text, reply, tool_calls_with_results, timing)` → appends one turn
  record and flushes.
- `end(turn_count, duration_s, end_reason)` → appends the footer record.

File: `<TRANSCRIPT_DIR>/<utc-timestamp>-<session_id>.jsonl`, default `TRANSCRIPT_DIR`
= `<repo>/.logs/transcripts/`.

#### Transcript JSONL schema (one JSON object per line)

```jsonc
// header
{"type":"header","session_id":"lenovo-go-ab12cd34","thread_id":"lenovo-go-ab12cd34",
 "started_at":"2026-06-15T18:03:11Z","user_id":"sm_project_default",
 "llm_provider":"xai","llm_model":"grok-3-mini","device_id":"dgx-spark-01"}

// turn (one per spoken exchange)
{"type":"turn","index":1,"started_at":"2026-06-15T18:03:14Z",
 "user_text":"what's on my calendar tomorrow?",
 "reply":"<full untruncated agent reply>",
 "tool_calls":[
   {"name":"get_calendar","args":{"date":"2026-06-16"},
    "result":"<full tool output>","tool_call_id":"call_abc"}],
 "timing_ms":{"asr":420,"agent":1180,"tts":260,"total":1900}}

// footer
{"type":"footer","ended_at":"2026-06-15T18:09:02Z","turns":4,
 "duration_s":351.2,"end_reason":"button"}
```

---

## Out of Scope

- Nemotron 3 / any LLM provider migration (separate future spec).
- Long-press or alternative button gestures.
- Transcript rendering to Markdown/HTML or any post-processing.
- Changes to STT, TTS, audio device handling, or the observability/Postgres stack.

## Success Criteria

- Pressing end during any phase of a session reliably ends it within ~1 s, says
  "Goodbye." once, and never self-restarts (covers User Story 1).
- A session survives arbitrary silence with zero spoken output and no auto-end
  (covers User Story 2).
- Every session yields a complete, durable JSONL transcript with full replies and tool
  calls + results, surviving a mid-session crash (covers User Story 3).

## Open Questions

- None blocking. The `SESSION_MAX_SECONDS` safety cap default (disabled vs. a large
  value like 1800 s) can be decided during implementation; default proposed: disabled.
