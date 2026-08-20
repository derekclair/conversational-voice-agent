#!/usr/bin/env python3
"""Lenovo Go Voice Spike (the current prototype implementation).

This module is the single source of truth for the spike:
- Named-pipe or physical Teams button trigger
- 4 s arecord from plughw:1,0 (Lenovo Go USB audio)
- Streaming Parakeet TDT 0.6B v3 (direct nemo.collections.asr, CPU path for this spike)
- Real partial transcripts delivered to send_partial_to_agent
- thelab LangGraph agent (Supermemory-enabled) invoked via get_agent()
- Reply spoken via speak() (espeak-ng + _robust_aplay on plughw:1,0 + LED)
- Graceful fallbacks everywhere (stub STT, mock agent, tone) so you can always make progress

The recommended way to run is via the Makefile (after `make` in ../thelab
and `make keys` here, then edit the project-local `.env`). That gives you
correct venv isolation, the cross-repo editable thelab install, key wiring,
and the audio-reset helpers.

See the top of the Makefile and README.md for the two-repo flow
and what "voice moving through the system" looks like in the logs
([Parakeet] Partial → non-default [HEARD] → real [AGENT] success → audible reply).
"""

import math
import os
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from local_tts.led_control import set_teams_light
from local_tts.telemetry import emit as telem, set_session as telem_session
from local_tts.text_chunk import split_spoken_sentences

# --- Config ---
PIPE_PATH = "/tmp/voice_trigger"
AUDIO_DEVICE = "plughw:1,0"
SAMPLE_RATE = 16000
PIPER_VOICE_PATH = os.path.expanduser("~/.local/share/piper-voices/en_US-amy-medium.onnx")
SILENCE_THRESHOLD = int(os.environ.get("VOICE_SILENCE_THRESHOLD", "300"))  # int16 RMS
# Default 0.5s EOU (spec 005); override with VOICE_SILENCE_SECONDS
SILENCE_SECONDS = float(os.environ.get("VOICE_SILENCE_SECONDS", "0.5"))
MAX_RECORD_SECONDS = 30   # safety cap
SETTLE_TIME = 0.3         # wait after arecord before aplay (USB audio quirk; _robust_aplay retries cover the rest)
USER_ID = os.getenv("DEFAULT_USER_ID", "sm_project_default")
MAX_IDLE_TURNS = 3        # end session after N consecutive no-speech turns


# --- Audio Recording with VAD ---

def record_until_silence(stop_event=None):
    """Record until speech followed by silence.

    Returns ``(pcm_bytes, eou_ms)`` when speech was captured, or ``(None, eou_ms)``
    if no speech / aborted. ``eou_ms`` is ms from last above-threshold chunk to
    return (0 if never spoke or aborted mid-speech without silence).
    """
    cmd = [
        "arecord", "-D", AUDIO_DEVICE,
        "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1",
        "-t", "raw", "--buffer-time=50000", "-q",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    chunk_ms = 100
    chunk_bytes = SAMPLE_RATE * 2 * chunk_ms // 1000
    audio_data = bytearray()
    speech_started = False
    silent_chunks = 0
    chunks_for_silence = max(1, int(SILENCE_SECONDS * 1000 / chunk_ms))
    max_chunks = int(MAX_RECORD_SECONDS * 1000 / chunk_ms)
    last_speech_t: Optional[float] = None
    eou_ms = 0

    try:
        for _ in range(max_chunks):
            if stop_event and stop_event.is_set():
                break

            chunk = proc.stdout.read(chunk_bytes)
            if not chunk:
                break

            samples = struct.unpack(f"<{len(chunk) // 2}h", chunk)
            rms = math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0

            if rms > SILENCE_THRESHOLD:
                speech_started = True
                silent_chunks = 0
                last_speech_t = time.perf_counter()
                audio_data.extend(chunk)
            elif speech_started:
                silent_chunks += 1
                audio_data.extend(chunk)
                if silent_chunks >= chunks_for_silence:
                    if last_speech_t is not None:
                        eou_ms = int((time.perf_counter() - last_speech_t) * 1000)
                    break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()

    if speech_started and eou_ms == 0 and last_speech_t is not None:
        # Cap/abort path: still report elapsed since last speech if any
        eou_ms = int((time.perf_counter() - last_speech_t) * 1000)

    if speech_started:
        return bytes(audio_data), eou_ms
    return None, eou_ms

def _save_wav(pcm_data, path):
    """Write raw 16-bit mono PCM to a WAV file."""
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)


