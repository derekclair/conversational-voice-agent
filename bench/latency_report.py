#!/usr/bin/env python3
"""Per-turn latency report for the conversational voice agent.

Turns raw telemetry into the Markdown latency table used in the README.

Two data sources are supported:

  --jsonl PATH
      Read the local telemetry JSONL fallback written by
      ``local_tts/telemetry.py`` (default location
      ``/tmp/local-tts-telemetry/events-*.jsonl``). Every line is a JSON
      event; this tool keeps ``event_type == "turn_complete"`` records and
      reads the numeric fields ``payload.asr_ms``, ``payload.agent_ms``,
      ``payload.tts_ms`` and ``payload.total_ms``. It then computes
      count / p50 / p95 / min / max for each metric and prints a Markdown
      table. This is the exact-percentile path (all raw samples are present).

  --prometheus URL
      Query a Prometheus server (e.g. one fed by an OpenTelemetry Collector
      that the agent ships to via ``VOICE_OTEL_ENDPOINT``). The OTLP
      histograms ``voice.turn.<metric>`` surface in Prometheus as
      ``voice_turn_<metric>_milliseconds`` with ``_count`` / ``_sum`` /
      ``_bucket`` series. From these we can report an exact count and mean,
      plus an *approximate* p50/p95 via ``histogram_quantile`` over the
      cumulative buckets. Degrades gracefully (prints a note) if the server
      is unreachable or has no data.

Only the Python standard library is used (json, statistics, argparse,
urllib, glob, os, sys). No third-party dependencies.

Examples:
    python bench/latency_report.py --jsonl /tmp/local-tts-telemetry/events-dgx-spark-01.jsonl
    python bench/latency_report.py                       # default JSONL glob
    python bench/latency_report.py --prometheus http://prom-host:9090
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request

# The four per-turn latency metrics emitted in the "turn_complete" telemetry
# event (see local_tts/voice_loop.py) and as OTLP histograms (otel_export.py).
METRICS = ("asr_ms", "agent_ms", "tts_ms", "total_ms", "eou_ms", "tts_ttfa_ms")

DEFAULT_JSONL_GLOB = "/tmp/local-tts-telemetry/events-*.jsonl"


# --- Statistics helpers ------------------------------------------------------

def _percentile(values, pct):
    """Linear-interpolation percentile of a list of numbers.

    ``pct`` is in [0, 100]. Returns None for an empty list. Matches the
    common "linear" method (same as numpy's default).
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def summarize(values):
    """Return count/p50/p95/min/max for a list of numeric samples."""
    if not values:
        return {"count": 0, "p50": None, "p95": None, "min": None, "max": None}
    return {
        "count": len(values),
        "p50": _percentile(values, 50),
        "p95": _percentile(values, 95),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
    }


# --- Markdown rendering ------------------------------------------------------

def _fmt(value):
    """Format a millisecond value for the table (int-ish), or a dash."""
    if value is None:
        return "-"
    return f"{round(float(value))}"


def render_table(stats_by_metric, columns=("count", "p50", "p95", "min", "max")):
    """Render a Markdown table: one row per metric, given stat columns."""
    header = "| metric | " + " | ".join(columns) + " |"
    divider = "|" + "---|" * (len(columns) + 1)
    lines = [header, divider]
    for metric in METRICS:
        stats = stats_by_metric.get(metric, {})
        cells = []
        for col in columns:
            val = stats.get(col)
            if col == "count":
                cells.append(str(val if val is not None else 0))
            else:
                cells.append(_fmt(val))
        lines.append(f"| {metric} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


# --- JSONL source ------------------------------------------------------------

def _resolve_jsonl_paths(arg):
    """Return the list of JSONL files to read (explicit file or default glob)."""
    if arg:
        # An explicit path may itself be a glob or a single file.
        matches = sorted(glob.glob(arg))
        return matches if matches else [arg]
    return sorted(glob.glob(DEFAULT_JSONL_GLOB))


def load_from_jsonl(paths):
    """Collect per-metric sample lists from turn_complete events in JSONL files.

    Returns (samples_by_metric, files_read, lines_parsed, turns_seen).
    Malformed lines and non-numeric fields are skipped silently.
    """
    samples = {m: [] for m in METRICS}
    files_read = []
    lines_parsed = 0
    turns_seen = 0

    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                files_read.append(path)
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    lines_parsed += 1
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if event.get("event_type") != "turn_complete":
                        continue
                    payload = event.get("payload") or {}
                    turns_seen += 1
                    for metric in METRICS:
                        val = payload.get(metric)
                        if isinstance(val, (int, float)) and not isinstance(val, bool):
                            samples[metric].append(float(val))
        except OSError:
            continue

    return samples, files_read, lines_parsed, turns_seen


def report_jsonl(arg):
    """Build and print the Markdown latency table from JSONL telemetry."""
    paths = _resolve_jsonl_paths(arg)
    if not paths:
        print(f"No telemetry JSONL files matched (looked for: {arg or DEFAULT_JSONL_GLOB}).")
        print("Run a session first, or pass --jsonl PATH. Emitting an empty table:\n")
        print(render_table({m: summarize([]) for m in METRICS}))
        return 0

    samples, files_read, lines_parsed, turns_seen = load_from_jsonl(paths)

    if not files_read:
        print(f"None of the paths were readable: {paths}. Emitting an empty table:\n")
        print(render_table({m: summarize([]) for m in METRICS}))
        return 0

    stats = {m: summarize(samples[m]) for m in METRICS}

    print("### Per-turn latency (from local telemetry JSONL)\n")
    print(render_table(stats))
    print()
    print(f"_Source: {', '.join(files_read)}_")
    print(f"_Parsed {lines_parsed} event line(s); {turns_seen} turn_complete event(s)._")
    if turns_seen == 0:
        print("_No `turn_complete` events found — run a real session to capture latencies._")
    return 0


# --- Prometheus source -------------------------------------------------------

def _prom_query(base_url, expr, timeout=5.0):
    """Run a Prometheus instant query, returning the parsed 'result' list.

    Returns None on any transport/parse error (caller degrades gracefully).
    """
    url = base_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    if not isinstance(data, dict) or data.get("status") != "success":
        return None
    return data.get("data", {}).get("result", [])


def _prom_scalar(result):
    """Extract a single float from an instant-query result list, or None."""
    if not result:
        return None
    try:
        value = result[0]["value"][1]
        num = float(value)
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    # Prometheus returns "NaN" for empty histogram_quantile; guard against it.
    if math.isnan(num):
        return None
    return num


def report_prometheus(base_url):
    """Query Prometheus for the voice.turn histograms and print a table."""
    print(f"### Per-turn latency (from Prometheus at {base_url})\n")

    stats = {}
    reachable = False
    approx_note = False
    for metric in METRICS:
        base = f"voice_turn_{metric}_milliseconds"
        count = _prom_scalar(_prom_query(base_url, f"sum({base}_count)"))
        total = _prom_scalar(_prom_query(base_url, f"sum({base}_sum)"))
        p50 = _prom_scalar(
            _prom_query(base_url, f"histogram_quantile(0.5, sum by (le) ({base}_bucket))")
        )
        p95 = _prom_scalar(
            _prom_query(base_url, f"histogram_quantile(0.95, sum by (le) ({base}_bucket))")
        )
        if count is not None:
            reachable = True
        mean = (total / count) if (total is not None and count) else None
        if p50 is not None or p95 is not None:
            approx_note = True
        stats[metric] = {
            "count": int(count) if count is not None else 0,
            "mean": mean,
            "p50": p50,
            "p95": p95,
        }

    if not reachable:
        print("_Prometheus unreachable or no `voice_turn_*` series found._")
        print("_Is the OpenTelemetry Collector receiving data, and is Prometheus scraping it?_")
        print("_Falling back to an empty table:_\n")
        print(render_table({m: summarize([]) for m in METRICS},
                            columns=("count", "mean", "p50", "p95")))
        return 0

    print(render_table(stats, columns=("count", "mean", "p50", "p95")))
    print()
    print("_count and mean are exact (from histogram `_count`/`_sum`)._")
    if approx_note:
        print("_p50/p95 are **approximate**: `histogram_quantile` interpolates within "
              "the cumulative OTLP bucket boundaries. For exact percentiles use --jsonl._")
    return 0


# --- CLI ---------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="latency_report.py",
        description="Render the per-turn voice-agent latency table (Markdown) "
                    "from telemetry JSONL or a Prometheus server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--jsonl",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="Read telemetry JSONL (a file or glob). Omit the value to use the "
             f"default glob {DEFAULT_JSONL_GLOB}.",
    )
    src.add_argument(
        "--prometheus",
        metavar="URL",
        help="Query a Prometheus base URL (e.g. http://host:9090) instead of JSONL.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.prometheus:
        return report_prometheus(args.prometheus)
    # Default to JSONL. args.jsonl is "" when --jsonl passed with no value,
    # None when the flag was omitted entirely; both mean "use the default glob".
    return report_jsonl(args.jsonl)


if __name__ == "__main__":
    sys.exit(main())
