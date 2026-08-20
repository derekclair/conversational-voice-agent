# Implementation tasks — 005-blueprint-informed-latency-ux

Ordered for dgx-coder. One PR preferred; split only if review size hurts.

Branch: `005-blueprint-informed-latency-ux` from main/default.

## T0 — Branch + baseline
1. Create branch.
2. Run `make smoke` (or available tests) and capture one sample `[TURN]` log line as baseline.
3. Read `specs/005-blueprint-informed-latency-ux/spec.md` + research workspace.

## T1 — Metrics: eou_ms + tts_ttfa_ms
1. In `record_until_silence`, track timestamp of last loud chunk; on return compute `eou_ms`.
2. Return eou via tuple or side channel (prefer clean return: e.g. `(pcm, eou_ms)` or small dataclass — update call sites).
3. In `speak`, record `t0`; set `tts_ttfa_ms` when first aplay starts successfully.
4. Extend `turn_complete` emit + `otel_export._TURN_MS_KEYS`.
5. Unit test: mock timers or pure helpers for eou calculation; assert keys on a test double for emit.

## T2 — Sentence chunker
1. Add `local_tts/text_chunk.py` with `split_spoken_sentences(text: str) -> list[str]`.
2. Rules: split on `.?!` followed by space/end; keep abbreviations naive OK for v1; merge tiny trailing fragments; never return empty strings.
3. Unit tests: multi-sentence, single sentence, no punctuation, markdown-ish text.

## T3 — Chunked speak
1. Refactor `speak` to loop chunks: synthesize one sentence → aplay → next.
2. Honor `stop_event` between and during aplay.
3. Preserve Piper → espeak → tone fallback chain.
4. Demo: long multi-sentence string; confirm audio starts before full text would have finished synth (log ttfa).

## T4 — Agent abandon on stop
1. Before `speak(reply)`, if `stop_event.is_set()`, skip speak and emit `agent_cancelled` (or `turn_cancelled` with `stage=agent`).
2. Run `agent_respond` in a way that allows checking stop after completion without blocking teardown forever (Future + wait with short poll, or thread + join timeout). Prefer simple: thread + poll stop_event while waiting; if stop, break session without speak (orphan thread may finish; do not use result).
3. Ensure session `finally` LED path still runs.

## T5 — EOU defaults
1. `SILENCE_SECONDS` default **0.5**; override `VOICE_SILENCE_SECONDS`.
2. Document in README.
3. Optional: `VOICE_VAD=silero` stub or minimal integration **only if** low-risk on aarch64; otherwise leave TODO comment and energy path only.

## T6 — Docs + PR
1. Short README env + metrics section.
2. PR description: before/after table, test plan, link to this spec + research dir.
3. Run tests + smoke.

## Test plan
- `pytest` new unit tests  
- `make smoke`  
- `make demo` if agent keys available  
- Manual: button end during think; multi-sentence speak cancel mid-way  

## Out of scope in this PR
Silero required install, Pipecat, NIM docker, WebRTC, Magpie, speculative ASR.
