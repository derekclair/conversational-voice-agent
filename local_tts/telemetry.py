"""Observability telemetry for the voice agent.

Captures structured events (conversation turns, tool calls, timing, errors, etc.)
and ships them async to the remote ingest service. Falls back to local JSONL when
the remote is unreachable.

Usage:
    from local_tts.telemetry import emit, set_session

    set_session(session_id="abc123", user_id="derek")
    emit("turn_start")
    emit("asr_result", text="hello", latency_ms=420)
    emit("agent_response", text="hi there", latency_ms=1200, tool_calls=[...])
"""

import json
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

# --- Config ---
INGEST_URL = os.environ.get("TELEMETRY_URL", "http://localhost:8100/events")
LOCAL_LOG_DIR = Path(os.environ.get("TELEMETRY_LOG_DIR", "/tmp/local-tts-telemetry"))
DEVICE_ID = os.environ.get("DEVICE_ID", "dgx-spark-01")
FLUSH_INTERVAL = 2.0  # seconds between flush attempts
BATCH_SIZE = 50  # max events per HTTP push
QUEUE_MAX = 10000  # drop events if queue backs up this far

# --- State ---
_queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
_session_ctx: dict[str, str] = {"session_id": "", "user_id": ""}
_shipper_started = False
_lock = threading.Lock()


def set_session(session_id: str = "", user_id: str = ""):
    """Set the current session context. Called at session start/end."""
    _session_ctx["session_id"] = session_id
    _session_ctx["user_id"] = user_id


def emit(event_type: str, **payload: Any):
    """Emit a telemetry event. Non-blocking — queues for async shipping."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "device_id": DEVICE_ID,
        "session_id": _session_ctx.get("session_id", ""),
        "user_id": _session_ctx.get("user_id", ""),
        "event_type": event_type,
        "payload": payload,
    }
    try:
        _queue.put_nowait(event)
    except queue.Full:
        pass  # drop rather than block the voice pipeline
    _ensure_shipper()
    _forward_otel(event_type, payload)


def _forward_otel(event_type: str, payload: dict[str, Any]):
    """Mirror a content-free (durations/counts only) view to the OTEL hub.

    Best-effort and lazily imported so the voice loop has no hard dependency on
    the OpenTelemetry SDK. Transcript text in ``payload`` is never read here.
    """
    try:
        from local_tts import otel_export

        otel_export.record_event(event_type, payload)
    except Exception:
        pass


def _ensure_shipper():
    """Start the background shipper thread on first emit."""
    global _shipper_started
    if _shipper_started:
        return
    with _lock:
        if _shipper_started:
            return
        _shipper_started = True
        t = threading.Thread(target=_shipper_loop, daemon=True)
        t.start()


def _shipper_loop():
    """Background thread: drain queue, batch-ship to remote, fallback to local JSONL."""
    LOCAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOCAL_LOG_DIR / f"events-{DEVICE_ID}.jsonl"

    while True:
        time.sleep(FLUSH_INTERVAL)
        batch = _drain_batch()
        if not batch:
            continue

        # Always write to local JSONL (durable fallback)
        _write_local(batch, log_file)

        # Try shipping to remote ingest
        _ship_remote(batch)


def _drain_batch():
    """Pull up to BATCH_SIZE events from the queue."""
    batch = []
    while len(batch) < BATCH_SIZE:
        try:
            batch.append(_queue.get_nowait())
        except queue.Empty:
            break
    return batch


def _write_local(batch, log_file):
    """Append events to local JSONL file."""
    try:
        with open(log_file, "a") as f:
            for event in batch:
                f.write(json.dumps(event, default=str) + "\n")
    except Exception:
        pass  # best effort


def _ship_remote(batch):
    """POST batch of events to the remote ingest service."""
    try:
        body = json.dumps(batch, default=str).encode()
        req = Request(
            INGEST_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=5)
    except (URLError, OSError, Exception):
        pass  # remote unreachable — local JSONL has the data
