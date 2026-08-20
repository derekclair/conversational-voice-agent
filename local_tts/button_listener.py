#!/usr/bin/env python3
"""Lenovo Go device listener — Teams button + volume control (USB hotplug aware).

Listens for:
  - Teams button (BTN_0) → toggles voice session via /tmp/voice_trigger
  - Volume up/down dial → adjusts ALSA PCM on the Lenovo card + feedback tick

Never exits solely because the device is unplugged. Waits for (re)connect,
re-discovers evdev nodes after settle, and stays under systemd as active.

Run with input-group access to /dev/input (udev rule for VID 17ef / PID a03f).
"""

from __future__ import annotations

import math
import os
import select
import struct
import subprocess
import tempfile
import time
import wave
from typing import Dict, Optional, Tuple

from evdev import InputDevice, ecodes, list_devices

from local_tts.telemetry import emit as telem

# --- Constants ---

PIPE_PATH = "/tmp/voice_trigger"
LENOVO_VID = "17ef"
LENOVO_PID = "a03f"
SETTLE_SECONDS = 1.5  # debounce after udev add/remove / port swap
WAIT_POLL_SECONDS = 1.0  # rediscovery cadence when no pyudev event
SELECT_TIMEOUT = 1.0  # allows periodic liveness checks while bound
VOLUME_STEP_PCT = 4

# Defaults overwritten after discovery (card index can change across ports)
AUDIO_DEVICE = "plughw:1,0"
MIXER_DEVICE = "hw:1"
MIXER_CONTROL = "PCM"


def _find_teams_button() -> Optional[InputDevice]:
    """Find the Lenovo Go input device that has BTN_0 (Teams button).

    The Lenovo Go exposes multiple input devices on the same USB interface.
    Prefer the candidate with the *smallest* key set (pure BTN_0 device).
    """
    candidates = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
            if "Lenovo" not in (dev.name or ""):
                continue
            keys = dev.capabilities().get(ecodes.EV_KEY, [])
            if ecodes.BTN_0 in keys:
                candidates.append((len(keys), path, dev))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort()  # fewest keys first
    _count, _path, dev = candidates[0]
    return dev


def _find_consumer_control() -> Optional[InputDevice]:
    """Find the Lenovo Go Consumer Control device (volume keys)."""
    for path in list_devices():
        try:
            dev = InputDevice(path)
            name = dev.name or ""
            if "Lenovo" in name and "Consumer Control" in name:
                return dev
        except Exception:
            continue
    return None


def discover_input_devices() -> Tuple[Optional[InputDevice], Optional[InputDevice]]:
    """Return (teams_button, volume_control), either may be None."""
    return _find_teams_button(), _find_consumer_control()


def discover_alsa_card() -> Optional[int]:
    """Return ALSA card index for Lenovo Go USB audio, if present."""
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return None
    for line in result.stdout.splitlines():
        # e.g. card 1: Speaker [Lenovo Go Wired Speaker], device 0: USB Audio
        if "Lenovo" not in line and "Speaker" not in line:
            continue
        if not line.strip().lower().startswith("card "):
            continue
        try:
            # "card N: ..."
            n = int(line.split(":")[0].split()[1])
            return n
        except (IndexError, ValueError):
            continue
    return None


def apply_alsa_paths(card: Optional[int]) -> None:
    """Update module-level ALSA device strings for the given card index."""
    global AUDIO_DEVICE, MIXER_DEVICE
    if card is None:
        return
    AUDIO_DEVICE = f"plughw:{card},0"
    MIXER_DEVICE = f"hw:{card}"


def close_input_devices(devices: Dict[int, InputDevice]) -> None:
    for dev in list(devices.values()):
        try:
            dev.close()
        except Exception:
            pass
    devices.clear()


