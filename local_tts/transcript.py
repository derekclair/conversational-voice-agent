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
