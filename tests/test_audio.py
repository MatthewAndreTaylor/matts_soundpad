import struct
import tempfile
import unittest
import wave
from pathlib import Path

from m_soundpad.engine import _device_name, load_audio, to_stereo
from m_soundpad.hotkeys import HotkeyTracker, display_combo
import numpy as np


def write_wav(path: Path, frames: int, channels: int) -> None:
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        frame = struct.pack("<" + ("h" * channels), *([1200] * channels))
        handle.writeframes(frame * frames)


class AudioTests(unittest.TestCase):
    def test_mono_files_become_stereo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mono.wav"
            write_wav(path, 32, 1)
            data, samplerate = load_audio(str(path))
        self.assertEqual(data.shape, (32, 2))
        self.assertEqual(samplerate, 22050)
        self.assertTrue(np.allclose(data[:, 0], data[:, 1]))

    def test_truncated_device_names_use_the_longer_match(self):
        devices = [
            {
                "name": "DELL P3424WEB (NVIDIA High Defi",
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
            {
                "name": "DELL P3424WEB (NVIDIA High Definition Audio)",
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
            {
                "name": "SteelSeries Sonar - Microphone ",
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
            {
                "name": "SteelSeries Sonar - Microphone (SteelSeries Sonar Virtual Audio Device)",
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
            {
                "name": "Speakers",
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
            {
                "name": "Speakers (THX Spatial)",
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
        ]
        self.assertEqual(
            _device_name(devices, 0, "fallback"),
            "DELL P3424WEB (NVIDIA High Definition Audio)",
        )
        self.assertEqual(
            _device_name(devices, 2, "fallback"),
            "SteelSeries Sonar - Microphone (SteelSeries Sonar Virtual Audio Device)",
        )
        self.assertEqual(_device_name(devices, 4, "fallback"), "Speakers")

    def test_hotkey_tracker_ignores_repeats_and_keeps_modifiers(self):
        seen = []
        tracker = HotkeyTracker(seen.append)
        tracker.press("ctrl", modifier=True)
        tracker.press("ctrl", modifier=True)
        tracker.press("f8", modifier=False)
        tracker.press("f8", modifier=False)
        tracker.release("f8")
        tracker.press("a", modifier=False)
        tracker.release("ctrl")
        tracker.release("a")
        self.assertEqual(seen, ["ctrl+f8", "ctrl+a"])
        self.assertEqual(display_combo("ctrl+shift+f8"), "Ctrl+Shift+F8")

    def test_to_stereo_keeps_the_first_two_channels(self):
        data = np.arange(12, dtype=np.float32).reshape(4, 3)
        stereo = to_stereo(data)
        self.assertEqual(stereo.shape, (4, 2))
        self.assertTrue(np.array_equal(stereo[:, 0], data[:, 0]))