def _set_volume(direction: str) -> int:
    """Adjust ALSA PCM volume. direction: '+' or '-'. Returns new volume percent."""
    try:
        result = subprocess.run(
            [
                "amixer",
                "-D",
                MIXER_DEVICE,
                "set",
                MIXER_CONTROL,
                f"{VOLUME_STEP_PCT}%{direction}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if "%" in line and "Playback" in line:
                start = line.index("[") + 1
                end = line.index("%")
                return int(line[start:end])
    except Exception as e:
        print(f"[VOL] Error: {e}")
    return -1


def _play_tick(volume_pct: int) -> None:
    """Volume feedback: best-effort audio tick when half-duplex device is free."""
    freq = 400 + (volume_pct * 4)
    duration = 0.08
    sample_rate = 22050
    amplitude = 4000
    n_samples = int(sample_rate * duration)

    fname = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            fname = f.name
        with wave.open(fname, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            for i in range(n_samples):
                t = i / sample_rate
                env = min(1.0, i / 200) * min(1.0, (n_samples - i) / 200)
                sample = int(amplitude * env * math.sin(2 * math.pi * freq * t))
                wf.writeframes(struct.pack("<h", sample))

        subprocess.run(
            ["aplay", "-D", AUDIO_DEVICE, "-q", fname],
            capture_output=True,
            timeout=2,
        )
    except Exception:
        pass
    finally:
        if fname and os.path.exists(fname):
            try:
                os.unlink(fname)
            except OSError:
                pass


def _toggle_session() -> None:
    try:
        with open(PIPE_PATH, "w") as pipe:
            pipe.write("start\n")
        print("[BTN] Teams button → session toggle")
        telem("button_press", action="session_toggle")
    except Exception as ex:
        print(f"[BTN] Pipe write failed: {ex}")


def _attr(device, *keys: str) -> str:
    for key in keys:
        try:
            val = device.get(key)
            if val:
                return str(val).lower()
        except Exception:
            pass
        try:
            raw = device.attributes.get(key)
            if raw is not None:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                return str(raw).lower()
        except Exception:
            pass
    return ""


def _is_lenovo_usb_device(device) -> bool:
    """True if a pyudev Device looks like our Lenovo Go USB node."""
    try:
        vid = _attr(device, "ID_VENDOR_ID", "idVendor")
        pid = _attr(device, "ID_MODEL_ID", "idProduct")
        if vid == LENOVO_VID and pid == LENOVO_PID:
            return True
        parent = device.find_parent("usb", "usb_device")
        if parent is not None:
            vid = _attr(parent, "ID_VENDOR_ID", "idVendor")
            pid = _attr(parent, "ID_MODEL_ID", "idProduct")
            return vid == LENOVO_VID and pid == LENOVO_PID
    except Exception:
        return False
    return False


def _make_udev_monitor():
    """Return (monitor, context) or (None, None) if pyudev unavailable."""
    try:
        import pyudev
    except ImportError:
        print("[HOTPLUG] pyudev not installed — using poll rediscovery only")
        print("  Fix: .venv/bin/pip install pyudev   (or: make deps)")
        return None, None
    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    # USB device add/remove for VID/PID filtering in handler
    monitor.filter_by(subsystem="usb")
    monitor.start()
    return monitor, context


def wait_for_device(monitor=None) -> Tuple[InputDevice, Optional[InputDevice]]:
    """Block until at least the Teams button is present. Never raises to exit."""
    print(
        f"[HOTPLUG] Waiting for Lenovo Go (VID {LENOVO_VID} PID {LENOVO_PID})…",
        flush=True,
    )
    telem("device_disconnected", state="waiting")

    while True:
        teams, vol = discover_input_devices()
        if teams is not None:
            apply_alsa_paths(discover_alsa_card())
            return teams, vol

        # Prefer udev wake-up; fall back to timed poll
        if monitor is not None:
            try:
                device = monitor.poll(timeout=int(WAIT_POLL_SECONDS * 1000))
                if device is not None and device.action in ("add", "change", "bind"):
                    # Input/hidraw nodes lag the USB add event — always settle.
                    if _is_lenovo_usb_device(device):
                        print("[HOTPLUG] udev: Lenovo Go add — settling", flush=True)
                    time.sleep(SETTLE_SECONDS)
                    continue
            except Exception as e:
                print(f"[HOTPLUG] udev poll error: {e}", flush=True)
                time.sleep(WAIT_POLL_SECONDS)
        else:
            time.sleep(WAIT_POLL_SECONDS)


def bind_devices(
    teams: InputDevice, vol: Optional[InputDevice]
) -> Dict[int, InputDevice]:
    devices: Dict[int, InputDevice] = {teams.fd: teams}
    print(f"[BTN] Teams button: {teams.name} ({teams.path})", flush=True)
    if vol is not None:
        devices[vol.fd] = vol
        print(f"[VOL] Volume control: {vol.name} ({vol.path})", flush=True)
    else:
        print("[VOL] Consumer Control not found — volume dial disabled", flush=True)
    card = discover_alsa_card()
    apply_alsa_paths(card)
    print(
        f"[HOTPLUG] Bound ALSA audio={AUDIO_DEVICE} mixer={MIXER_DEVICE} card={card}",
        flush=True,
    )
    telem(
        "device_connected",
        teams_path=getattr(teams, "path", ""),
        vol_path=getattr(vol, "path", "") if vol else "",
        alsa_card=card if card is not None else -1,
    )
    print(
        "\nReady. Press Teams button to toggle session, turn dial to adjust volume.",
        flush=True,
    )
    print("Ctrl+C to exit. (Service stays up if device is unplugged.)\n", flush=True)
    return devices


def run_event_loop(devices: Dict[int, InputDevice], monitor=None) -> str:
    """Run until disconnect. Returns reason: 'disconnect' | 'interrupt'."""
    last_tick = 0.0
    udev_fd = -1
    if monitor is not None:
        try:
            udev_fd = monitor.fileno()
        except Exception:
            udev_fd = -1

    try:
        while True:
            watch = list(devices.keys())
            if udev_fd >= 0:
                watch.append(udev_fd)

            try:
                r, _, _ = select.select(watch, [], [], SELECT_TIMEOUT)
            except (InterruptedError, ValueError):
                return "disconnect"

            if not r:
                for dev in list(devices.values()):
                    try:
                        if not os.path.exists(dev.path):
                            print(f"[HOTPLUG] Device path gone: {dev.path}", flush=True)
                            return "disconnect"
                    except Exception:
                        return "disconnect"
                continue

            if udev_fd >= 0 and udev_fd in r and monitor is not None:
                try:
                    while True:
                        device = monitor.poll(timeout=0)
                        if device is None:
                            break
                        action = getattr(device, "action", None)
                        if action in ("remove", "unbind") and _is_lenovo_usb_device(device):
                            print(
                                "[HOTPLUG] udev remove — Lenovo Go disconnected",
                                flush=True,
                            )
                            return "disconnect"
                        if action in ("add", "change", "bind") and _is_lenovo_usb_device(
                            device
                        ):
                            print(
                                "[HOTPLUG] udev add/change while bound — rediscovering",
                                flush=True,
                            )
                            time.sleep(SETTLE_SECONDS)
                            return "disconnect"
                except Exception as e:
                    print(f"[HOTPLUG] udev read error: {e}", flush=True)

            for fd in r:
                if fd == udev_fd:
                    continue
                dev = devices.get(fd)
                if dev is None:
                    continue
                try:
                    events = list(dev.read())
                except (OSError, IOError) as e:
                    print(
                        f"[HOTPLUG] evdev read failed ({e}) — device unplugged?",
                        flush=True,
                    )
                    return "disconnect"

                for event in events:
                    if event.type != ecodes.EV_KEY or event.value != 1:
                        continue

                    if event.code == ecodes.BTN_0:
                        _toggle_session()
                    elif event.code == ecodes.KEY_VOLUMEUP:
                        vol = _set_volume("+")
                        now = time.monotonic()
                        if now - last_tick > 0.15:
                            _play_tick(vol if vol >= 0 else 50)
                            last_tick = now
                        print(f"[VOL] Up → {vol}%", flush=True)
                        telem("volume_change", direction="up", volume_pct=vol)
                    elif event.code == ecodes.KEY_VOLUMEDOWN:
                        vol = _set_volume("-")
                        now = time.monotonic()
                        if now - last_tick > 0.15:
                            _play_tick(vol if vol >= 0 else 50)
                            last_tick = now
                        print(f"[VOL] Down → {vol}%", flush=True)
                        telem("volume_change", direction="down", volume_pct=vol)

    except KeyboardInterrupt:
        return "interrupt"


def main() -> None:
    monitor, _ctx = _make_udev_monitor()
    devices: Dict[int, InputDevice] = {}

    try:
        while True:
            close_input_devices(devices)
            teams, vol = discover_input_devices()
            if teams is None:
                teams, vol = wait_for_device(monitor)
                # Extra settle after wait returns (input nodes may still be registering)
                time.sleep(0.3)
                teams2, vol2 = discover_input_devices()
                if teams2 is not None:
                    teams, vol = teams2, vol2
                telem("device_recovery")

            devices = bind_devices(teams, vol)
            reason = run_event_loop(devices, monitor=monitor)
            close_input_devices(devices)
            telem("device_disconnected", reason=reason)

            if reason == "interrupt":
                print("\nExiting device listener.", flush=True)
                return

            print(
                f"[HOTPLUG] Released devices ({reason}). Waiting for reconnect…",
                flush=True,
            )
            time.sleep(SETTLE_SECONDS)
    finally:
        close_input_devices(devices)


if __name__ == "__main__":
    main()
