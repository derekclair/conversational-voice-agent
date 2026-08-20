# Feature Specification: USB Hotplug for Lenovo Go Speakerphone

**Feature Branch**: `002-usb-hotplug`

**Created**: 2026-06-15

**Status**: Implemented (button listener 2026-07-08). Voice-loop ALSA mid-session unplug still partial (FR-004).

**Input**: The button listener and voice loop should dynamically detect when the Lenovo Go Wired Speakerphone is plugged in or moved to a different USB port, bind to it automatically, and gracefully handle disconnection — without requiring a service restart.

## User Scenarios & Testing

### User Story 1 — Plug in and go (Priority: P1)

The user plugs the Lenovo Go Speakerphone into any USB port on the DGX Spark. Within a few seconds, the LED lights up and the voice agent is ready — no manual service restart, no SSH needed.

**Why this priority**: This is the core value — the device should "just work" regardless of which port it's in or when it was plugged in.

**Independent Test**: Plug the Lenovo Go into a USB port while the systemd services are running. The LED should turn on within 5 seconds and the Teams button should start a voice session.

**Acceptance Scenarios**:

1. **Given** both systemd services are running and no Lenovo Go is connected, **When** the user plugs in the Lenovo Go, **Then** the button listener binds to BTN_0 and Consumer Control devices within 5 seconds and the voice loop's LED turns on.
2. **Given** the services started before the Lenovo Go was plugged in, **When** the user plugs in the device, **Then** the system behaves identically to when the device was present at boot.
3. **Given** the Lenovo Go is plugged into USB port A, **When** the user moves it to USB port B, **Then** the system re-discovers the device on the new port within 5 seconds.

---

### User Story 2 — Graceful disconnect (Priority: P2)

The user unplugs the Lenovo Go (to move it, because of a USB glitch, etc.). The services remain running and healthy, waiting for the device to reappear. No crash loops, no zombie processes.

**Why this priority**: Without graceful disconnect, removing the device causes the listener to crash-loop (current behavior). While `Restart=on-failure` recovers, it's noisy and slow.

**Independent Test**: Start a voice session, unplug the Lenovo Go mid-session. The service should log the disconnection and enter a "waiting for device" state without crashing.

**Acceptance Scenarios**:

1. **Given** a voice session is active, **When** the user unplugs the Lenovo Go, **Then** the session ends cleanly, arecord/aplay subprocesses are killed, and the button listener enters a "waiting for device" state.
2. **Given** the Lenovo Go is disconnected, **When** the user checks `systemctl --user status`, **Then** both services show `active (running)`, not `failed` or restart-looping.
3. **Given** the device was disconnected and reconnected, **When** the user presses the Teams button, **Then** a voice session starts normally.

---

### User Story 3 — Mid-session port swap (Priority: P3)

The user unplugs the Lenovo Go and plugs it back in (same or different port) while thinking about what to say next. The session should recover or a new session should be trivially startable.

**Why this priority**: Nice-to-have polish. The P1+P2 stories cover the practical use cases; this is about seamless recovery.

**Independent Test**: Start a session, unplug/replug within 10 seconds, press the Teams button. A new session should start.

**Acceptance Scenarios**:

1. **Given** a session was interrupted by disconnect, **When** the device is reconnected and the user presses the Teams button, **Then** a new session starts within 5 seconds of reconnection.

---

### Edge Cases

- What happens if two Lenovo Go devices are connected simultaneously? Pick the first one discovered; log a warning.
- What happens if a non-Lenovo USB audio device is plugged in? Ignore it — discovery filters by vendor name and BTN_0 capability.
- What happens if the device is plugged in but permissions are wrong (`/dev/input` not readable)? Log a clear error with the `usermod` fix, enter waiting state.
- What happens if udev events are delayed or arrive out of order? Use a short debounce/settle time after hotplug event before attempting discovery.
- What happens during a firmware update or USB reset that causes a brief disconnect/reconnect cycle? Debounce prevents premature rebind; 1-2s settle time.

## Requirements

### Functional Requirements

- **FR-001**: `button_listener.py` MUST use `pyudev` to monitor USB hotplug events for Lenovo Go devices (vendor `17ef`, product `a03f`).
- **FR-002**: On device arrival, the listener MUST run `_find_teams_button()` and `_find_consumer_control()` to bind to the correct evdev devices.
- **FR-003**: On device removal, the listener MUST close evdev file descriptors, kill any in-flight audio subprocesses, and enter a "waiting for device" state.
- **FR-004**: `voice_loop.py` MUST handle the audio device (`plughw:1,0`) disappearing mid-session — catch ALSA errors from arecord/aplay and end the session cleanly.
- **FR-005**: Both services MUST remain in `active (running)` state during device absence (no crash, no restart loop).
- **FR-006**: Device discovery MUST complete within 5 seconds of USB plug event.
- **FR-007**: The system MUST debounce rapid connect/disconnect cycles (firmware resets, port swaps) with a 1-2 second settle time.
- **FR-008**: Telemetry events MUST be emitted for `device_connected`, `device_disconnected`, and `device_recovery`.

### Key Entities

- **USB Monitor**: `pyudev`-based background thread watching for Lenovo Go USB events (add/remove).
- **Device State**: Enum or flag tracking whether the Lenovo Go is currently available (`connected`, `disconnected`, `discovering`).
- **Audio Device Guard**: Wrapper in `voice_loop.py` that catches ALSA subprocess failures and attributes them to device removal vs. transient errors.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Plug-to-ready time under 5 seconds (LED on + button responsive).
- **SC-002**: Zero service crashes during a plug/unplug/replug cycle.
- **SC-003**: No regression in voice turn latency (hotplug monitoring adds < 1ms overhead to the audio path).
- **SC-004**: `systemctl --user status` shows `active (running)` at all times, regardless of device presence.

## Assumptions

- `pyudev` is available or installable on AArch64 (DGX Spark) — it's a pure-Python wrapper around libudev which is present on all systemd systems.
- Only one Lenovo Go Speakerphone will be connected at a time (multi-device is out of scope).
- The ALSA device name `plughw:1,0` may change when the device moves ports — the audio device path may also need dynamic discovery (investigate whether ALSA device enumeration is stable or port-dependent).
- The `pyudev` monitor runs in a dedicated thread alongside the existing `select()` loop, or replaces it with a combined poll.
- The current `Restart=on-failure` systemd behavior remains as a safety net even after hotplug support is added.

## Current State (context for implementers)

As of 2026-06-15, the button listener uses dynamic capability-based discovery (`_find_teams_button()` scans for BTN_0, `_find_consumer_control()` scans by name). This was added to survive USB port changes across restarts. The hotplug feature builds on this foundation — the discovery functions already exist, they just need to be re-invoked when udev signals a device change instead of only at startup.

The voice loop's audio pipeline (arecord/aplay via subprocess) will throw errors if the USB audio device disappears — currently these propagate as unhandled exceptions that crash the session. FR-004 addresses wrapping these in device-aware error handling.
