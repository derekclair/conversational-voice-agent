# Feature Specification: Blueprint-Informed Latency & UX (Option A)

**Feature Branch**: `005-blueprint-informed-latency-ux`  
**Created**: 2026-08-18  
**Status**: Ready for implementation  
**Owner**: Derek Clair  

**Input**: Evaluate NVIDIA Nemotron Voice Agent blueprint and improve local Lenovo Go voice loop where Spark-feasible. Direction locked to **Option A — minimal incremental**.

**Related**:
- Blueprint: https://github.com/NVIDIA-AI-Blueprints/nemotron-voice-agent
- Code: `local_tts/voice_loop.py`, `agent_io.py`, `telemetry.py`, `otel_export.py`, `button_listener.py`, `text_chunk.py`
- Prior specs: `003-session-lifecycle-and-transcripts` (cancel/session model)

---

## Overview

Adopt **patterns** from the Nemotron Voice Agent blueprint (measure stages, faster EOU, first-audio TTS, interruptibility) **without** adopting multi-GPU NIM docker or WebRTC as primary UX.

This cycle hardens the existing half-duplex Lenovo Go loop:

1. Complete per-stage latency surface (especially **EOU dead-air** and **TTS time-to-first-audio**).
2. Strengthen cancel so end-session abandons in-flight **agent** work as well as TTS playback.
3. **Sentence-chunked Piper** playback so first audio starts before full reply synthesis finishes.
4. **Shorter / adaptive EOU** (default silence threshold lower than 0.8s; optional Silero behind env flag).
5. Document thin **stage seams** for a future modular extraction (no Pipecat rewrite now).

---

## Current baseline (facts)

| Area | Today |
|------|--------|
| Metrics | `turn_complete` already emits `asr_ms`, `agent_ms`, `tts_ms`, `total_ms`; OTEL histograms + Grafana SQL exist |
| Missing metrics | No `eou_ms` (silence wait after speech); no `tts_ttfa_ms` (time to first audio chunk); `total_ms` starts after record ends (excludes EOU) |
| TTS cancel | `speak(..., stop_event)` kills `aplay` mid-playback; full Piper synth of **entire** reply before first `aplay` |
| Agent cancel | `agent_respond` / `graph.invoke` is **blocking**; stop_event only checked after it returns |
| EOU | Energy VAD, `SILENCE_SECONDS = 0.8` fixed |
| UX | Teams button session toggle; LED; half-duplex USB |

Blueprint anchors (inspiration only): E2E speech-end→bot-start target **600–1500 ms**; streaming/chunked TTS first audio **~150–200 ms** on NIM hardware; barge-in via pipeline interruptions. Full NIM stack needs **2× ~48GB GPUs** — out of scope on Spark.

---

## Goals

1. Emit and document **EOU** and **TTS TTFA** metrics alongside existing stage timers; one real session shows all fields in telemetry JSONL and (when OTEL configured) histograms.
2. On session end (button), **abandon** in-flight agent result and stop TTS within ~1s wall clock after stop (align with 003 intent).
3. For multi-sentence agent replies, **first audio** begins after the first sentence is synthesized, not after the full reply.
4. Default EOU silence deadline is **≤ 0.5s** (configurable), with optional Silero path behind env flag; false-cutoff risk documented.
5. `research.md` + this spec define stage boundaries Capture→ASR→Agent→TTS→Playback without requiring a framework migration.

## Non-Goals

- Multi-container Riva/Magpie/LLM NIM compose as primary path  
- WebRTC / browser as primary desk UX  
- Speculative speech needing interim Riva ASR  
- Magpie TTS hard dependency; Gepard swap (see 004)  
- 49B / 120B+ models  
- Hosted telephony / customer-service product work  
- Full Pipecat rewrite  
- Replacing thelab brain / Supermemory  
- Force-killing OS threads mid-`invoke` (abandon result is sufficient)

---

## User stories

### US1 — See where time goes (P1)

As Derek, I want EOU and TTFA in the same turn telemetry as ASR/agent/TTS so I can tune without guessing.

**Acceptance**:
- `turn_complete` payload includes `eou_ms` and `tts_ttfa_ms` (integers, ms) when a full turn completes.
- `local_tts/otel_export.py` records histograms for the new keys (extend `_TURN_MS_KEYS`).
- Unit or smoke test asserts emit payload keys (mock emit).
- README or `specs/005-.../metrics.md` lists event fields for Grafana.

### US2 — End means end (P1)

As Derek, if I press Teams to end while the agent is “thinking,” the session ends without playing a stale long reply (except optional short “Goodbye.” if 003 is in force).

