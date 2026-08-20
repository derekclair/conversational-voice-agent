"""LED control for Lenovo Go Wired Speaker Teams light via hidraw.

Provides set_teams_light(active: bool) to turn the Teams/call status LED on or off.
The correct /dev/hidraw* node for the Lenovo Go is discovered at runtime (using
udev attributes) so that USB port changes / replugs don't break it.

The Teams light stays active while the voice loop/session is running (visual
confirmation that the device is in a voice interaction).

Report format (from HID descriptor analysis):
- Report ID: 5
- Value 0x01 turns the relevant LED (used as Teams/call active proxy) on.
- Value 0x00 turns it off.

Requires a udev rule (recommended) or chmod so your user can write the hidraw node.
See the 99-lenovo-go.rules example.
"""

import glob
import os
import subprocess
import time
from typing import Optional


def _find_lenovo_hidraw():
    """Dynamically find the hidraw node for the Lenovo Go (survives USB port changes)."""
    for path in sorted(glob.glob("/dev/hidraw*")):
        try:
            out = subprocess.check_output(
                ["udevadm", "info", "--name=" + path],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=1,
            )
            if "ID_VENDOR_FROM_DATABASE=Lenovo" in out or (
                "idVendor" in out and "17ef" in out.lower()
            ):
                return path
        except Exception:
            continue
    return None


# Lenovo Go HID raw device (interface with report ID 5 containing LED controls)
# We discover it at runtime instead of hard-coding so replugs / different ports work.
# Discovery runs at import time (when voice_loop starts).
HIDRAW_PATH = _find_lenovo_hidraw() or "/dev/hidraw4"

# Report ID 5 controls the Telephony/LED collection (5 output bits for LEDs)
# LED usages in this report: 0x17, 0x09, 0x18, 0x20, 0x21 (from HID descriptor)
# Bit 2 (0x04) is the one that visibly controls the call status / "answer call" light
# on this hardware (determined via --test-leds). We use it as the Teams/session proxy.
TEAMS_LED_REPORT_ID = 5
TEAMS_LED_ON = 0x04   # Bit 2 (0x04). This was the *only* bit that produced a visible light
                          # during --test-leds testing. It lit the "answer call" / call status
                          # indicator (the light the user observed). We treat this as the
                          # effective "Teams / in-session" proxy light for this hardware.
                          # If a different bit controls the exact ring around the Teams button
                          # on future units, change this constant and re-test.
TEAMS_LED_OFF = 0x00


def set_teams_light(active: bool, pulse: bool = False, duration: float = 0.0, value_override: int = None) -> bool:
    """Set the Teams LED state on the Lenovo Go.

    Args:
        active: True to turn light on, False to turn off.
        pulse: If True, pulse the light (blink) while active instead of steady.
               (Basic implementation: simple on/off; pulsing can be extended with thread.)
        duration: If >0 and pulse=True, pulse for this many seconds then stop.
        value_override: If provided, use this exact byte value for the LED report
                        instead of the default TEAMS_LED_ON/OFF. Useful for testing
                        which of the 5 LED bits controls the physical Teams light.

    Returns:
        True if report was sent successfully, False on error (e.g. permission).
    """
    if not HIDRAW_PATH or not os.path.exists(HIDRAW_PATH):
        print(f"[LED] No suitable Lenovo hidraw device found (looked for VID 17ef).")
        print("  The udev rule should have created a writable node. Try replugging the device.")
        return False

    report_id = TEAMS_LED_REPORT_ID
    if value_override is not None:
        value = value_override
    else:
        value = TEAMS_LED_ON if active else TEAMS_LED_OFF

    # Build output report: report ID byte + data byte
    report = bytes([report_id, value])

    try:
        with open(HIDRAW_PATH, "wb", buffering=0) as hid:
            hid.write(report)
            hid.flush()
        state = "ON" if active else "OFF"
        print(f"[LED] Teams light set to {state} (report 0x{report_id:02x} 0x{value:02x}) on {HIDRAW_PATH}")
        return True
    except PermissionError:
        print(f"[LED] Permission denied writing to {HIDRAW_PATH}.")
        print("  Re-run the udev rule + udevadm trigger, or: sudo chmod 666 " + HIDRAW_PATH)
        return False
    except Exception as e:
        print(f"[LED] Error controlling light: {e}")
        return False


def pulse_teams_light(seconds: float = 2.0, interval: float = 0.3) -> None:
    """Pulse the Teams light for a short time (demo / test helper)."""
    print(f"[LED] Pulsing Teams light for {seconds}s...")
    end = time.time() + seconds
    on = True
    while time.time() < end:
        set_teams_light(on)
        on = not on
        time.sleep(interval)
    set_teams_light(False)
    print("[LED] Pulse complete.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Control Lenovo Go Teams LED")
    parser.add_argument("--on", action="store_true", help="Turn Teams light on (default bit)")
    parser.add_argument("--off", action="store_true", help="Turn Teams light off")
    parser.add_argument("--pulse", action="store_true", help="Pulse the light briefly")
    parser.add_argument("--test-leds", action="store_true",
                        help="Cycle through each of the 5 possible LED bits (0x01, 0x02, ...). "
                             "Watch the physical light on the device and note which bit controls "
                             "the Teams button light. Then we can update TEAMS_LED_ON.")
    args = parser.parse_args()

    if args.test_leds:
        print("Testing each of the 5 LED output bits on report ID 5.")
        print("Watch the physical LED ring around the Teams button on the Lenovo Go.")
        print("Note which bit makes the light come on.\n")
        for i in range(5):
            val = 1 << i
            print(f"Bit {i} (0x{val:02x}) → ", end="", flush=True)
            if set_teams_light(True, value_override=val):
                time.sleep(2.0)
            set_teams_light(False, value_override=0)
            time.sleep(0.8)
        print("\nTest complete. Tell me which bit (0-4) lit the main Teams light.")
    elif args.pulse:
        pulse_teams_light()
    elif args.on:
        set_teams_light(True)
    elif args.off:
        set_teams_light(False)
    else:
        print("Use --on, --off, --pulse, or --test-leds")
        set_teams_light(True)
        time.sleep(1)
        set_teams_light(False)
