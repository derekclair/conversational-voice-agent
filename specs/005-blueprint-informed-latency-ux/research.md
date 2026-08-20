# Research link-back — 005-blueprint-informed-latency-ux

## Decision
**Option A** (minimal incremental) locked 2026-08-18 by orchestrator after blueprint gap analysis.

## Research artifacts

Local notes comparing the NVIDIA Nemotron Voice Agent blueprint to this stack
(architecture, ASR/VAD, LLM, TTS, observability, hardware, VRAM). Option A
(minimal incremental) was locked from that comparison.

## Blueprint

https://github.com/NVIDIA-AI-Blueprints/nemotron-voice-agent  
Card: https://build.nvidia.com/nvidia/nemotron-voice-agent

Patterns adopted as **ideas**: stage latency, interruptibility, chunked/streaming first audio, shorter EOU.  
Patterns **rejected as primary**: multi-GPU NIM compose, WebRTC desk UX, Riva speculative speech.

## Baseline code reality (corrects early research overclaim)
- Per-turn `asr_ms` / `agent_ms` / `tts_ms` / `total_ms` **already** emitted and graphed.
- Spec 005 therefore focuses on **EOU + TTFA**, **chunked Piper**, **agent abandon**, **EOU default tune** — not inventing metrics from zero.