# --- STT (Parakeet TDT 0.6B via NeMo) ---

_asr_model = None


def _load_asr():
    """Load Parakeet model once, reuse across sessions."""
    global _asr_model
    if _asr_model is not None:
        return _asr_model
    import nemo.collections.asr as nemo_asr

    print("[ASR] Loading nvidia/parakeet-tdt-0.6b-v3 (first run downloads ~2.5 GB)...")
    _asr_model = nemo_asr.models.ASRModel.from_pretrained("nvidia/parakeet-tdt-0.6b-v3")
    _asr_model.eval()
    print("[ASR] Model ready.")
    return _asr_model


def transcribe(pcm_data):
    """Transcribe raw PCM bytes to text string."""
    model = _load_asr()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp = f.name
    _save_wav(pcm_data, tmp)

    try:
        results = model.transcribe([tmp], batch_size=1, verbose=False)
        if not results:
            return ""
        text = results[0]
        if hasattr(text, "text"):
            text = text.text
        return (text or "").strip()
    finally:
        os.unlink(tmp)


# --- Agent (thelab LangGraph + Supermemory) ---

_agent = None


def _get_agent():
    """Get or create the thelab agent (cached)."""
    global _agent
    if _agent is not None:
        return _agent
    from thelab_langchain.agent.graph import get_agent

    _agent = get_agent(user_id=USER_ID)
    return _agent


def agent_respond(messages, thread_id):
    """Send message history to the agent, return reply string."""
    t0 = time.perf_counter()
    telem("agent_request", thread_id=thread_id, message_count=len(messages),
          user_text=getattr(messages[-1], "content", "") if messages else "")
    try:
        graph = _get_agent()
        result = graph.invoke({
            "messages": messages,
            "user_id": USER_ID,
            "thread_id": thread_id,
        })
    except Exception as e:
        telem("error", stage="agent", message=str(e))
        err = str(e).lower()
        if "connection" in err or "refused" in err:
            raise RuntimeError(
                "LLM server unreachable. Check LLM_BASE_URL in .env "
                "or switch LLM_PROVIDER to 'xai'."
            ) from None
        raise

    latency_ms = int((time.perf_counter() - t0) * 1000)
    if isinstance(result, dict) and "messages" in result:
        last = result["messages"][-1]
        reply = getattr(last, "content", str(last))
        # Capture any tool calls from intermediate messages
        tool_calls = []
        for msg in result["messages"]:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append({"name": tc.get("name", ""), "args": tc.get("args", {})})
        telem("agent_response", reply=reply[:500], latency_ms=latency_ms,
              tool_calls=tool_calls, reply_length=len(reply))
        return reply
    reply = str(result)
    telem("agent_response", reply=reply[:500], latency_ms=latency_ms, reply_length=len(reply))
    return reply


# --- TTS (Piper neural voice → espeak-ng fallback → diagnostic tone) ---

_tts_voice = None


def _load_tts():
    """Load Piper voice model once, reuse across sessions."""
    global _tts_voice
    if _tts_voice is not None:
        return _tts_voice
    if not os.path.exists(PIPER_VOICE_PATH):
        print(f"[TTS] Piper voice not found at {PIPER_VOICE_PATH}, using espeak-ng")
        return None
    from piper import PiperVoice

    print("[TTS] Loading Piper voice (en_US-amy-medium)...")
    _tts_voice = PiperVoice.load(PIPER_VOICE_PATH)
    print("[TTS] Piper voice ready.")
    return _tts_voice


