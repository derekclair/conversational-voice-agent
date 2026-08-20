"""Metric key contract for turn_complete (spec 005)."""

from local_tts.otel_export import _TURN_MS_KEYS


def test_turn_ms_keys_include_eou_and_ttfa():
    keys = set(_TURN_MS_KEYS)
    assert "asr_ms" in keys
    assert "agent_ms" in keys
    assert "tts_ms" in keys
    assert "total_ms" in keys
    assert "eou_ms" in keys
    assert "tts_ttfa_ms" in keys
