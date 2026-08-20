"""Content-free OpenTelemetry metrics export for the voice agent.

Ships per-turn *latency* metrics (durations + counts only) to a LAN
OpenTelemetry collector over OTLP/HTTP. This is a **tier-A / content-free**
exporter by construction: it accepts numbers, not text. Transcript content
(user speech, agent replies) is never passed to, or emitted by, this module —
only durations, counts, and low-cardinality labels leave the host.

It is deliberately fail-safe: if the OTEL SDK is missing or the collector is
unreachable, every call becomes a no-op. Export happens on a background thread,
so recording a metric never blocks the voice pipeline.

Export is **opt-in**: it is a no-op unless a collector endpoint is configured
via one of (first match wins):
    OTEL_EXPORTER_OTLP_ENDPOINT   (standard OTEL base, "/v1/metrics" appended)
    VOICE_OTEL_ENDPOINT           (base URL)

e.g. VOICE_OTEL_ENDPOINT=http://collector-host:4318. With neither set, _init()
returns False and every record_event/force_flush call is a harmless no-op
(no host is baked into the code).
"""

from __future__ import annotations

import os
import threading
from typing import Any

# Only these numeric keys are ever read from an event payload. Anything else
# (user_text, agent_reply, message, ...) is ignored — content cannot leak here.
_TURN_MS_KEYS = ("asr_ms", "agent_ms", "tts_ms", "total_ms", "eou_ms", "tts_ttfa_ms")

DEVICE_ID = os.environ.get("DEVICE_ID", "dgx-spark-01")
SERVICE_NAME = "conversational-voice-agent"

_lock = threading.Lock()
_initialized = False
_enabled = False
_provider = None
_hist: dict[str, Any] = {}
_counters: dict[str, Any] = {}


def _endpoint() -> str | None:
    """Resolve the metrics endpoint from env, or None if none is configured."""
    base = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get(
        "VOICE_OTEL_ENDPOINT"
    )
    if not base:
        return None
    return base.rstrip("/") + "/v1/metrics"


def _init() -> bool:
    """Lazily build the meter provider. Returns True if export is live."""
    global _initialized, _enabled, _provider, _hist, _counters
    if _initialized:
        return _enabled
    with _lock:
        if _initialized:
            return _enabled
        _initialized = True
        if os.environ.get("VOICE_OTEL_DISABLED") == "1":
            return False
        endpoint = _endpoint()
        if endpoint is None:
            # Export is strictly opt-in: no endpoint env set means no-op.
            return False
        try:
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )

            resource = Resource.create(
                {
                    "service.name": SERVICE_NAME,
                    "host.name": DEVICE_ID,
                }
            )
            exporter = OTLPMetricExporter(endpoint=endpoint)
            # Short interval so a benchmark session shows up in Grafana quickly.
            interval_ms = int(os.environ.get("VOICE_OTEL_INTERVAL_MS", "5000"))
            reader = PeriodicExportingMetricReader(
                exporter, export_interval_millis=interval_ms
            )
            _provider = MeterProvider(resource=resource, metric_readers=[reader])
            meter = _provider.get_meter("voice_agent")

            for key in _TURN_MS_KEYS:
                _hist[key] = meter.create_histogram(
                    name=f"voice.turn.{key}",
                    unit="ms",
                    description=f"Per-turn {key} latency",
                )
            _counters["turns"] = meter.create_counter(
                "voice.turns_total", description="Completed conversation turns"
            )
            _counters["errors"] = meter.create_counter(
                "voice.errors_total", description="Voice pipeline errors"
            )
            _counters["cancelled"] = meter.create_counter(
                "voice.turns_cancelled_total",
                description="Turns cancelled by button press",
            )
            _enabled = True
        except Exception:
            _enabled = False
    return _enabled


def record_event(event_type: str, payload: dict[str, Any]) -> None:
    """Content-free hook, called from telemetry.emit().

    Reads only known numeric keys from ``payload``; text keys are ignored.
    """
    if not _init():
        return
    try:
        labels = {"host": DEVICE_ID}
        if event_type == "turn_complete":
            recorded = False
            for key in _TURN_MS_KEYS:
                val = payload.get(key)
                if isinstance(val, (int, float)):
                    _hist[key].record(float(val), labels)
                    recorded = True
            if recorded:
                _counters["turns"].add(1, labels)
        elif event_type == "error":
            _counters["errors"].add(1, labels)
        elif event_type == "speech_cancelled":
            _counters["cancelled"].add(1, labels)
    except Exception:
        pass  # never disrupt the voice loop for a metrics failure


def force_flush(timeout_ms: int = 5000) -> None:
    """Flush pending metrics (call at shutdown or after a benchmark run)."""
    if not _enabled or _provider is None:
        return
    try:
        _provider.force_flush(timeout_millis=timeout_ms)
    except Exception:
        pass