def _robust_aplay(fname, stop_event=None, max_tries=8):
    """aplay with retry for USB audio 'device busy' errors. Interruptible via stop_event."""
    for attempt in range(max_tries):
        try:
            proc = subprocess.Popen(
                ["aplay", "-D", AUDIO_DEVICE, "-q", fname],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            # Poll so we can check stop_event for cancellation
            while proc.poll() is None:
                if stop_event and stop_event.is_set():
                    proc.terminate()
                    proc.wait(timeout=1)
                    return  # cancelled
                time.sleep(0.05)
            if proc.returncode != 0:
                stderr = (proc.stderr.read() or b"").decode(errors="ignore").lower()
                if "busy" in stderr and attempt < max_tries - 1:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise subprocess.CalledProcessError(proc.returncode, "aplay", stderr=proc.stderr.read())
            return  # success
        except subprocess.CalledProcessError:
            if attempt >= max_tries - 1:
                raise


def _synthesize_to_wav(text: str, fname: str, voice) -> None:
    """Write one utterance to *fname* via Piper or espeak-ng."""
    if voice is not None:
        chunks = list(voice.synthesize(text))
        audio_bytes = b"".join(c.audio_int16_bytes for c in chunks)
        with wave.open(fname, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(voice.config.sample_rate)
            wf.writeframes(audio_bytes)
    else:
        subprocess.run(
            ["espeak-ng", "-v", "en-us", "-s", "160", "-w", fname, text],
            check=True, capture_output=True,
        )


def speak(text, stop_event=None):
    """Synthesize and play text; sentence-chunked for lower time-to-first-audio.

    Returns ``tts_ttfa_ms`` (ms from entry to first successful aplay start), or
    ``None`` if no audio was started (cancel/failure before first play).
    """
    t_speak0 = time.perf_counter()
    ttfa_ms = None
    sentences = split_spoken_sentences(text)
    if not sentences:
        return None

    voice = _load_tts()
    try:
        for sentence in sentences:
            if stop_event and stop_event.is_set():
                return ttfa_ms

            fname = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    fname = f.name
                _synthesize_to_wav(sentence, fname, voice)

                if stop_event and stop_event.is_set():
                    return ttfa_ms

                if ttfa_ms is None:
                    ttfa_ms = int((time.perf_counter() - t_speak0) * 1000)

                _robust_aplay(fname, stop_event=stop_event)
            except Exception as e:
                print(f"[TTS] Failed chunk: {e}")
                if ttfa_ms is None:
                    from local_tts.play_tone import play_diagnostic_tone
                    play_diagnostic_tone(device=AUDIO_DEVICE)
                    ttfa_ms = int((time.perf_counter() - t_speak0) * 1000)
                # continue remaining sentences only if not hard-failed first chunk
            finally:
                if fname and os.path.exists(fname):
                    try:
                        os.unlink(fname)
                    except OSError:
                        pass

            if stop_event and stop_event.is_set():
                return ttfa_ms
    except Exception as e:
        print(f"[TTS] Failed: {e}")
        if ttfa_ms is None:
            from local_tts.play_tone import play_diagnostic_tone
            play_diagnostic_tone(device=AUDIO_DEVICE)
            ttfa_ms = int((time.perf_counter() - t_speak0) * 1000)
    return ttfa_ms


def agent_respond_cancellable(messages, thread_id, stop_event=None):
    """Run agent_respond on a worker thread; abandon result if stop_event is set.

    Returns reply str, or None if cancelled / should not be spoken.
    Does not block session teardown on a long in-flight invoke (executor
    shut down with wait=False).
    """
    if stop_event is not None and stop_event.is_set():
        return None

    executor = ThreadPoolExecutor(max_workers=1)
    fut = executor.submit(agent_respond, messages, thread_id)
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                return None
            if fut.done():
                return fut.result()
            time.sleep(0.05)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


# --- Session Management ---

def _ensure_pipe():
    if not os.path.exists(PIPE_PATH):
        os.mkfifo(PIPE_PATH)


def _pipe_reader(session_event, stop_event):
    """Background thread: read pipe triggers, toggle session state."""
    while True:
        try:
            with open(PIPE_PATH, "r") as pipe:
                for line in pipe:
                    if not line.strip():
                        continue
                    if session_event.is_set():
                        # Session active → end it
                        stop_event.set()
                        session_event.clear()
                    else:
                        # Idle → start session
                        stop_event.clear()
                        session_event.set()
        except Exception:
            time.sleep(0.1)


def _session_loop(stop_event):
    """Run a multi-turn conversation until stop_event is set or idle timeout."""
    from langchain_core.messages import AIMessage, HumanMessage

    thread_id = f"lenovo-go-{uuid.uuid4().hex[:8]}"
    messages = []
    idle_count = 0
    session_start_time = time.perf_counter()

    telem_session(session_id=thread_id, user_id=USER_ID)
    telem("session_start", thread_id=thread_id)
    print(f"\n--- Session started (thread: {thread_id}) ---")

    # Agent greeting — signals session is live
    speak("Ready.")
    time.sleep(SETTLE_TIME)

    try:
        while not stop_event.is_set():
            # Record
            print("[MIC] Listening...")
            pcm, eou_ms = record_until_silence(stop_event=stop_event)

            if stop_event.is_set():
                break

            if not pcm:
                idle_count += 1
                if idle_count >= MAX_IDLE_TURNS:
                    print(f"[SESSION] No speech for {MAX_IDLE_TURNS} turns. Ending.")
                    time.sleep(SETTLE_TIME)
                    speak("Ending session.")
                    break
                continue

            idle_count = 0
            duration = len(pcm) / (SAMPLE_RATE * 2)
            print(f"[MIC] Captured {duration:.1f}s of audio (eou={eou_ms}ms)")
            telem("recording_end", duration_s=round(duration, 2), audio_bytes=len(pcm),
                  eou_ms=eou_ms)
            t_record = time.perf_counter()
            time.sleep(SETTLE_TIME)

            # Transcribe
            print("[ASR] Transcribing...")
            text = transcribe(pcm)
            t_asr = time.perf_counter()
            asr_ms = int((t_asr - t_record) * 1000)
            telem("asr_result", text=text or "", latency_ms=asr_ms,
                  audio_duration_s=round(duration, 2), eou_ms=eou_ms)

            if not text:
                print("[ASR] Empty transcription.")
                time.sleep(SETTLE_TIME)
                speak("Sorry, I couldn't understand that.")
                time.sleep(SETTLE_TIME)
                continue

            print(f'[ASR] "{text}"')

            # Accumulate conversation and call agent (cancellable)
            messages.append(HumanMessage(content=text))
            print("[AGENT] Thinking...")
            try:
                reply = agent_respond_cancellable(messages, thread_id, stop_event=stop_event)
            except Exception as e:
                print(f"[AGENT] Error: {e}")
                telem("error", stage="agent", message=str(e))
                if stop_event.is_set():
                    break
                continue

            t_agent = time.perf_counter()
            if stop_event.is_set() or reply is None:
                print("[SESSION] Agent turn cancelled by stop.")
                telem("agent_cancelled", user_text=text[:200])
                break

            messages.append(AIMessage(content=reply))
            print(f'[AGENT] "{reply[:120]}{"..." if len(reply) > 120 else ""}"')

            # Speak reply (sentence-chunked; interruptible)
            t_tts_start = time.perf_counter()
            tts_ttfa_ms = speak(reply, stop_event=stop_event)
            t_tts = time.perf_counter()
            if stop_event.is_set():
                print("[SESSION] Speech cancelled by button press.")
                telem("speech_cancelled", reply_length=len(reply),
                      tts_ttfa_ms=tts_ttfa_ms)
                break
            tts_ms = int((t_tts - t_tts_start) * 1000)
            if tts_ttfa_ms is None:
                tts_ttfa_ms = tts_ms
            telem("tts_end", latency_ms=tts_ms, text_length=len(reply),
                  tts_ttfa_ms=tts_ttfa_ms)
            time.sleep(SETTLE_TIME)

            # Per-turn latency summary
            agent_ms = int((t_agent - t_asr) * 1000)
            total_ms = int((t_tts - t_record) * 1000)
            print(
                f"[TURN] eou={eou_ms}ms asr={asr_ms}ms agent={agent_ms}ms "
                f"tts={tts_ms}ms ttfa={tts_ttfa_ms}ms total={total_ms}ms"
            )
            telem(
                "turn_complete",
                asr_ms=asr_ms,
                agent_ms=agent_ms,
                tts_ms=tts_ms,
                total_ms=total_ms,
                eou_ms=eou_ms,
                tts_ttfa_ms=tts_ttfa_ms,
                user_text=text,
                agent_reply=reply[:500],
            )

    except Exception as e:
        print(f"[SESSION] Error: {e}")
        telem("error", stage="session", message=str(e))
    finally:
        # Blink LED to confirm session end, then restore to solid (services still running)
        set_teams_light(False)
        time.sleep(0.15)
        set_teams_light(True)
        time.sleep(0.15)
        set_teams_light(False)
        time.sleep(0.15)
        set_teams_light(True)  # back to solid = ready

        turns = sum(1 for m in messages if isinstance(m, HumanMessage))
        session_duration = time.perf_counter() - session_start_time
        telem("session_end", turns=turns, duration_s=round(session_duration, 1))
        telem_session()  # clear session context
        print(f"--- Session ended ({turns} turn{'s' if turns != 1 else ''}) ---\n")


def main():
    _ensure_pipe()

    # Pre-load models so first session is instant
    print("Loading ASR model (one-time)...")
    _load_asr()
    print("Loading TTS voice...")
    _load_tts()

    # LED on = services ready, waiting for session
    set_teams_light(True)
    telem("service_ready")

    session_event = threading.Event()
    stop_event = threading.Event()
    shutdown = threading.Event()

    def _request_shutdown(signum, _frame):
        # Event.wait() with no timeout blocks in C and swallows SIGINT/SIGTERM.
        shutdown.set()
        stop_event.set()

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    threading.Thread(
        target=_pipe_reader, args=(session_event, stop_event), daemon=True
    ).start()

    print("\nVoice agent ready. LED on = awaiting session.")
    print("  Press Teams button to start/end a session.")
    print("  Ctrl+C to shut down.\n")

    try:
        while not shutdown.is_set():
            # Timed wait so the interpreter can run the signal handler.
            session_event.wait(timeout=0.5)
            if shutdown.is_set():
                break
            if not session_event.is_set():
                continue
            if shutdown.is_set():
                break
            stop_event.clear()
            _session_loop(stop_event)
            session_event.clear()
            if shutdown.is_set():
                break
            # Restore LED to solid = ready for next session
            set_teams_light(True)
        print("\nShutting down.")
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        set_teams_light(False)
        telem("service_shutdown")


# --- CLI modes ---

def _demo():
    """Run agent + TTS without mic (test agent integration)."""
    from langchain_core.messages import HumanMessage

    print("=== Demo mode (no mic) ===")
    set_teams_light(True)
    try:
        text = "Hello, what can you help me with today?"
        print(f'[DEMO] Simulated input: "{text}"')
        thread_id = f"demo-{uuid.uuid4().hex[:8]}"
        reply = agent_respond([HumanMessage(content=text)], thread_id)
        print(f'[DEMO] Agent reply: "{reply}"')
        speak(reply)
    except Exception as e:
        print(f"[DEMO] Error: {e}")
    finally:
        set_teams_light(False)
    print("=== Demo complete ===")


def _smoke():
    """Quick test: LED + TTS only."""
    print("[SMOKE] Testing LED + TTS...")
    set_teams_light(True)
    time.sleep(0.2)
    set_teams_light(False)
    speak("Voice agent smoke test passed.")
    print("[SMOKE] OK")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        _demo()
    elif "--smoke" in sys.argv:
        _smoke()
    else:
        main()
