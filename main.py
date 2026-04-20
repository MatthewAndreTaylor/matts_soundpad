import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from collections import deque

import numpy as np
import sounddevice as sd
import soundfile as sf

TITLE = "Matts SoundPad"
DARK_BG = "#0d0d0f"
PANEL_BG = "#141418"
CARD_BG = "#1c1c22"
CARD_HOVER = "#25252d"
CARD_ACTIVE = "#2e2e3a"
ACCENT = "#7c5cfc"
ACCENT2 = "#fc5c7d"
TEXT_PRI = "#f0eeff"
TEXT_SEC = "#8888aa"
SUCCESS = "#4ade80"

SUPPORTED_EXTS = {".wav", ".flac", ".ogg"}


def load_audio(path: str) -> tuple[np.ndarray, int]:
    """Load an audio file and return (float32 stereo array, sample_rate)."""
    ext = os.path.splitext(path)[1].lower()

    if ext in SUPPORTED_EXTS:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    assert data.shape[1] == 2, "Audio must have 2 channels"
    return data, sr


def find_sonar_mic_output() -> int | None:
    """Return the device index of a SteelSeries Sonar virtual mic, or None."""
    for i, d in enumerate(sd.query_devices()):
        if (
            "steelseries sonar - microphone" in d["name"].lower()
            and d["max_output_channels"] > 0
        ):
            return i
    return None


SAMPLE_RATE = 44100 # 34100 sounds cool, 44100 is standard
CHANNELS = 2


class PlaybackEngine:
    def __init__(self, mic_device: int | None, monitor_device: int | None):
        self._lock = threading.Lock()
        self._audio: np.ndarray | None = None
        self._pos = 0
        self._playing = False
        self._buffer: deque[np.ndarray] = deque()
        self._buffer_frames = 0
        self.mic_device: int | None = mic_device
        self.monitor_device: int | None = monitor_device
        self.volume = 1.0

        self._master_stream = sd.OutputStream(
            channels=CHANNELS,
            dtype="float32",
            device=self.mic_device,
            callback=self._master_callback,
        )

        self._monitor_stream = None

        if self.monitor_device is not None and self.monitor_device != self.mic_device:
            self._monitor_stream = sd.OutputStream(
                channels=CHANNELS,
                dtype="float32",
                device=self.monitor_device,
                callback=self._monitor_callback,
            )

    def play(self, path: str) -> None:
        self.stop()
        data, _ = load_audio(path)

        with self._lock:
            self._audio = data
            self._pos = 0
            self._playing = True
            self._buffer.clear()
            self._buffer_frames = 0

        self._master_stream.start()
        if self._monitor_stream:
            self._monitor_stream.start()

    def stop(self) -> None:
        with self._lock:
            self._playing = False

        for stream in (self._master_stream, self._monitor_stream):
            if stream:
                stream.stop()

    def _master_callback(self, outdata, frames, time, status):
        with self._lock:
            if not self._playing:
                outdata[:] = 0
                raise sd.CallbackStop()

            chunk = self._audio[self._pos : self._pos + frames]
            actual = len(chunk)

            outdata[:actual] = chunk * self.volume
            if actual < frames:
                outdata[actual:] = 0
                self._push_buffer(chunk)
                raise sd.CallbackStop()

            self._pos += frames
            self._push_buffer(chunk)

    def _monitor_callback(self, outdata, frames, time, status):
        chunk = self._pop_buffer(frames)
        if chunk is None:
            outdata[:] = 0
            return

        outdata[: len(chunk)] = chunk * self.volume
        if len(chunk) < frames:
            outdata[len(chunk) :] = 0

    def _push_buffer(self, chunk: np.ndarray) -> None:
        self._buffer.append(chunk.copy())
        self._buffer_frames += len(chunk)

        while self._buffer_frames > SAMPLE_RATE:
            dropped = self._buffer.popleft()
            self._buffer_frames -= len(dropped)

    def _pop_buffer(self, frames: int) -> np.ndarray | None:
        if not self._buffer_frames:
            return None

        segments, needed = [], frames
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

        return np.vstack(segments) if segments else None


