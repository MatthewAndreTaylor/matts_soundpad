import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import numpy as np
import sounddevice as sd
import soundfile as sf
import miniaudio
from collections import deque


def find_sonar_mic_output():
    for i, d in enumerate(sd.query_devices()):
        name = d["name"].lower()

        if "steelseries sonar - microphone" in name and d["max_output_channels"] > 0:
            return i

    return None


def get_devices(direction: str):
    """Return list of (index, name) for input or output devices."""
    all_devs = sd.query_devices()
    key = "max_output_channels" if direction == "output" else "max_input_channels"
    return [(i, d["name"]) for i, d in enumerate(all_devs) if d[key] > 0]


class PlaybackEngine:
    def __init__(self):
        self._lock = threading.Lock()
        self._audio = None
        self._pos = 0
        self._playing = False
        self.mic_device = None
        self.monitor_device = None
        self.volume = 1.0
        self.on_finish = None
        self._master_stream = None
        self._monitor_stream = None
        self._buffer = deque()
        self._buffer_frames = 0
        self._max_buffer = 48000

    def play(self, path):
        self.stop()
        ext = os.path.splitext(path)[1].lower()

        if ext in {".wav", ".flac", ".ogg"}:
            data, sr = sf.read(path, dtype="float32", always_2d=True)
        else:
            decoded = miniaudio.decode_file(path)
            data = np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float32)
            data /= 32768.0
            ch = decoded.nchannels
            data = data.reshape(-1, ch) if ch > 1 else data.reshape(-1, 1)
            sr = decoded.sample_rate

        sd.default.samplerate = 48000  # enforce stable rate

        with self._lock:
            self._audio = data
            self._pos = 0
            self._playing = True
            self._buffer.clear()
            self._buffer_frames = 0

        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        channels = 2

        def master_cb(outdata, frames, time, status):
            if status:
                print("Master:", status)

            with self._lock:
                if not self._playing:
                    outdata[:] = 0
                    raise sd.CallbackStop()

                chunk = self._audio[self._pos : self._pos + frames]
                actual = len(chunk)

                if actual < frames:
                    outdata[:actual] = chunk * self.volume
                    outdata[actual:] = 0
                    self._push_buffer(chunk)
                    raise sd.CallbackStop()

                outdata[:] = chunk * self.volume
                self._pos += frames

                self._push_buffer(chunk)

        def monitor_cb(outdata, frames, time, status):
            if status:
                print("Monitor:", status)

            chunk = self._pop_buffer(frames)

            if chunk is None:
                outdata[:] = 0
                return

            if len(chunk) < frames:
                outdata[: len(chunk)] = chunk * self.volume
                outdata[len(chunk) :] = 0
            else:
                outdata[:] = chunk * self.volume

        self._master_stream = sd.OutputStream(
            samplerate=48000,
            channels=channels,
            dtype="float32",
            device=self.mic_device,
            callback=master_cb,
            finished_callback=self._finished,
        )
        self._master_stream.start()

        if self.monitor_device is not None and self.monitor_device != self.mic_device:
            self._monitor_stream = sd.OutputStream(
                samplerate=48000,
                channels=channels,
                dtype="float32",
                device=self.monitor_device,
                callback=monitor_cb,
            )
            self._monitor_stream.start()

    def _push_buffer(self, chunk):
        """Push audio into ring buffer (non-blocking)."""
        self._buffer.append(chunk.copy())
        self._buffer_frames += len(chunk)

        while self._buffer_frames > self._max_buffer:
            old = self._buffer.popleft()
            self._buffer_frames -= len(old)

    def _pop_buffer(self, frames):
        """Pop exact number of frames for monitor."""
        if self._buffer_frames == 0:
            return None

        out = []
        needed = frames

        while needed > 0 and self._buffer:
            chunk = self._buffer[0]

            if len(chunk) <= needed:
                out.append(self._buffer.popleft())
                self._buffer_frames -= len(chunk)
                needed -= len(chunk)
            else:
                out.append(chunk[:needed])
                self._buffer[0] = chunk[needed:]
                self._buffer_frames -= needed
                needed = 0

        return np.vstack(out) if out else None

    # ─────────────────────────────────────────────

    def stop(self):
        with self._lock:
            self._playing = False

        if self._master_stream:
            self._master_stream.stop()
            self._master_stream.close()
            self._master_stream = None

        if self._monitor_stream:
            self._monitor_stream.stop()
            self._monitor_stream.close()
            self._monitor_stream = None

    def _finished(self):
        if self.on_finish:
            self.on_finish()

    def set_volume(self, v):
        self.volume = max(0.0, min(2.0, v))


