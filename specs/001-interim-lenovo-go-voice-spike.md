# Spike: Lenovo Go Voice Loop — Natural Language Interface Prototype

**Spike ID**: 001-interim-lenovo-go-voice-spike  
**Status**: Implemented (post this work)  
**Date**: 2026-06-12  
**Owner**: Derek Clair  
**Related**: README.md in this repo; `thelab/specs/001-voice-dgx-spark-agent/` (light note added)

## Overview (What This Spike Is)

This is a **lightweight spike** (rapid prototype / proof-of-concept), not a production implementation.

Goal: Get a working local voice I/O loop on the physical Lenovo Go Wired Speaker hardware (mic + speaker + Teams button + LED) so a human can quickly "feel" a natural language interface:

- Press button or `echo start > /tmp/voice_trigger`
- Speak naturally
- Live partial transcripts (liveness / responsive feel)
- The existing agent + Supermemory (on the thelab side) receives the input via a simple seam
- A spoken reply comes back out the same Lenovo Go speaker
- LED gives clear "session active" visual feedback

The spike proves the basic system can work end-to-end on the desk hardware and lets us prototype user interactions before investing in a fuller voice layer (webhook trigger, main orchestrator, barge-in VAD, heavy models, docker, etc.).

## Locked Decisions for the Spike (from handoff + user)

- **Trigger**: Named pipe `/tmp/voice_trigger` (also driven by physical Teams button via button_listener). A later webhook trigger can reuse the same seam.
- **Window**: Fixed 4-second capture (user request). No variable VAD or full barge-in for this spike.
- **STT**: Streaming NeMo ASR via `riva.client` (real, not mock; `interim_results=True` so partials flow as the user speaks).
- **LED**: Teams light stays active for the duration of the voice session (listen + reply).
- **Agent**: Leverage the *existing* agent harness + Supermemory pieces on the thelab (langgraph) side. The spike does **not** build or replace the brain. The two loose seams are:
  - Input to brain: `send_partial_to_agent()` (or passed callback) + prints for now.
  - Output from brain: `speak(text)` or writing a line to `/tmp/voice_speak`.
- **Lightweight brain**: No 120B-class NIM (it locks the hardware). Use whatever lighter/faster path is already working for the existing agent.
- **No new runtime Python deps** beyond `nvidia-riva-client` (already in the venv).
- **Natural language interface feel** is the success metric: live partials, coherent spoken replies, hardware feedback, fluid back-and-forth even with the artificial 4 s window.

## Current State (After This Work)

- Named pipe trigger + 4 s streaming capture from `plughw:1,0` (arecord) works.
- Real `riva.client` streaming ASR with partial + final transcripts flowing immediately.
- Partial results delivered to `send_partial_to_agent()` as soon as they arrive.
- Teams LED active for the whole session (set in `record_and_stream_asr`, guaranteed off in finally).
- New: `speak(text)` (Riva TTS when the local server has a model, otherwise reliable diagnostic tone fallback on the Go speaker) + `/tmp/voice_speak` pipe + daemon listener.
- Physical Teams button (via `button_listener.py`) now writes to the trigger pipe (starts a real session instead of just a tone).
- E2E prototype path: after STT window, a simple reply derived from the last heard transcript is spoken (easily replaced by the real existing agent when the seams are wired).
- Smoke test (`--smoke`) exercises light + speak(fallback) without hardware.
- Docs updated (this file + README + handoff postscript + light note in thelab 001).

## Architecture (Spike View — Very Small)

```
Trigger (pipe write or Teams button press)
   │
   ▼
record_and_stream_asr (light on)
   │
   ├─► streaming_audio_generator (arecord chunks)
   │
   ├─► StreamingNeMoASR (riva.client, interim_results=True)
   │      partials → send_partial_to_agent (or override)  [seam to existing brain + Supermemory]
   │
   └─ (after 4s window) last transcript → trivial or real reply text
         │
         ▼
      speak(text)  [or write to /tmp/voice_speak]
         │
         ├─► Riva SpeechSynthesisService (when available) → temp wav → aplay on hw:1,0
         └─► fallback: play_diagnostic_tone on the Lenovo Go (still proves speaker path)
   │
   ▼
light off (finally)
```

The only new "coupling" the spike introduces is the two seams above. Everything else (agent logic, Supermemory, any LLM) stays in the existing pieces.

## How to Run (E2E Prototype Feel)

See the updated README.md for full prerequisites and commands.

Quick version:
- `./run_voice_loop.sh`
- In another terminal: `echo "start" > /tmp/voice_trigger` (or run `python -m local_tts.button_listener` and press the Teams button on the Go).
- Speak. Watch live partials. Hear a reply (or tone fallback).
- Separate test: `echo "Hello from the agent side via the speak pipe." > /tmp/voice_speak`

Smoke (no hardware needed):
`PYTHONPATH=. python -m local_tts.voice_loop --smoke`

## Known Limitations (by Design for the Spike)

- Fixed 4 s window (no end-of-speech VAD yet).
- No true barge-in / interrupt-while-speaking.
- TTS falls back to tone if the local Riva server doesn't have a TTS model loaded (still validates the full audio path).
- The "agent" side for pure standalone E2E is a simple mock unless you wire the seams to the real existing thelab agent + Supermemory.
- Hardware-specific (Lenovo Go ALSA devices, specific hidraw, specific input event device).

These are acceptable for a spike whose job is to let us feel the natural interface on the actual device quickly.

## Verification (What "Done" Looks Like)

- Smoke passes.
- On the real Lenovo Go + working local STT server: one button press or pipe write produces live partial text in the log, a spoken reply (or clear tone) comes out the speaker, LED behavior is correct, and a short natural-feeling exchange is possible.
- Docs (README, this file, handoff, thelab 001) are truthful and mention that we are exercising the *existing* agent + Supermemory via minimal seams, not building a new harness.
- No new Python runtime dependencies.

## Relation to thelab 001 and Production

This spike is a parallel, low-commitment vehicle for hardware bring-up and interaction prototyping. It deliberately stays uncommitted to final choices for a fuller voice layer (webhook trigger, main VoiceOrchestrator, models, deployment, etc.). The seams make it easy for the existing brain to participate today.

See the light note added to `thelab/specs/001-voice-dgx-spark-agent/` and the main handoff document for more context.

---

**Paper trail**: this file, README.md, light updates to thelab 001 tasks/spec/plan.
