# Metrics contract — 005

## `turn_complete` payload (numeric)

| Key | Unit | Required | Notes |
|-----|------|----------|-------|
| `asr_ms` | ms | yes | Existing |
| `agent_ms` | ms | yes | Existing |
| `tts_ms` | ms | yes | Full speak wall clock |
| `tts_ttfa_ms` | ms | yes (when speak ran) | First audio out |
| `eou_ms` | ms | yes (when VAD ended on silence) | Dead air after last speech |
| `total_ms` | ms | yes | Document definition in PR if changed |

## Other events
| event_type | When |
|------------|------|
| `asr_result` | After STT (`latency_ms`) |
| `tts_end` | After full speak |
| `speech_cancelled` | TTS interrupted by stop |
| `agent_cancelled` | Agent result abandoned due to stop (new) |
| `turn_cancelled` | Optional umbrella with `stage` |

## OTEL
Extend `_TURN_MS_KEYS` in `local_tts/otel_export.py` with `eou_ms`, `tts_ttfa_ms`.  
Content-free: never put transcript text into OTEL attributes.
