import json
from types import SimpleNamespace

from local_tts.transcript import SessionTranscript


def _read(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_writes_header_turn_footer(tmp_path):
    t = SessionTranscript("sess1", base_dir=tmp_path)
    t.start(thread_id="sess1", user_id="u", llm_provider="xai",
            llm_model="grok-3-mini", device_id="dgx-spark-01")
    tool = SimpleNamespace(name="get_time", args={"tz": "UTC"},
                           result="noon", tool_call_id="c1")
    t.record_turn(user_text="hi", reply="hello there", tool_calls=[tool],
                  timing_ms={"asr": 1, "agent": 2, "tts": 3, "total": 6})
    t.end(turns=1, duration_s=12.34, end_reason="button")

    records = _read(t.path)
    assert [r["type"] for r in records] == ["header", "turn", "footer"]
    assert records[0]["llm_model"] == "grok-3-mini"
    assert records[1]["index"] == 1
    assert records[1]["user_text"] == "hi"
    assert records[1]["tool_calls"][0]["name"] == "get_time"
    assert records[1]["tool_calls"][0]["result"] == "noon"
    assert records[2]["end_reason"] == "button"
    assert records[2]["turns"] == 1


def test_reply_is_untruncated(tmp_path):
    long_reply = "x" * 2000
    t = SessionTranscript("sess2", base_dir=tmp_path)
    t.start(thread_id="sess2", user_id="u", llm_provider="p",
            llm_model="m", device_id="d")
    t.record_turn(user_text="q", reply=long_reply, tool_calls=[],
                  timing_ms={"asr": 0, "agent": 0, "tts": 0, "total": 0})
    records = _read(t.path)
    assert len(records[1]["reply"]) == 2000


def test_write_failure_does_not_raise(tmp_path, monkeypatch):
    t = SessionTranscript("sess3", base_dir=tmp_path)

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr("builtins.open", boom)
    # Must not raise even though writing fails.
    t.start(thread_id="x", user_id="u", llm_provider="p", llm_model="m", device_id="d")
    t.record_turn(user_text="q", reply="r", tool_calls=[],
                  timing_ms={"asr": 0, "agent": 0, "tts": 0, "total": 0})
    t.end(turns=0, duration_s=0.0, end_reason="error")