**Acceptance**:
- After `stop_event` is set during `agent_respond`, the session loop does **not** call `speak(full_reply)` for that turn.
- Telemetry: `turn_cancelled` or existing `speech_cancelled` / new `agent_cancelled` with stage label.
- Best-effort: run agent in a worker future with timeout or post-check `stop_event` before speak (document chosen pattern in tasks).
- No LED stuck-off; services remain ready for next session.

### US3 — Faster first word (P1)

As Derek, long replies start speaking sooner.

**Acceptance**:
- `speak` (or successor) splits text into sentences (`.?!` + length heuristics); synthesizes and plays **per sentence**.
- `tts_ttfa_ms` measured from speak-entry to start of first successful aplay (or first PCM write).
- `stop_event` still aborts mid-chunk and skips remaining sentences.
- Fallbacks: espeak path also chunked if practical; tone fallback unchanged.
- Empty/single-sentence replies still work; no regression on `make smoke` / `make demo`.

### US4 — Less dead air after I stop talking (P2)

As Derek, the agent should start thinking sooner after I finish a phrase.

**Acceptance**:
- Default `SILENCE_SECONDS` ≤ 0.5 (env override `VOICE_SILENCE_SECONDS`).
- Optional `VOICE_VAD=silero|energy` (default energy); Silero optional dependency, fail open to energy.
- `eou_ms` reflects time from last above-threshold speech chunk to record return.
- Document noise tradeoff; no crash if Silero missing.

---

## Functional requirements

### FR1 — Metrics completeness

| Field | Meaning |
|-------|---------|
| `eou_ms` | From last active speech frame to end of `record_until_silence` |
| `asr_ms` | Existing |
| `agent_ms` | Existing |
| `tts_ms` | Full speak wall time (all chunks) |
| `tts_ttfa_ms` | Speak start → first audio out |
| `total_ms` | Prefer speech-end→playback-end **or** document if still post-record only; if redefined, bump schema note |

Emit `eou_ms` even when transcription empty (if recording produced speech then silence).

### FR2 — Cancel / abandon

- Pattern: wrap `agent_respond` so when `stop_event` is set, result is discarded before TTS.
- Prefer `concurrent.futures` with checking stop between stages; do not leave orphaned speak.
- Coordinate with 003 session lifecycle if partially applied — do not reintroduce restart race.

### FR3 — Chunked TTS

- Sentence split utility (pure function, unit-tested).
- Piper: synthesize sentence → wav → aplay → next sentence (reuse `_robust_aplay`).
- Optional micro-gap ≤ 50ms between sentences if needed for USB settle (measure; avoid large gaps).

### FR4 — EOU tuning

- Constants → env-configurable.
- Keep RMS threshold configurable (`VOICE_SILENCE_THRESHOLD` if not already).

### FR5 — Stage seams (design only + light code if natural)

Document interfaces (functions already approximate stages):

```
record_until_silence → transcribe → agent_respond → speak → (LED/session)
```

No new framework required. Optional tiny modules only if coder needs them for tests (`local_tts/text_chunk.py`).

---

## Success metrics (cycle done when)

1. Before/after table in PR description for ≥3 turns: `eou_ms`, `tts_ttfa_ms`, `total_ms`.  
2. Synthetic multi-sentence demo shows TTFA **materially lower** than full-reply synth time (order-of-magnitude: first sentence, not full essay).  
3. Manual: end session during agent think → no full stale reply played.  
4. `make smoke` and existing tests pass; new unit tests for chunker + metric keys.  
5. Spec files merged or PR open with this package.

---

## Touch points (coder)

| Path | Change |
|------|--------|
| `local_tts/voice_loop.py` | EOU timing, cancel-before-speak, chunked speak, env config |
| `local_tts/telemetry.py` | No API break; new payload keys only |
| `local_tts/otel_export.py` | Extend `_TURN_MS_KEYS` |
| `local_tts/text_chunk.py` | **New** (optional) pure split helper |
| `local_tts/agent_io.py` | Only if agent invoke lives here |
| `tests/` | Unit tests chunker + metric contract |
| `README.md` | Env vars + metrics table brief |

Riskiest file: **`voice_loop.py`** (session races + audio).

---

## Dependencies & risks

- Half-duplex USB “busy” with rapid chunk aplay → reuse `_robust_aplay`.  
- Aggressive EOU cutoffs mid-sentence → tune after metrics.  
- Agent cancel cannot hard-kill CUDA/LLM mid-forward; abandon is correct.  
- Architect profile xAI OAuth broken at write time — this spec is orchestrator-authored; review welcome.

---

## Open questions (non-blocking defaults)

| Q | Default |
|---|---------|
| Goodbye utterance on cancel | Keep 003 behavior if present; else silent end OK for this PR |
| Sentence regex language | English-first |
| Silero in default deps | Optional extra; not required to install for green CI |
