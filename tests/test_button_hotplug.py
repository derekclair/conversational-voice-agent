"""Unit tests for button_listener hotplug helpers (no hardware required)."""

from local_tts import button_listener as bl


def test_apply_alsa_paths_updates_globals():
    old_audio, old_mixer = bl.AUDIO_DEVICE, bl.MIXER_DEVICE
    try:
        bl.apply_alsa_paths(2)
        assert bl.AUDIO_DEVICE == "plughw:2,0"
        assert bl.MIXER_DEVICE == "hw:2"
        bl.apply_alsa_paths(None)  # no-op
        assert bl.AUDIO_DEVICE == "plughw:2,0"
    finally:
        bl.AUDIO_DEVICE, bl.MIXER_DEVICE = old_audio, old_mixer


def test_close_input_devices_clears_dict():
    class Fake:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    f = Fake()
    d = {3: f}
    bl.close_input_devices(d)
    assert d == {}
    assert f.closed is True


def test_discover_input_devices_returns_tuple():
    # May be (None, None) if Go unplugged — must not raise.
    teams, vol = bl.discover_input_devices()
    assert teams is None or hasattr(teams, "path")
    assert vol is None or hasattr(vol, "path")