def resolve_mic_output_device():
    sonar_device = find_sonar_mic_output()
    if sonar_device is not None:
        return sonar_device

    try:
        return sd.default.device[1]
    except (TypeError, IndexError):
        pass

    output_devices = get_devices("output")
    return output_devices[0][0] if output_devices else None


def resolve_monitor_output_device(mic_device: int | None):
    try:
        default_output = sd.default.device[1]
        if default_output is not None and default_output != mic_device:
            return default_output
    except (TypeError, IndexError):
        pass

    for device_index, _ in get_devices("output"):
        if device_index != mic_device:
            return device_index

    return None


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


class SoundPadApp(tk.Tk):
    MAX_COLS = 4

    def __init__(self):
        super().__init__()
        self.title("SoundPad")
        self.configure(bg=DARK_BG)
        self.geometry("1020x700")

        self.engine = PlaybackEngine()
        self.engine.on_finish = self._on_playback_finish
        self.folder = tk.StringVar(value="")
        self.files: list[str] = []
        self.active_path: str | None = None
        self.active_btn: tk.Widget | None = None
        self.btn_map: dict[str, tk.Button] = {}
        topbar = tk.Frame(self, bg=PANEL_BG, height=56)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        tk.Label(
            topbar,
            text="◈  SoundPad",
            font=("Courier", 16, "bold"),
            fg=ACCENT,
            bg=PANEL_BG,
        ).pack(side="left", padx=18, pady=10)

        pick_btn = tk.Button(
            topbar,
            text="📂  Open Folder",
            command=self._pick_folder,
            font=("Courier", 10, "bold"),
            bg=ACCENT,
            fg=TEXT_PRI,
            relief="flat",
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
            activebackground=ACCENT2,
            activeforeground=TEXT_PRI,
        )
        pick_btn.pack(side="right", padx=12, pady=8)

        tk.Label(
            topbar,
            textvariable=self.folder,
            font=("Courier", 9),
            fg=TEXT_SEC,
            bg=PANEL_BG,
            anchor="e",
        ).pack(side="right", padx=4)

        device_row = tk.Frame(self, bg=DARK_BG)
        device_row.pack(fill="x", padx=16, pady=(10, 0))

        tk.Label(
            device_row,
            text="🎙 MIC DEVICE",
            font=("Courier", 8, "bold"),
            fg=SUCCESS,
            bg=DARK_BG,
        ).pack(side="left")

        self.device_lbl = tk.Label(
            device_row,
            text="Auto-detected",
            font=("Courier", 8),
            fg=TEXT_SEC,
            bg=DARK_BG,
        )
        self.device_lbl.pack(side="left", padx=(4, 16))

        tk.Label(
            device_row,
            text="Voice apps should use this output as the microphone input",
            font=("Courier", 7),
            fg=TEXT_SEC,
            bg=DARK_BG,
        ).pack(side="left")

        self._refresh_devices()

        # ── volume + stop row ─────────────────────────────────────────────────
        ctrlbar = tk.Frame(self, bg=DARK_BG)
        ctrlbar.pack(fill="x", padx=16, pady=(8, 0))

        tk.Label(
            ctrlbar, text="VOL", font=("Courier", 8, "bold"), fg=TEXT_SEC, bg=DARK_BG
        ).pack(side="left")

        self.vol_slider = tk.Scale(
            ctrlbar,
            from_=0,
            to=200,
            orient="horizontal",
            command=self._apply_volume,
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
            ctrlbar, text="100%", font=("Courier", 9), fg=TEXT_PRI, bg=DARK_BG, width=5
        )
        self.vol_lbl.pack(side="left")

        stop_btn = tk.Button(
            ctrlbar,
            text="⏹  STOP",
            command=self._stop,
            font=("Courier", 9, "bold"),
            bg=CARD_BG,
            fg=ACCENT2,
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            activebackground=ACCENT2,
            activeforeground=TEXT_PRI,
        )
        stop_btn.pack(side="right", padx=4)

        # help label
        help_lbl = tk.Label(
            self,
            text="ℹ  Set your voice app's microphone to the MIC DEVICE selected above",
            font=("Courier", 8),
            fg=TEXT_SEC,
            bg=DARK_BG,
        )
        help_lbl.pack(fill="x", padx=16)

        # now-playing bar
        npbar = tk.Frame(self, bg=PANEL_BG, height=30)
        npbar.pack(fill="x", pady=(6, 0))
        npbar.pack_propagate(False)
        tk.Label(
            npbar, text="NOW PLAYING:", font=("Courier", 8), fg=TEXT_SEC, bg=PANEL_BG
        ).pack(side="left", padx=12)
        self.np_lbl = tk.Label(
            npbar, text="—", font=("Courier", 9, "bold"), fg=ACCENT, bg=PANEL_BG
        )
        self.np_lbl.pack(side="left")

        # scrollable pad grid
        container = tk.Frame(self, bg=DARK_BG)
        container.pack(fill="both", expand=True, padx=12, pady=12)

        canvas = tk.Canvas(container, bg=DARK_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.pad_frame = tk.Frame(canvas, bg=DARK_BG)
        self.pad_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.pad_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"),
        )
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))
        self.canvas = canvas
        self._show_empty_state()

    def _show_empty_state(self):
        for w in self.pad_frame.winfo_children():
            w.destroy()
        tk.Label(
            self.pad_frame,
            text="Click  📂 Open Folder  to load your audio files",
            font=("Courier", 12),
            fg=TEXT_SEC,
            bg=DARK_BG,
        ).grid(row=0, column=0, padx=60, pady=80)

    # ── device refresh ────────────────────────────────────────────────────────

    def _refresh_devices(self):
        """Refresh audio devices and auto-pick the mic output."""
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass

        mic_device = resolve_mic_output_device()
        monitor_device = resolve_monitor_output_device(mic_device)
        self.engine.mic_device = mic_device
        self.engine.monitor_device = monitor_device

        if mic_device is None:
            self.device_lbl.configure(text="No output device found", fg=ACCENT2)
            return

        output_names = dict(get_devices("output"))
        if monitor_device is None:
            device_text = output_names.get(mic_device, f"Device {mic_device}")
        else:
            mic_name = output_names.get(mic_device, f"Device {mic_device}")
            monitor_name = output_names.get(monitor_device, f"Device {monitor_device}")
            device_text = f"{mic_name} | Speaker: {monitor_name}"
        self.device_lbl.configure(
            text=device_text,
            fg=TEXT_SEC,
        )

    # ── folder / pad building ─────────────────────────────────────────────────

    def _pick_folder(self):
        path = filedialog.askdirectory(title="Select audio folder")
        if not path:
            return
        exts = {".mp3", ".wav", ".ogg", ".flac", ".aiff", ".m4a"}
        files = sorted(
            f for f in os.listdir(path) if os.path.splitext(f)[1].lower() in exts
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
        cols = self.MAX_COLS
        for idx, fpath in enumerate(self.files):
            r, c = divmod(idx, cols)
            name = os.path.splitext(os.path.basename(fpath))[0]
            btn = self._make_pad_btn(name, fpath)
            btn.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")
            self.btn_map[fpath] = btn
        for c in range(cols):
            self.pad_frame.columnconfigure(c, weight=1, minsize=180)

    def _make_pad_btn(self, label: str, fpath: str) -> tk.Button:
        display = label if len(label) <= 22 else label[:20] + "…"
        btn = tk.Button(
            self.pad_frame,
            text=display,
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

    # ── playback ──────────────────────────────────────────────────────────────

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
        self.np_lbl.configure(text="—", fg=ACCENT)

    def _reset_active_btn(self):
        if self.active_btn:
            self.active_btn.configure(bg=CARD_BG, fg=TEXT_PRI)
        self.active_btn = None

    def _on_playback_finish(self):
        self.after(0, self._stop)

    def _apply_devices(self):
        self._refresh_devices()

    def _apply_volume(self, val):
        v = int(val)
        self.engine.set_volume(v / 100.0)
        self.vol_lbl.configure(text=f"{v}%")


if __name__ == "__main__":
    root = SoundPadApp()
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        "TCombobox",
        fieldbackground=CARD_BG,
        background=CARD_BG,
        foreground=TEXT_PRI,
        selectbackground=ACCENT,
        selectforeground=TEXT_PRI,
        arrowcolor=ACCENT,
    )
    style.configure(
        "TScrollbar", background=CARD_BG, troughcolor=DARK_BG, arrowcolor=TEXT_SEC
    )
    root.mainloop()
