"""Voice Agent Telemetry Ingest Service.

Receives batches of structured events from the voice agent,
writes them to Postgres (structured) and Loki (searchable logs).
"""

import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
import psycopg
from fastapi import FastAPI, Request
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://voice:voice@localhost:5432/voice_agent")
LOKI_URL = os.environ.get("LOKI_URL", "http://localhost:3100")

db_pool = None
http_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, http_client
    db_pool = psycopg.ConnectionPool(DATABASE_URL, min_size=2, max_size=10)
    http_client = httpx.AsyncClient(timeout=5.0)
    yield
    db_pool.close()
    await http_client.aclose()


app = FastAPI(title="Voice Agent Ingest", lifespan=lifespan)

# --- Track turn numbers per session ---
_turn_counters: dict[str, int] = {}


@app.post("/events")
async def ingest_events(request: Request):
    """Receive a batch of telemetry events from the voice agent."""
    events = await request.json()
    if not isinstance(events, list):
        events = [events]

    for event in events:
        _write_postgres(event)
        await _push_loki(event)

    return {"accepted": len(events)}


@app.get("/health")
async def health():
    return {"status": "ok"}


def _write_postgres(event: dict):
    """Write event to appropriate Postgres tables."""
    event_type = event.get("event_type", "")
    payload = event.get("payload", {})
    session_id = event.get("session_id", "")
    device_id = event.get("device_id", "")
    user_id = event.get("user_id", "")
    timestamp = event.get("timestamp", datetime.utcnow().isoformat())

    try:
        with db_pool.connection() as conn:
            # Always write to events table (full audit trail)
            conn.execute(
                """INSERT INTO events (timestamp, device_id, session_id, user_id, event_type, payload)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (timestamp, device_id, session_id, user_id, event_type, json.dumps(payload)),
            )

            # Structured writes for key event types
            if event_type == "session_start":
                conn.execute(
                    """INSERT INTO sessions (id, device_id, user_id, started_at)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (id) DO NOTHING""",
                    (session_id, device_id, user_id, timestamp),
                )
                _turn_counters[session_id] = 0

            elif event_type == "session_end":
                conn.execute(
                    """UPDATE sessions SET ended_at = %s, turns = %s, duration_s = %s
                       WHERE id = %s""",
                    (timestamp, payload.get("turns", 0), payload.get("duration_s"), session_id),
                )
                _turn_counters.pop(session_id, None)

            elif event_type == "turn_complete":
                turn_num = _turn_counters.get(session_id, 0) + 1
                _turn_counters[session_id] = turn_num
                conn.execute(
                    """INSERT INTO turns (session_id, turn_number, user_text, agent_reply,
                                          asr_ms, agent_ms, tts_ms, total_ms, tool_calls)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        session_id,
                        turn_num,
                        payload.get("user_text", ""),
                        payload.get("agent_reply", ""),
                        payload.get("asr_ms"),
                        payload.get("agent_ms"),
                        payload.get("tts_ms"),
                        payload.get("total_ms"),
                        json.dumps(payload.get("tool_calls", [])),
                    ),
                )

            conn.commit()
    except Exception as e:
        print(f"[INGEST] Postgres error: {e}")


async def _push_loki(event: dict):
    """Push event to Loki as a structured log entry."""
    try:
        labels = {
            "job": "voice_agent",
            "device_id": event.get("device_id", "unknown"),
            "event_type": event.get("event_type", "unknown"),
        }
        if event.get("user_id"):
            labels["user_id"] = event["user_id"]
        if event.get("session_id"):
            labels["session_id"] = event["session_id"]

        ts = event.get("timestamp", datetime.utcnow().isoformat())
        # Loki wants nanosecond epoch strings
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ns = str(int(dt.timestamp() * 1e9))
        except Exception:
            ns = str(int(time.time() * 1e9))

        loki_payload = {
            "streams": [
                {
                    "stream": labels,
                    "values": [[ns, json.dumps(event, default=str)]],
                }
            ]
        }

        await http_client.post(
            f"{LOKI_URL}/loki/api/v1/push",
            json=loki_payload,
            headers={"Content-Type": "application/json"},
        )
    except Exception as e:
        print(f"[INGEST] Loki push error: {e}")