class SoundPadApp(tk.Tk):
    MAX_COLS = 6

    def __init__(self):
        super().__init__()
        self.title(TITLE)
        self.configure(bg=DARK_BG)
        self.geometry("1020x700")
        self.folder = tk.StringVar()
        self.files: list[str] = []
        self.active_path: str | None = None
        self.active_btn: tk.Button | None = None
        self.btn_map: dict[str, tk.Button] = {}
        self._build_ui()
        self._refresh_devices()
        self.engine = PlaybackEngine(
            mic_device=self.mic_device, monitor_device=self.monitor_device
        )
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self._build_topbar()
        self._build_device_row()
        self._build_controls()
        self._build_now_playing()
        self._build_pad_grid()

    def _build_topbar(self):
        bar = tk.Frame(self, bg=PANEL_BG, height=56)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        tk.Label(
            bar,
            text=TITLE,
            fg=ACCENT,
            bg=PANEL_BG,
        ).pack(side="left", padx=18, pady=10)
        tk.Button(
            bar,
            text="📂  Open Folder",
            command=self._pick_folder,
            bg=ACCENT,
            fg=TEXT_PRI,
            relief="flat",
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
            activebackground=ACCENT2,
            activeforeground=TEXT_PRI,
        ).pack(side="right", padx=12, pady=8)

        tk.Label(
            bar,
            textvariable=self.folder,
            fg=TEXT_SEC,
            bg=PANEL_BG,
            anchor="e",
        ).pack(side="right", padx=4)

    def _build_device_row(self):
        row = tk.Frame(self, bg=DARK_BG)
        row.pack(fill="x", padx=16, pady=(10, 0))
        tk.Label(
            row,
            text="🎙 MIC DEVICE",
            fg=SUCCESS,
            bg=DARK_BG,
        ).pack(side="left")
        self.device_lbl = tk.Label(row, fg=TEXT_SEC, bg=DARK_BG)
        self.device_lbl.pack(side="left", padx=(4, 16))

    def _build_controls(self):
        bar = tk.Frame(self, bg=DARK_BG)
        bar.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(
            bar, text="VOL", fg=TEXT_SEC, bg=DARK_BG
        ).pack(side="left")
        self.vol_slider = tk.Scale(
            bar,
            from_=0,
            to=200,
            orient="horizontal",
            command=self._on_volume_change,
            bg=DARK_BG,
            fg=TEXT_PRI,
            troughcolor=CARD_BG,
            highlightthickness=0,
            sliderrelief="flat",
            length=160,
            showvalue=False,
            bd=0,
        )
        self.vol_slider.set(100)
        self.vol_slider.pack(side="left", padx=(6, 4))
        self.vol_lbl = tk.Label(
            bar, text="100%", fg=TEXT_PRI, bg=DARK_BG, width=5
        )
        self.vol_lbl.pack(side="left")

        tk.Button(
            bar,
            text="⏹  STOP",
            command=self._stop,
            bg=CARD_BG,
            fg=ACCENT2,
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            activebackground=ACCENT2,
            activeforeground=TEXT_PRI,
        ).pack(side="right", padx=4)

        tk.Label(
            self,
            text="ℹ  Set your voice app's microphone to the MIC DEVICE selected above",
            fg=TEXT_SEC,
            bg=DARK_BG,
        ).pack(fill="x", padx=16)

    def _build_now_playing(self):
        bar = tk.Frame(self, bg=PANEL_BG, height=30)
        bar.pack(fill="x", pady=(6, 0))
        bar.pack_propagate(False)
        tk.Label(
            bar, text="NOW PLAYING:", fg=TEXT_SEC, bg=PANEL_BG
        ).pack(side="left", padx=12)
        self.np_lbl = tk.Label(bar, text="-", fg=ACCENT, bg=PANEL_BG)
        self.np_lbl.pack(side="left")

    def _build_pad_grid(self):
        container = tk.Frame(self, bg=DARK_BG)
        container.pack(fill="both", expand=True, padx=12, pady=12)
        canvas = tk.Canvas(container, bg=DARK_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.pad_frame = tk.Frame(canvas, bg=DARK_BG)
        self.pad_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.pad_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"),
        )
        self.canvas = canvas
        tk.Label(
            self.pad_frame,
            text="Click  📂 Open Folder  to load your audio files",
            font=("Courier", 12),
            fg=TEXT_SEC,
            bg=DARK_BG,
        ).grid(row=0, column=0, padx=60, pady=80)
        

    def _refresh_devices(self):
        sd._terminate()
        sd._initialize()

        mic = find_sonar_mic_output()
        monitor = sd.default.device[1] if sd.default.device else None
        self.mic_device = mic
        self.monitor_device = monitor

        if mic is None:
            self.device_lbl.configure(text="No output device found", fg=ACCENT2)
            return

        names = {i: d["name"] for i, d in enumerate(sd.query_devices())}
        mic_name = names.get(mic, f"Device {mic}")
        if monitor is not None:
            monitor_name = names.get(monitor, f"Device {monitor}")
            label = f"{mic_name} | Speaker: {monitor_name}"
        else:
            label = mic_name

        self.device_lbl.configure(text=label, fg=TEXT_SEC)

    def _pick_folder(self):
        path = filedialog.askdirectory(title="Select audio folder")
        if not path:
            return

        files = sorted(
            f
            for f in os.listdir(path)
            if os.path.splitext(f)[1].lower() in SUPPORTED_EXTS
        )
        if not files:
            messagebox.showinfo("Empty", "No audio files found in that folder.")
            return

        self.folder.set(path)
        self.files = [os.path.join(path, f) for f in files]
        self._build_pad()

    def _build_pad(self):
        self._stop()
        for w in self.pad_frame.winfo_children():
            w.destroy()
        self.btn_map = {}

        for idx, fpath in enumerate(self.files):
            row, col = divmod(idx, self.MAX_COLS)
            name = os.path.splitext(os.path.basename(fpath))[0]
            btn = self._make_pad_btn(name, fpath)
            btn.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            self.btn_map[fpath] = btn

        for c in range(self.MAX_COLS):
            self.pad_frame.columnconfigure(c, weight=1, minsize=180)

    def _make_pad_btn(self, label: str, fpath: str) -> tk.Button:
        btn = tk.Button(
            self.pad_frame,
            text=label,
            font=("Courier", 10, "bold"),
            bg=CARD_BG,
            fg=TEXT_PRI,
            relief="flat",
            bd=0,
            padx=8,
            pady=22,
            wraplength=160,
            cursor="hand2",
            activebackground=CARD_ACTIVE,
            activeforeground=TEXT_PRI,
            command=lambda p=fpath: self._play(p),
        )
        btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=CARD_HOVER))
        btn.bind(
            "<Leave>",
            lambda e, b=btn, p=fpath: b.configure(
                bg=CARD_ACTIVE if p == self.active_path else CARD_BG
            ),
        )
        return btn

    def _play(self, fpath: str):
        if self.active_path == fpath:
            self._stop()
            return

        self._reset_active_btn()
        self.active_path = fpath
        self.active_btn = self.btn_map.get(fpath)
        name = os.path.splitext(os.path.basename(fpath))[0]
        self.np_lbl.configure(text=name, fg=ACCENT)
        if self.active_btn:
            self.active_btn.configure(bg=ACCENT, fg="#ffffff")

        self.engine.play(fpath)

    def _stop(self):
        self.engine.stop()
        self._reset_active_btn()
        self.active_path = None
        self.np_lbl.configure(text="-", fg=ACCENT)

    def _reset_active_btn(self):
        if self.active_btn:
            self.active_btn.configure(bg=CARD_BG, fg=TEXT_PRI)
        self.active_btn = None

    def _on_volume_change(self, val: str):
        v = int(val)
        self.engine.volume = (v / 100.0)
        self.vol_lbl.configure(text=f"{v}%")

    def _on_close(self):
        self.engine.stop()
        self.destroy()


if __name__ == "__main__":
    root = SoundPadApp()
    root.mainloop()
