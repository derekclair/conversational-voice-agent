# CLAUDE.md — Contributor / agent notes

Short orientation for anyone (human or AI) working in this repo. For the
full project overview, read `README.md`.

## What this is

A personal, for-fun learning project: a **button-driven, fully-local voice
agent** that runs on an NVIDIA DGX Spark and turns a Lenovo Go Wired
Speakerphone into a small physical assistant. Press the Teams button, speak,
hear a reply through the speaker.

It is a spike / prototype, not a distributable package — there is no
`pyproject.toml` here and the code runs from a local `.venv`.

- **STT**: Parakeet TDT 0.6B via NeMo (CPU inference).
- **Brain**: the `thelab` LangGraph agent (a companion repo), optional.
- **TTS**: Piper (`en_US-amy-medium`) with an espeak-ng fallback.

## Architecture

```
Teams button (BTN_0, evdev)
    → button_listener.py → /tmp/voice_trigger (named pipe)
        → voice_loop.py (main loop)
            ├─ arecord (plughw:1,0) → WAV → Parakeet STT (NeMo, CPU)
            ├─ thelab LangGraph agent (optional brain)
            ├─ Piper TTS → aplay (plughw:1,0)
            ├─ LED control (HID report 0x05)
            └─ telemetry (local JSONL + optional HTTP)
        → Lenovo Go speaker (plughw:1,0, half-duplex USB audio)
```

Button press toggles a session on/off. LED solid = ready/listening. Multiple
turns happen within a session. The audio device is **half-duplex**: it cannot
record and play at the same time, so there is a settle delay between `arecord`
and `aplay`, and `_robust_aplay` retries on "device busy".

## Key files

| File | Purpose |
|------|---------|
| `local_tts/voice_loop.py` | Main loop: record → STT → agent → TTS → LED. |
| `local_tts/button_listener.py` | Reads the Teams button, writes to the trigger pipe. |
| `local_tts/led_control.py` | Lenovo Go LED via HID report 0x05. |
| `local_tts/play_tone.py` | Diagnostic tones / volume-tick feedback. |
| `local_tts/telemetry.py` | Structured event emitter (JSONL + optional HTTP). |
| `local_tts/otel_export.py` | Opt-in, content-free OTLP latency metrics (off unless an endpoint env is set). |
| `local_tts/text_chunk.py` | Sentence splitting so Piper can start the first audio chunk early. |
| `Makefile` | All run/install/cleanup targets — read the top comment block. |
| `systemd/` | User services for the voice loop and button listener. |
| `observability/` | Optional Docker Compose stack (Postgres + Loki + Grafana + ingest). |
| `specs/` | Design specs for the spike (001–003, 005). |

## How to run

```bash
make smoke     # LED + Piper TTS sanity check — standalone, no thelab, no keys
make           # full voice agent (needs the ../thelab brain + keys)
make ollama    # same, but with a local Ollama model
make button    # (2nd terminal) start the Teams button listener
make install   # install as systemd user services
```

`make smoke` is the quickest thing to try and needs no API keys and no
companion repo. The full `make` target installs `../thelab` as an editable
dependency and reads LLM keys from your environment / `.env`.

## Gotchas

- Half-duplex USB audio on `plughw:1,0` is the biggest constraint; use
  `make audio-reset` when playback gets stuck on "device busy".
- On DGX Spark there is no `nvidia-smi`, and `torch.cuda.is_available()` may
  return `False` even with CUDA present — don't rely on it.
- Ollama runs on port `11434`.
- Piper voice model expected at `~/.local/share/piper-voices/en_US-amy-medium.onnx`.
- Python 3.11, local `.venv` only; run modules as `python -m local_tts.<mod>`.
