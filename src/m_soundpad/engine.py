"""Dual-output playback. The mic stream is the source of truth; the monitor follows it."""

from __future__ import annotations

import os
import threading
from collections import deque
from dataclasses import dataclass

import numpy as np
import sounddevice as sd
import soundfile as sf

from m_soundpad.library import AUDIO_EXTENSIONS

CHANNELS = 2


@dataclass(frozen=True)
class DeviceStatus:
    mic_index: int | None
    mic_name: str
    monitor_index: int | None
    monitor_name: str
    warning: str | None = None


def to_stereo(data: np.ndarray) -> np.ndarray:
    channels = data.shape[1]
    if channels == 1:
        return np.repeat(data, CHANNELS, axis=1)
    if channels >= CHANNELS:
        return np.ascontiguousarray(data[:, :CHANNELS])
    raise ValueError("Audio file has no channels")


def load_audio(path: str) -> tuple[np.ndarray, int]:
    extension = os_path_ext(path)
    if extension not in AUDIO_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {extension or 'unknown'}")
    try:
        data, samplerate = sf.read(path, dtype="float32", always_2d=True)
    except Exception as exc:
        raise ValueError(f"Couldn't read {os_path_name(path)}: {exc}") from exc
    if data.size == 0 or data.shape[0] == 0:
        raise ValueError(f"{os_path_name(path)} is empty")
    if samplerate <= 0:
        raise ValueError(f"{os_path_name(path)} has an invalid sample rate")
    stereo = np.ascontiguousarray(to_stereo(data), dtype=np.float32)
    return stereo, int(samplerate)


def describe_devices() -> DeviceStatus:
    try:
        devices = list(sd.query_devices())
    except Exception as exc:
        return DeviceStatus(None, "Unavailable", None, "Unavailable", str(exc))

    mic = _find_sonar_output(devices)
    monitor = _default_output_index()
    monitor_name = _device_name(devices, monitor, "Default output")

    if mic is None:
        return DeviceStatus(
            None,
            "Not found",
            None,
            monitor_name,
            "SteelSeries Sonar microphone was not found. Clips play on the default output instead of voice chat.",
        )

    mic_name = _device_name(devices, mic, f"Device {mic}")
    if monitor == mic:
        return DeviceStatus(mic, mic_name, None, "Same as microphone")
    return DeviceStatus(mic, mic_name, monitor, monitor_name)


def reinitialize_audio() -> None:
    sd._terminate()
    sd._initialize()


# Serializes stream open/close with the device-list rescan.
host_lock = threading.Lock()


def _status_key(status: DeviceStatus) -> tuple:
    return (
        status.mic_index,
        status.mic_name,
        status.monitor_index,
        status.monitor_name,
        status.warning,
    )


