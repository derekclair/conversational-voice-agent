# AGENTS.md

Guidance for AI coding agents working in this repository. (Human contributors:
see `README.md` for the overview and `CLAUDE.md` for a concise orientation.)

## Project in one line

A personal, fully-local, button-driven voice agent on an NVIDIA DGX Spark:
Parakeet STT → optional `thelab` LangGraph brain → Piper TTS, with a Lenovo Go
speakerphone for I/O.

## Working here

- Python 3.11, run from a local `.venv`. There is no `pyproject.toml`; run code
  as `python -m local_tts.<module>`.
- Fastest sanity check: `make smoke` (LED + Piper TTS only — no `thelab`, no API keys).
- Tests: `python -m pytest -q`. They are hardware-free and must stay that way.
- Keep changes modest and honest — this is a learning spike, not a product.
- Never commit secrets. The observability stack reads credentials from
  `observability/.env` (see `observability/.env.example`); telemetry export is
  opt-in and content-free.
