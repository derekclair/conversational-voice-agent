"""Diagnostic tone generator and player for Lenovo Go Wired Speaker (hw:1,0).

Uses pure Python stdlib (wave, math, struct) to generate a distinct new tone (1000Hz sine, short duration).
Plays via aplay on the specified ALSA device. No external Python deps required.
This serves as the default/minimal audio interface for now.

Example usage:
    from local_tts.play_tone import play_diagnostic_tone
    play_diagnostic_tone()

CLI:
    python -m local_tts.play_tone
"""

import wave
import struct
import math
import subprocess
import tempfile
import os
import argparse
import time


def generate_tone_wav(filename: str, freq: float = 1000.0, duration: float = 0.75,
                      sample_rate: int = 44100, amplitude: float = 0.6) -> None:
    """Generate a simple sine wave WAV file (stereo, 16-bit). Distinct 'new tone' at 1000Hz.
    Stereo to ensure compatibility with Lenovo Go Wired Speaker.
    """
    n_samples = int(sample_rate * duration)
    with wave.open(filename, 'w') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        for i in range(n_samples):
            t = i / sample_rate
            # Sine wave (same on both channels)
            value = int(amplitude * 32767 * math.sin(2 * math.pi * freq * t))
            wf.writeframes(struct.pack('<hh', value, value))


def play_diagnostic_tone(device: str = 'plughw:1,0', freq: float = 1000.0, duration: float = 0.75) -> None:
    """Generate and play a distinct diagnostic tone on the Lenovo Go speaker via ALSA."""
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        fname = tmp.name
    try:
        generate_tone_wav(fname, freq=freq, duration=duration)
        # Retry a few times on "busy" (common right after arecord on the Lenovo Go USB device).
        # This mirrors the spirit of _robust_aplay in the main voice loop without creating a dep.
        last_err = None
        for attempt in range(6):
            try:
                result = subprocess.run(
                    ['aplay', '-D', device, '-q', fname],
                    check=True,
                    capture_output=True,
                    text=True
                )
                print(f"Played diagnostic tone (freq={freq}Hz, dur={duration}s) on {device}")
                if result.stdout:
                    print(result.stdout)
                last_err = None
                break
            except subprocess.CalledProcessError as e:
                last_err = e
                stderr = (e.stderr or '').lower()
                if 'busy' in stderr and attempt < 5:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                break
        if last_err:
            print(f"Error playing tone: {last_err}")
            print(f"stderr: {last_err.stderr}")
            # Do not re-raise so callers like the speak pipe listener don't treat it as fatal
            # (the device may be temporarily busy or other transient issues)
    except Exception as e:
        print(f"Error playing tone: {e}")
    finally:
        if os.path.exists(fname):
            os.unlink(fname)


def main():
    parser = argparse.ArgumentParser(description="Play diagnostic tone on Lenovo Go Wired Speaker")
    parser.add_argument('--device', default='hw:1,0', help='ALSA device (default: hw:1,0 for Lenovo Go)')
    parser.add_argument('--freq', type=float, default=1000.0, help='Tone frequency in Hz')
    parser.add_argument('--duration', type=float, default=0.75, help='Tone duration in seconds')
    args = parser.parse_args()
    play_diagnostic_tone(device=args.device, freq=args.freq, duration=args.duration)


if __name__ == '__main__':
    main()