class DeviceWatcher:
    """Rescans audio devices and calls back when the selected outputs change."""

    def __init__(self, get_engine, on_change, initial: DeviceStatus | None = None):
        self._get_engine = get_engine
        self._on_change = on_change
        self._stop = threading.Event()
        self._last = _status_key(initial) if initial is not None else None
        self._thread = threading.Thread(
            target=self._run, name="m-soundpad-devices", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(2.0):
            status = self._rescan()
            if status is None:
                continue
            key = _status_key(status)
            if key == self._last:
                continue
            self._last = key
            self._on_change(status)

    def _rescan(self) -> DeviceStatus | None:
        with host_lock:
            engine = self._get_engine()
            if engine is None or not engine.prepare_for_rescan():
                return None
            try:
                reinitialize_audio()
                status = describe_devices()
            except Exception:
                return None
            engine.mic_device = status.mic_index
            engine.monitor_device = status.monitor_index
            return status


class PlaybackEngine:
    def __init__(self, mic_device: int | None, monitor_device: int | None):
        self._lock = threading.Lock()
        self._audio: np.ndarray | None = None
        self._pos = 0
        self._playing = False
        self._natural_end = False
        self._last_error: str | None = None
        self._buffer: deque[np.ndarray] = deque()
        self._buffer_frames = 0
        self.mic_device = mic_device
        self.monitor_device = monitor_device
        self.volume = 1.0
        self.monitor_warning: str | None = None
        self._samplerate: int | None = None
        self._master_stream: sd.OutputStream | None = None
        self._monitor_stream: sd.OutputStream | None = None

    def play(self, data: np.ndarray, samplerate: int) -> None:
        with host_lock:
            self._halt()
            self._open_streams(samplerate)
            with self._lock:
                self._audio = data
                self._pos = 0
                self._playing = True
                self._natural_end = False
                self._last_error = None
                self._buffer.clear()
                self._buffer_frames = 0
            try:
                assert self._master_stream is not None
                self._master_stream.start()
                if self._monitor_stream is not None:
                    self._monitor_stream.start()
            except sd.PortAudioError as exc:
                self._close_streams()
                raise RuntimeError(f"Could not start playback: {exc}") from exc

    def stop(self) -> None:
        with host_lock:
            self._halt()

    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def prepare_for_rescan(self) -> bool:
        """Close idle streams so PortAudio can be reinitialized. False while audio is playing."""
        if self.is_playing():
            return False
        self._halt()
        self._close_streams()
        return True

    def _halt(self) -> None:
        with self._lock:
            self._playing = False
            self._natural_end = False
        self._stop_streams()

    def consume_natural_end(self) -> bool:
        with self._lock:
            ended = self._natural_end and not self._playing
            if ended:
                self._natural_end = False
            return ended

    def take_error(self) -> str | None:
        with self._lock:
            error = self._last_error
            self._last_error = None
            return error

    def close(self) -> None:
        with host_lock:
            self._halt()
            self._close_streams()

    def _open_streams(self, samplerate: int) -> None:
        reusable = (
            self._master_stream is not None
            and not self._master_stream.closed
            and self._samplerate == samplerate
        )
        if reusable:
            return
        self._close_streams()
        self._samplerate = samplerate
        try:
            self._master_stream = sd.OutputStream(
                samplerate=samplerate,
                channels=CHANNELS,
                dtype="float32",
                device=self.mic_device,
                callback=self._master_callback,
            )
        except sd.PortAudioError as exc:
            self._close_streams()
            raise RuntimeError(f"Could not open the audio output: {exc}") from exc

        if self.monitor_device is None or self.monitor_device == self.mic_device:
            return
        try:
            self._monitor_stream = sd.OutputStream(
                samplerate=samplerate,
                channels=CHANNELS,
                dtype="float32",
                device=self.monitor_device,
                callback=self._monitor_callback,
            )
        except sd.PortAudioError as exc:
            self._monitor_stream = None
            self.monitor_warning = f"Monitor output unavailable: {exc}"

    def _stop_streams(self) -> None:
        for stream in (self._master_stream, self._monitor_stream):
            if stream is None or stream.closed:
                continue
            try:
                if stream.active:
                    stream.stop()
            except sd.PortAudioError:
                pass

    def _close_streams(self) -> None:
        self._stop_streams()
        for name in ("_master_stream", "_monitor_stream"):
            stream = getattr(self, name)
            if stream is None:
                continue
            try:
                stream.close()
            except sd.PortAudioError:
                pass
            setattr(self, name, None)
        self._samplerate = None

    def _master_callback(self, outdata, frames, _time_info, _status) -> None:
        try:
            self._render_master(outdata, frames)
        except sd.CallbackStop:
            raise
        except Exception as exc:
            with self._lock:
                self._playing = False
                self._natural_end = True
                self._last_error = str(exc)
            outdata[:] = 0
            raise sd.CallbackStop() from exc

    def _render_master(self, outdata, frames: int) -> None:
        with self._lock:
            if not self._playing or self._audio is None:
                outdata[:] = 0
                raise sd.CallbackStop()

            chunk = self._audio[self._pos : self._pos + frames]
            actual = len(chunk)
            if actual < frames:
                if actual:
                    self._write(outdata, chunk)
                    outdata[actual:] = 0
                    self._push_buffer(chunk)
                else:
                    outdata[:] = 0
                self._pos += actual
                self._playing = False
                self._natural_end = True
                raise sd.CallbackStop()

            self._write(outdata, chunk)
            self._pos += frames
            self._push_buffer(chunk)

    def _monitor_callback(self, outdata, frames, _time_info, _status) -> None:
        with self._lock:
            chunk = self._pop_buffer(frames)
        if chunk is None:
            outdata[:] = 0
            return
        self._write(outdata, chunk)
        if len(chunk) < frames:
            outdata[len(chunk) :] = 0

    def _write(self, outdata, chunk: np.ndarray) -> None:
        actual = len(chunk)
        if actual == 0:
            return
        np.multiply(chunk, self.volume, out=outdata[:actual])
        np.clip(outdata[:actual], -1.0, 1.0, out=outdata[:actual])

    def _push_buffer(self, chunk: np.ndarray) -> None:
        copied = np.array(chunk, dtype=np.float32, copy=True)
        self._buffer.append(copied)
        self._buffer_frames += len(copied)
        limit = self._samplerate or 44100
        while self._buffer_frames > limit and self._buffer:
            dropped = self._buffer.popleft()
            self._buffer_frames -= len(dropped)

    def _pop_buffer(self, frames: int) -> np.ndarray | None:
        if not self._buffer_frames:
            return None
        segments: list[np.ndarray] = []
        needed = frames
        while needed > 0 and self._buffer:
            chunk = self._buffer[0]
            if len(chunk) <= needed:
                segments.append(self._buffer.popleft())
                self._buffer_frames -= len(chunk)
                needed -= len(chunk)
            else:
                segments.append(chunk[:needed])
                self._buffer[0] = chunk[needed:]
                self._buffer_frames -= needed
                needed = 0
        if not segments:
            return None
        return np.ascontiguousarray(np.vstack(segments))


def _find_sonar_output(devices) -> int | None:
    for index, device in enumerate(devices):
        name = str(device.get("name", "")).lower()
        outputs = int(device.get("max_output_channels", 0) or 0)
        if outputs > 0 and "steelseries sonar" in name and "microphone" in name:
            return index
    return None


def _default_output_index() -> int | None:
    try:
        default_pair = sd.default.device
    except Exception:
        return None
    try:
        candidate = int(default_pair[1])
    except (TypeError, ValueError, IndexError):
        return None
    if candidate < 0:
        return None
    return candidate


# PortAudio's MME host reports device names in a 32-byte buffer, so they stop at 31 characters.
_TRUNCATED_NAME_LENGTH = 31


def _device_name(devices, index: int | None, fallback: str) -> str:
    if index is None or index < 0 or index >= len(devices):
        return fallback
    raw = str(devices[index].get("name", fallback))
    if len(raw) < _TRUNCATED_NAME_LENGTH:
        return raw.strip() or fallback
    outputs = int(devices[index].get("max_output_channels", 0) or 0)
    inputs = int(devices[index].get("max_input_channels", 0) or 0)
    best = raw.strip() or fallback
    prefix = raw.rstrip()
    for device in devices:
        other = str(device.get("name", "")).strip()
        if len(other) <= len(best) or not other.startswith(prefix):
            continue
        if int(device.get("max_output_channels", 0) or 0) != outputs:
            continue
        if int(device.get("max_input_channels", 0) or 0) != inputs:
            continue
        best = other
    return best


def os_path_ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def os_path_name(path: str) -> str:
    return os.path.basename(path)
