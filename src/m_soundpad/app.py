"""Desktop window for the soundpad."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from m_soundpad.engine import DeviceWatcher, PlaybackEngine, describe_devices, load_audio
from m_soundpad.hotkeys import GlobalHotkeys, display_combo
from m_soundpad.library import (
    AUDIO_EXTENSIONS,
    FolderRecord,
    LibraryCache,
    clean_path,
    folder_key,
    group_by_folder,
    pretty_relative,
    scan_folder,
)
from m_soundpad.runtime import emergency_dialog, log_exception

FONT = "Segoe UI"
BG = "#0e0e12"
PANEL = "#121218"
SIDEBAR = "#101014"
CARD = "#1c1c26"
CARD_HOVER = "#2a2a38"
ACCENT = "#7c6bf5"
LINE = "#2c2c38"
TEXT = "#f4f2fb"
MUTED = "#9393ab"
FAINT = "#65657c"
DANGER = "#ff5d7a"
OK = "#3ddc97"
WARN = "#f5b942"
SELECTED = "#2a2448"
GROUP_COLORS = (
    "#7c6bf5",
    "#ff6b8a",
    "#3ddc97",
    "#f5b942",
    "#5cc8ff",
    "#c084fc",
    "#ff8a4c",
    "#7ee0d0",
)
EXTENSION_LABEL = ", ".join(sorted(AUDIO_EXTENSIONS))
SIDEBAR_WIDTH = 420
PAD_WIDTH = 220
PAD_HEIGHT = 84
PAD_GAP_X = 12


def main() -> None:
    try:
        app = SoundPadApp()
        app.deiconify()
        app.mainloop()
    except Exception as exc:
        log_exception(type(exc), exc, exc.__traceback__)
        emergency_dialog(f"Soundpad failed to start.\n\n{exc}")
        if sys.stderr is not None:
            import traceback

            traceback.print_exc()
        raise SystemExit(1) from exc


class Pad:
    def __init__(
        self,
        path: str,
        shell: tk.Frame,
        body: tk.Frame,
        stripe: tk.Frame,
        button: tk.Button,
        key_label: tk.Label,
    ):
        self.path = path
        self.shell = shell
        self.body = body
        self.stripe = stripe
        self.button = button
        self.key_label = key_label


class SoundPadApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.withdraw()
        self.title("Matt's Soundpad")
        self.configure(bg=BG)
        self.minsize(920, 620)
        self.cache = LibraryCache()
        self.engine: PlaybackEngine | None = None
        self.devices = describe_devices()
        self.current_folder: str | None = None
        self.files: list[str] = []
        self.pads: dict[str, Pad] = {}
        self.active_path: str | None = None
        self._cols = 0
        self._configured_cols = 0
        self._token = 0
        self._scan_token = 0
        self._rebuilding = False
        self._closing = False
        self._search_job: str | None = None
        self._persistent_hint = ""
        self._hover_hint = ""
        self._menu_path: str | None = None
        self._capturing_path: str | None = None
        self._inbox: queue.Queue = queue.Queue()
        self._hotkeys = GlobalHotkeys(self._queue_hotkey)

        self.search_var = tk.StringVar()
        self.folder_var = tk.StringVar(value="Add a folder to start")
        self.count_var = tk.StringVar(value="0 clips")
        self.now_var = tk.StringVar(value="Ready")
        self.vol_var = tk.StringVar(value=f"{self.cache.volume}%")
        self.hint_var = tk.StringVar()

        self._build_ui()
        self._set_icon()
        self._bind_keys()
        self._restore_window()
        self._apply_devices()
        self._device_watcher = DeviceWatcher(
            get_engine=lambda: self.engine,
            on_change=self._queue_devices,
            initial=self.devices,
        )
        self._device_watcher.start()
        self._render_folders()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(16, self._pump)
        hotkey_error = self._hotkeys.start()
        if hotkey_error:
            self._set_persistent_hint("Keyboard shortcuts are unavailable")
        self._autoload()

    def _build_ui(self) -> None:
        self._style_scrollbar()
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        tk.Frame(self, bg=ACCENT, height=3).grid(row=0, column=0, sticky="ew")
        self._build_header()
        self._build_body()
        self._build_status()
        self._build_menu()

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=PANEL)
        header.grid(row=1, column=0, sticky="ew")
        mark = tk.Frame(header, bg=ACCENT, width=12, height=12)
        mark.pack(side="left", padx=(18, 10), pady=18)
        mark.pack_propagate(False)
        tk.Label(
            header,
            text="Matt's Soundpad",
            font=(FONT, 16, "bold"),
            fg=TEXT,
            bg=PANEL,
        ).pack(side="left")
        self.header_status = tk.Label(
            header,
            text="Checking audio…",
            font=(FONT, 10),
            fg=MUTED,
            bg=PANEL,
        )
        self.header_status.pack(side="right", padx=18)

    def _build_body(self) -> None:
        body = tk.Frame(self, bg=BG)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, minsize=SIDEBAR_WIDTH, weight=0)
        body.grid_columnconfigure(2, weight=1)

        sidebar = tk.Frame(body, bg=SIDEBAR, width=SIDEBAR_WIDTH)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.pack_propagate(False)
        tk.Frame(body, bg=LINE, width=1).grid(row=0, column=1, sticky="nsew")
        main = tk.Frame(body, bg=BG)
        main.grid(row=0, column=2, sticky="nsew")
        main.grid_rowconfigure(2, weight=1)
        main.grid_columnconfigure(0, weight=1)

        self._build_sidebar(sidebar)
        self._build_main(main)

    def _build_sidebar(self, sidebar: tk.Frame) -> None:
        controls = tk.Frame(sidebar, bg=SIDEBAR)
        controls.pack(side="bottom", fill="x")
        self._build_controls(controls)

        heading = tk.Frame(sidebar, bg=SIDEBAR)
        heading.pack(side="top", fill="x", padx=16, pady=(16, 8))
        tk.Label(
            heading,
            text="LIBRARY",
            font=(FONT, 9, "bold"),
            fg=MUTED,
            bg=SIDEBAR,
        ).pack(side="left")

        actions = tk.Frame(sidebar, bg=SIDEBAR)
        actions.pack(side="top", fill="x", padx=16, pady=(0, 8))
        flat_button(
            actions,
            "Add folder",
            self._add_folder,
            ACCENT,
            "#ffffff",
            (FONT, 10, "bold"),
            "#6a5ae0",
            padx=10,
            pady=7,
        ).pack(side="left")
        flat_button(
            actions,
            "Rescan",
            self._rescan,
            CARD,
            TEXT,
            (FONT, 10),
            CARD_HOVER,
            padx=10,
            pady=7,
        ).pack(side="left", padx=(8, 0))

        wrap = tk.Frame(sidebar, bg=SIDEBAR)
        wrap.pack(side="top", fill="both", expand=True)
        self.folder_canvas = tk.Canvas(
            wrap, bg=SIDEBAR, highlightthickness=0, bd=0
        )
        folder_scroll = ttk.Scrollbar(
            wrap, orient="vertical", style="Pad.Vertical.TScrollbar", command=self.folder_canvas.yview
        )
        self.folder_frame = tk.Frame(self.folder_canvas, bg=SIDEBAR)
        self._folder_window = self.folder_canvas.create_window(
            (0, 0), window=self.folder_frame, anchor="nw"
        )
        self.folder_frame.bind(
            "<Configure>",
            lambda _event: self.folder_canvas.configure(scrollregion=self.folder_canvas.bbox("all")),
        )
        self.folder_canvas.bind("<Configure>", self._size_folder_canvas)
        self.folder_canvas.configure(yscrollcommand=folder_scroll.set)
        folder_scroll.pack(side="right", fill="y")
        self.folder_canvas.pack(side="left", fill="both", expand=True)
        self._style_scrollbar()

    def _build_controls(self, parent: tk.Frame) -> None:
        tk.Frame(parent, bg=LINE, height=1).pack(fill="x")
        box = tk.Frame(parent, bg=SIDEBAR)
        box.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(box, text="AUDIO", font=(FONT, 9, "bold"), fg=MUTED, bg=SIDEBAR).pack(anchor="w")
        tk.Label(
            box, text="Voice chat mic", font=(FONT, 8), fg=FAINT, bg=SIDEBAR
        ).pack(anchor="w", pady=(8, 0))
        self.mic_value = self._device_label(box)
        self.mic_value.pack(anchor="w", fill="x")
        tk.Label(box, text="Monitor", font=(FONT, 8), fg=FAINT, bg=SIDEBAR).pack(anchor="w", pady=(8, 0))
        self.monitor_value = self._device_label(box)
        self.monitor_value.pack(anchor="w", fill="x")
        self.device_warning = self._device_label(box, font=(FONT, 8), fg=WARN)
        self.device_warning.pack(anchor="w", fill="x", pady=(8, 0))

        volume = tk.Frame(parent, bg=SIDEBAR)
        volume.pack(fill="x", padx=16, pady=(12, 4))
        volume_top = tk.Frame(volume, bg=SIDEBAR)
        volume_top.pack(fill="x")
        tk.Label(volume_top, text="VOLUME", font=(FONT, 9, "bold"), fg=MUTED, bg=SIDEBAR).pack(side="left")
        tk.Label(volume_top, textvariable=self.vol_var, font=(FONT, 9), fg=TEXT, bg=SIDEBAR).pack(side="right")
        self.vol_slider = tk.Scale(
            volume,
            from_=0,
            to=200,
            orient="horizontal",
            command=self._on_volume,
            bg=SIDEBAR,
            fg=TEXT,
            troughcolor=CARD,
            activebackground=ACCENT,
            highlightthickness=0,
            sliderrelief="flat",
            bd=0,
            showvalue=False,
            length=248,
            width=12,
            sliderlength=18,
        )
        self.vol_slider.set(self.cache.volume)
        self.vol_slider.pack(fill="x", pady=(4, 0))
        self.vol_slider.bind("<ButtonRelease-1>", self._save_preferences)

        stop_row = tk.Frame(parent, bg=SIDEBAR)
        stop_row.pack(fill="x", padx=16, pady=(8, 16))
        self.stop_button = flat_button(
            stop_row,
            "Stop",
            self._stop,
            CARD,
            DANGER,
            (FONT, 11, "bold"),
            DANGER,
            padx=10,
            pady=8,
        )
        self.stop_button.pack(side="left", fill="x", expand=True)
        tk.Label(stop_row, text="Esc", font=(FONT, 8), fg=FAINT, bg=SIDEBAR).pack(side="right", padx=(8, 0))
        self.stop_button.bind("<Enter>", lambda _e: self.stop_button.configure(bg=DANGER, fg="#ffffff"))
        self.stop_button.bind("<Leave>", lambda _e: self.stop_button.configure(bg=CARD, fg=DANGER))

    def _build_main(self, main: tk.Frame) -> None:
        toolbar = tk.Frame(main, bg=BG)
        toolbar.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 0))
        tk.Label(toolbar, text="Search", font=(FONT, 10), fg=MUTED, bg=BG).pack(side="left")
        tk.Label(toolbar, textvariable=self.count_var, font=(FONT, 10), fg=MUTED, bg=BG).pack(side="right")
        self.search = tk.Entry(
            toolbar,
            textvariable=self.search_var,
            font=(FONT, 11),
            bg=CARD,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=ACCENT,
            bd=0,
        )
        self.search.pack(side="left", fill="x", expand=True, padx=(10, 12), ipady=6)
        self.search_var.trace_add("write", self._on_search)

        self.folder_label = tk.Label(
            main,
            textvariable=self.folder_var,
            font=(FONT, 9),
            fg=FAINT,
            bg=BG,
            anchor="w",
        )
        self.folder_label.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 0))
        main.bind("<Configure>", self._fit_folder_label, add="+")

        canvas_wrap = tk.Frame(main, bg=BG)
        canvas_wrap.grid(row=2, column=0, sticky="nsew", padx=(10, 0), pady=(8, 0))
        canvas_wrap.grid_rowconfigure(0, weight=1)
        canvas_wrap.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(canvas_wrap, bg=BG, highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(
            canvas_wrap,
            orient="vertical",
            style="Pad.Vertical.TScrollbar",
            command=self.canvas.yview,
        )
        self.pad_frame = tk.Frame(self.canvas, bg=BG)
        self._canvas_window = self.canvas.create_window((0, 0), window=self.pad_frame, anchor="nw")
        self.pad_frame.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.grid(row=0, column=0, sticky="nsew")

    def _build_status(self) -> None:
        status = tk.Frame(self, bg=PANEL, height=36)
        status.grid(row=3, column=0, sticky="ew")
        status.grid_propagate(False)
        self.dot = tk.Label(status, text="●", font=(FONT, 9), fg=FAINT, bg=PANEL)
        self.dot.pack(side="left", padx=(16, 6))
        tk.Label(
            status, textvariable=self.now_var, font=(FONT, 10), fg=TEXT, bg=PANEL, anchor="w"
        ).pack(side="left")
        tk.Label(
            status, textvariable=self.hint_var, font=(FONT, 9), fg=MUTED, bg=PANEL, anchor="e"
        ).pack(side="right", padx=16)

    def _build_menu(self) -> None:
        self.menu = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT, activeforeground="#ffffff", bd=0)
        self.menu.add_command(label="Set key", command=self._start_key_capture)
        self.menu.add_command(label="Clear key", command=self._clear_key)
        self.menu.add_separator()
        self.menu.add_command(label="Show in file manager", command=self._reveal)
        self.menu.add_command(label="Copy path", command=self._copy_path)

    def _style_scrollbar(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            return
        style.configure(
            "Pad.Vertical.TScrollbar",
            background=CARD,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=MUTED,
            lightcolor=CARD,
            darkcolor=CARD,
            gripcount=0,
            arrowsize=12,
        )
        style.map(
            "Pad.Vertical.TScrollbar",
            background=[("active", "#343446"), ("pressed", ACCENT)],
        )

    def _set_icon(self) -> None:
        image = tk.PhotoImage(width=16, height=16)
        image.put(ACCENT, to=(0, 0, 16, 16))
        image.put(BG, to=(4, 4, 12, 12))
        self._icon = image
        self.iconphoto(True, image)

    def _bind_keys(self) -> None:
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.bind("<Escape>", self._on_escape)
        self.bind("<Control-f>", self._focus_search)
        self.bind("<Control-o>", self._add_folder_event)
        self.bind("<F5>", lambda _event: self._rescan())

    def _restore_window(self) -> None:
        self.geometry("1180x760")
        if self.cache.window_state == "zoomed":
            try:
                self.state("zoomed")
            except tk.TclError:
                pass

    def _apply_devices(self) -> None:
        level = int(float(self.vol_slider.get())) / 100
        if self.engine is not None:
            self.engine.close()
            self.engine = None
        self.devices = describe_devices()
        self.engine = PlaybackEngine(self.devices.mic_index, self.devices.monitor_index)
        self.engine.volume = level
        self._render_device_labels()

    def _queue_devices(self, status) -> None:
        self._inbox.put(("devices", status))

    def _show_devices(self, status) -> None:
        self.devices = status
        self._render_device_labels()

    def _render_device_labels(self) -> None:
        status = self.devices
        if status.warning:
            self.header_status.configure(text="Default output", fg=WARN)
        else:
            self.header_status.configure(text="Sonar connected", fg=OK)
        self.mic_value.configure(text=status.mic_name)
        self.monitor_value.configure(text=status.monitor_name)
        self.device_warning.configure(text=status.warning or "")

    def _device_label(self, parent: tk.Frame, font=(FONT, 9), fg=TEXT) -> tk.Label:
        return tk.Label(
            parent,
            font=font,
            fg=fg,
            bg=SIDEBAR,
            anchor="w",
            wraplength=SIDEBAR_WIDTH - 40,
        )

    def _render_folders(self) -> None:
        for child in self.folder_frame.winfo_children():
            child.destroy()
        records = list(self.cache.folders)
        if not records:
            tk.Label(
                self.folder_frame,
                text="Folders you add are remembered on this PC.",
                font=(FONT, 9),
                fg=MUTED,
                bg=SIDEBAR,
                wraplength=240,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=16, pady=8)
            return

        titles = _folder_titles([record.path for record in records])
        current = folder_key(self.current_folder) if self.current_folder else None
        for record in records:
            self._make_folder_row(
                record,
                titles.get(record.path, Path(record.path).name),
                selected=current == folder_key(record.path),
            )

    def _make_folder_row(self, record: FolderRecord, title: str, selected: bool) -> None:
        exists = os.path.isdir(record.path)
        row = tk.Frame(self.folder_frame, bg=SELECTED if selected else SIDEBAR, cursor="hand2")
        row.pack(fill="x", padx=10, pady=2)
        body = tk.Frame(row, bg=row["bg"])
        body.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=6)
        name = tk.Label(
            body,
            text=title if exists else f"{title}  ·  missing",
            font=(FONT, 10, "bold"),
            fg=TEXT if exists else FAINT,
            bg=row["bg"],
            anchor="w",
        )
        name.pack(fill="x")
        detail = "missing" if not exists else f"{record.file_count} clip" + ("s" if record.file_count != 1 else "")
        meta = tk.Label(
            body,
            text=f"{detail}   {_short_path(record.path, 46)}",
            font=(FONT, 8),
            fg=MUTED if exists else FAINT,
            bg=row["bg"],
            anchor="w",
        )
        meta.pack(fill="x")
        remove = flat_button(
            row,
            "×",
            lambda path=record.path: self._remove_folder(path),
            row["bg"],
            MUTED,
            (FONT, 12),
            DANGER,
            padx=8,
            pady=4,
        )
        remove.pack(side="right", padx=4)
        for widget in (row, body, name, meta):
            widget.bind("<Button-1>", lambda _event, path=record.path: self._load_folder(path))
            widget.bind("<Enter>", lambda _event, widgets=(row, body, name, meta, remove), on=selected: self._paint_folder(widgets, True, on))
            widget.bind("<Leave>", lambda _event, widgets=(row, body, name, meta, remove), on=selected: self._paint_folder(widgets, False, on))

    def _paint_folder(self, widgets, hover: bool, selected: bool) -> None:
        row, body, name, meta, remove = widgets
        pointer = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        inside = pointer is not None and _widget_in(pointer, row)
        bg = SELECTED if selected else CARD_HOVER if (hover or inside) else SIDEBAR
        for widget in (row, body, name, meta):
            widget.configure(bg=bg)
        remove.configure(bg=bg)

    def _load_folder(self, path: str, *, stop_playback: bool | None = None) -> None:
        path = clean_path(path)
        if not os.path.isdir(path):
            messagebox.showerror(
                "Missing folder",
                f"That folder is no longer available:\n{path}",
                parent=self,
            )
            return
        same = self.current_folder is not None and folder_key(self.current_folder) == folder_key(path)
        if stop_playback is None:
            stop_playback = not same
        if stop_playback:
            self._stop()
        self.current_folder = path
        self._fit_folder_label()
        self._render_folders()
        self._scan_token += 1
        token = self._scan_token
        if self.active_path is None:
            self.now_var.set("Scanning…")
        self._set_persistent_hint("Scanning subfolders…")
        threading.Thread(target=self._scan_worker, args=(token, path), daemon=True).start()

    def _add_folder(self) -> None:
        initial = self.current_folder
        if initial is None and self.cache.folders:
            initial = self.cache.folders[0].path
        if initial and not os.path.isdir(initial):
            initial = None
        path = filedialog.askdirectory(
            title="Add audio folder",
            initialdir=initial or os.path.expanduser("~"),
            parent=self,
        )
        if path:
            self._load_folder(path, stop_playback=True)

    def _add_folder_event(self, _event=None):
        self._add_folder()
        return "break"

    def _rescan(self) -> None:
        if self.current_folder:
            self._load_folder(self.current_folder, stop_playback=False)

    def _remove_folder(self, path: str) -> None:
        name = Path(path).name or path
        if not messagebox.askyesno(
            "Remove folder",
            f"Remove {name} from the library?\n\nAudio files stay on disk.",
            parent=self,
        ):
            return
        self.cache.remove(path)
        if self.current_folder and folder_key(self.current_folder) == folder_key(path):
            self._scan_token += 1
            self._stop()
            self.current_folder = None
            self.files = []
            self.folder_var.set("Add a folder to start")
            self._rebuild_pads()
        self._render_folders()

    def _autoload(self) -> None:
        last = self.cache.last_folder
        if last and os.path.isdir(last):
            self._load_folder(last, stop_playback=True)

    def _scan_worker(self, token: int, path: str) -> None:
        try:
            result = scan_folder(path)
        except Exception as exc:
            self._inbox.put(("scan-error", (token, path, str(exc))))
            return
        self._inbox.put(("scan", (token, path, result)))

    def _scan_done(self, token: int, path: str, result) -> None:
        if token != self._scan_token:
            return
        self.current_folder = path
        self.files = result.files
        self.cache.remember(path, len(result.files))
        if result.skipped_dirs:
            self._set_persistent_hint(
                f"Skipped {result.skipped_dirs} unreadable folder"
                + ("s" if result.skipped_dirs != 1 else "")
            )
        else:
            self._set_persistent_hint("")
        if self.active_path and folder_key(self.active_path) not in {folder_key(item) for item in self.files}:
            self._stop()
        elif self.active_path is None:
            self.now_var.set("Ready")
        self._render_folders()
        self._fit_folder_label()
        if self.search_var.get():
            self.search_var.set("")
        else:
            self._rebuild_pads()
        self.canvas.yview_moveto(0)

    def _scan_failed(self, token: int, path: str, message: str) -> None:
        if token != self._scan_token:
            return
        self._set_persistent_hint("")
        if self.active_path is None:
            self.now_var.set("Ready")
        messagebox.showerror("Couldn't scan folder", f"{path}\n\n{message}", parent=self)

    def _filtered_files(self) -> list[str]:
        query = self.search_var.get().strip().casefold()
        if not query or not self.current_folder:
            return list(self.files)
        root = self.current_folder
        return [
            path
            for path in self.files
            if query in os.path.relpath(path, root).casefold()
        ]

    def _rebuild_pads(self) -> None:
        if self._rebuilding:
            return
        self._rebuilding = True
        try:
            for child in self.pad_frame.winfo_children():
                child.destroy()
            self.pads = {}
            visible = self._filtered_files()
            self._update_count(len(visible))
            if not self.current_folder:
                self._empty_label("Add a folder. Every subfolder is scanned, and the folder stays in the library.")
                return
            if not self.files:
                self._empty_label(f"No {EXTENSION_LABEL} files in this folder.")
                return
            if not visible:
                self._empty_label("No clips match that search.")
                return

            cols = max(1, self._cols or 1)
            self._configure_pad_columns(cols)
            row_index = 0
            for key, paths in group_by_folder(self.current_folder, visible):
                header = tk.Frame(self.pad_frame, bg=BG)
                header.grid(row=row_index, column=0, columnspan=cols, sticky="ew", padx=8, pady=(14, 4))
                color = _group_color(key)
                tk.Frame(header, bg=color, width=3).pack(side="left", fill="y", padx=(2, 8))
                tk.Label(
                    header,
                    text=pretty_relative(key) or "This folder",
                    font=(FONT, 11, "bold"),
                    fg=TEXT,
                    bg=BG,
                ).pack(side="left")
                tk.Label(
                    header,
                    text=str(len(paths)),
                    font=(FONT, 9),
                    fg=MUTED,
                    bg=BG,
                ).pack(side="left", padx=8)
                row_index += 1
                for index, path in enumerate(paths):
                    row, column = divmod(index, cols)
                    pad = self._make_pad(path, color)
                    pad.shell.grid(row=row_index + row, column=column, padx=6, pady=6, sticky="nw")
                    self.pads[path] = pad
                    self._paint_pad(pad)
                row_index += (len(paths) + cols - 1) // cols
            tk.Frame(self.pad_frame, bg=BG, height=12).grid(row=row_index, column=0)
        finally:
            self._rebuilding = False

    def _empty_label(self, text: str) -> None:
        tk.Label(
            self.pad_frame,
            text=text,
            font=(FONT, 12),
            fg=MUTED,
            bg=BG,
            wraplength=460,
            justify="left",
        ).grid(row=0, column=0, padx=24, pady=48, sticky="nw")

    def _make_pad(self, path: str, color: str) -> Pad:
        shell = tk.Frame(self.pad_frame, bg=CARD, width=PAD_WIDTH, height=PAD_HEIGHT)
        shell.pack_propagate(False)
        stripe = tk.Frame(shell, bg=color, width=4)
        stripe.pack(side="left", fill="y")
        stripe.pack_propagate(False)
        body = tk.Frame(shell, bg=CARD)
        body.pack(side="left", fill="both", expand=True)
        combo = self.cache.combo_for(path)
        key_label = tk.Label(
            body,
            text=display_combo(combo) if combo else "",
            font=(FONT, 8),
            fg=ACCENT,
            bg=CARD,
            anchor="e",
            padx=6,
        )
        button = tk.Button(
            body,
            text=_clip_text(Path(path).stem, 42),
            font=(FONT, 10, "bold"),
            bg=CARD,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            highlightthickness=0,
            wraplength=PAD_WIDTH - 28,
            cursor="hand2",
            command=lambda current=path: self._play(current),
        )
        if combo:
            key_label.pack(side="bottom", fill="x")
        button.pack(side="top", fill="both", expand=True)
        pad = Pad(path, shell, body, stripe, button, key_label)
        for widget in (shell, stripe, body, button, key_label):
            widget.bind("<Enter>", lambda _event, current=pad: self._on_pad_enter(current))
            widget.bind("<Leave>", lambda _event, current=pad: self._on_pad_leave(current))
            widget.bind("<Button-3>", lambda event, current=path: self._show_menu(event, current))
        return pad

    def _paint_pad(self, pad: Pad, hovered: bool = False) -> None:
        active = pad.path == self.active_path
        if active:
            bg, fg = ACCENT, "#ffffff"
        elif hovered:
            bg, fg = CARD_HOVER, TEXT
        else:
            bg, fg = CARD, TEXT
        pad.shell.configure(bg=bg)
        pad.body.configure(bg=bg)
        pad.button.configure(bg=bg, fg=fg)
        pad.key_label.configure(bg=bg, fg="#ffffff" if active else ACCENT)

    def _on_pad_enter(self, pad: Pad) -> None:
        self._paint_pad(pad, hovered=True)
        if self.current_folder:
            relative = pretty_relative(os.path.relpath(pad.path, self.current_folder))
            self._set_hover_hint(relative)

    def _on_pad_leave(self, pad: Pad) -> None:
        pointer = _pointer_widget(self)
        if pointer is not None and _widget_in(pointer, pad.shell):
            return
        self._paint_pad(pad)
        self._set_hover_hint("")

    def _play(self, path: str) -> None:
        if path == self.active_path:
            self._stop()
            return
        self._token += 1
        token = self._token
        self._mark_active(path)
        if self.engine is not None:
            self.engine.stop()
        threading.Thread(target=self._load_worker, args=(token, path), daemon=True).start()

    def _load_worker(self, token: int, path: str) -> None:
        try:
            audio = load_audio(path)
        except Exception as exc:
            self._inbox.put(("play-error", (token, path, str(exc))))
            return
        self._inbox.put(("play", (token, path, audio)))

    def _play_ready(self, token: int, path: str, data, samplerate: int) -> None:
        if token != self._token or self.engine is None:
            return
        try:
            self.engine.play(data, samplerate)
        except Exception as exc:
            self._play_failed(token, path, str(exc))
            return
        if self.engine.monitor_warning:
            self._set_persistent_hint(self.engine.monitor_warning)

    def _play_failed(self, token: int, path: str, message: str) -> None:
        if token != self._token:
            return
        self._clear_active_ui()
        messagebox.showerror("Playback failed", f"{Path(path).name}\n\n{message}", parent=self)

    def _mark_active(self, path: str) -> None:
        previous = self.active_path
        self.active_path = path
        if previous in self.pads:
            self._paint_pad(self.pads[previous])
        if path in self.pads:
            self._paint_pad(self.pads[path])
        self.now_var.set(self._clip_label(path))
        self.dot.configure(fg=ACCENT)

    def _stop(self) -> None:
        self._token += 1
        if self.engine is not None:
            self.engine.stop()
        self._clear_active_ui()

    def _clear_active_ui(self) -> None:
        previous = self.active_path
        self.active_path = None
        if previous in self.pads:
            self._paint_pad(self.pads[previous])
        self.now_var.set("Ready")
        self.dot.configure(fg=FAINT)

    def _clip_label(self, path: str) -> str:
        if self.current_folder:
            relative = os.path.relpath(path, self.current_folder)
        else:
            relative = os.path.basename(path)
        parent = Path(relative).parent.as_posix()
        name = Path(relative).stem
        if parent in {"", "."}:
            return name
        return f"{pretty_relative(parent)}   /   {name}"

    def _on_volume(self, value: str) -> None:
        level = int(float(value))
        self.cache.volume = level
        if self.engine is not None:
            self.engine.volume = level / 100
        self.vol_var.set(f"{level}%")

    def _on_search(self, *_args) -> None:
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(80, self._apply_search)

    def _apply_search(self) -> None:
        self._search_job = None
        self._rebuild_pads()

    def _on_escape(self, _event=None):
        if self.focus_get() is self.search and self.search_var.get():
            self.search_var.set("")
            return "break"
        self._stop()
        return "break"

    def _focus_search(self, _event=None):
        self.search.focus_set()
        self.search.select_range(0, "end")
        return "break"

    def _on_wheel(self, event) -> None:
        widget = _pointer_widget(self, event.x_root, event.y_root)
        canvas = _ancestor_canvas(widget, (self.canvas, self.folder_canvas))
        if canvas is None or not event.delta:
            return
        steps = int(-event.delta / 120) or (-1 if event.delta > 0 else 1)
        canvas.yview_scroll(steps, "units")

    def _on_canvas_configure(self, event) -> None:
        if event.widget is not self.canvas or event.width <= 1:
            return
        self.canvas.itemconfigure(self._canvas_window, width=event.width)
        cols = max(1, event.width // (PAD_WIDTH + PAD_GAP_X))
        if cols != self._cols:
            self._cols = cols
            self._rebuild_pads()

    def _configure_pad_columns(self, cols: int) -> None:
        # Drop weight from earlier layouts so a narrower panel wraps tiles
        # instead of squeezing the ones that are already on screen.
        for column in range(max(self._configured_cols, cols)):
            self.pad_frame.columnconfigure(column, weight=0, minsize=0, uniform="")
        self._configured_cols = cols

    def _size_folder_canvas(self, event) -> None:
        self.folder_canvas.itemconfigure(self._folder_window, width=event.width)

    def _fit_folder_label(self, _event=None) -> None:
        if not self.current_folder:
            self.folder_var.set("Add a folder to start")
            return
        width = max(self.folder_label.winfo_width(), 240)
        self.folder_var.set(_short_path(self.current_folder, max(24, width // 7)))

    def _update_count(self, shown: int) -> None:
        total = len(self.files)
        if total and shown != total:
            self.count_var.set(f"{shown} of {total}")
        elif total == 1:
            self.count_var.set("1 clip")
        else:
            self.count_var.set(f"{total} clips")

    def _show_menu(self, event, path: str) -> None:
        self._menu_path = path
        state = "normal" if self.cache.combo_for(path) else "disabled"
        self.menu.entryconfigure(1, state=state)
        self.menu.tk_popup(event.x_root, event.y_root)

    def _start_key_capture(self) -> None:
        path = self._menu_path
        if not path:
            return
        self._capturing_path = path
        self._set_persistent_hint(f"Press a key for {Path(path).stem}. Esc cancels.")

    def _clear_key(self) -> None:
        path = self._menu_path
        if not path:
            return
        self.cache.unbind_path(path)
        self._rebuild_pads()

    def _queue_hotkey(self, combo: str) -> None:
        self._inbox.put(("hotkey", combo))

    def _handle_hotkey(self, combo: str) -> None:
        if self._capturing_path:
            self._finish_key_capture(combo)
            return
        try:
            focused = self.focus_get()
        except tk.TclError:
            focused = None
        if focused is self.search:
            return
        path = self.cache.path_for_combo(combo)
        if path:
            self._play(path)

    def _finish_key_capture(self, combo: str) -> None:
        path = self._capturing_path
        self._capturing_path = None
        if path is None or combo == "esc":
            self._set_persistent_hint("")
            return
        self.cache.bind_key(path, combo)
        self._set_persistent_hint(f"Bound {display_combo(combo)}")
        self._rebuild_pads()

    def _reveal(self) -> None:
        path = self._menu_path
        if not path:
            return
        if sys.platform == "win32":
            subprocess.run(["explorer", "/select,", os.path.normpath(path)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", path], check=False)
        else:
            subprocess.run(["xdg-open", os.path.dirname(path)], check=False)

    def _copy_path(self) -> None:
        if not self._menu_path:
            return
        self.clipboard_clear()
        self.clipboard_append(self._menu_path)
        self._set_hover_hint("Path copied")

    def _set_persistent_hint(self, text: str) -> None:
        self._persistent_hint = text
        self._refresh_hint()

    def _set_hover_hint(self, text: str) -> None:
        self._hover_hint = text
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        self.hint_var.set(self._hover_hint or self._persistent_hint)

    def _save_preferences(self, _event=None) -> None:
        self.cache.volume = int(float(self.vol_slider.get()))
        state = self.state()
        self.cache.window_state = state if state in {"normal", "zoomed"} else "normal"
        self.cache.save()

    def _pump(self) -> None:
        if self._closing:
            return
        try:
            while True:
                action, payload = self._inbox.get_nowait()
                self._handle_async(action, payload)
        except queue.Empty:
            pass
        if self.engine is not None and self.engine.consume_natural_end():
            error = self.engine.take_error()
            self._clear_active_ui()
            self.engine.stop()
            if error:
                messagebox.showerror("Playback failed", error, parent=self)
        if not self._closing:
            self.after(16, self._pump)

    def _handle_async(self, action: str, payload) -> None:
        if action == "scan":
            self._scan_done(*payload)
        elif action == "scan-error":
            self._scan_failed(*payload)
        elif action == "play":
            token, path, audio = payload
            self._play_ready(token, path, audio[0], audio[1])
        elif action == "play-error":
            self._play_failed(*payload)
        elif action == "devices":
            self._show_devices(payload)
        elif action == "hotkey":
            self._handle_hotkey(payload)

    def _on_close(self) -> None:
        self._closing = True
        self._token += 1
        self._scan_token += 1
        if getattr(self, "_device_watcher", None) is not None:
            self._device_watcher.stop()
        if getattr(self, "_hotkeys", None) is not None:
            self._hotkeys.stop()
        try:
            self.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        try:
            self._save_preferences()
        except Exception as exc:
            log_exception(type(exc), exc, exc.__traceback__)
        try:
            if self.engine is not None:
                self.engine.close()
        finally:
            self.destroy()

    def report_callback_exception(self, exc_type, exc, tb) -> None:
        log_exception(exc_type, exc, tb)
        try:
            messagebox.showerror("Soundpad", str(exc), parent=self)
        except tk.TclError:
            return


def flat_button(parent, text, command, bg, fg, font, active_bg, **extra):
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=active_bg,
        activeforeground=fg,
        font=font,
        relief="flat",
        bd=0,
        highlightthickness=0,
        cursor="hand2",
        **extra,
    )


def _folder_titles(paths: list[str]) -> dict[str, str]:
    names = [Path(path).name or path for path in paths]
    titles = {}
    for path, name in zip(paths, names):
        if names.count(name) > 1:
            parent = Path(path).parent.name
            titles[path] = f"{parent} / {name}" if parent else name
        else:
            titles[path] = name
    return titles


def _group_color(key: str) -> str:
    if not key:
        return ACCENT
    total = sum(key.casefold().encode("utf-8", "replace"))
    return GROUP_COLORS[total % len(GROUP_COLORS)]


def _clip_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _short_path(path: str, limit: int) -> str:
    if len(path) <= limit:
        return path
    return "…" + path[-(limit - 1) :]


def _widget_in(widget, ancestor) -> bool:
    while widget is not None:
        if widget == ancestor:
            return True
        widget = getattr(widget, "master", None)
    return False


def _ancestor_canvas(widget, canvases):
    while widget is not None:
        if widget in canvases:
            return widget
        widget = getattr(widget, "master", None)
    return None


def _pointer_widget(widget, x_root=None, y_root=None):
    if x_root is None or y_root is None:
        x_root = widget.winfo_pointerx()
        y_root = widget.winfo_pointery()
    try:
        return widget.winfo_containing(x_root, y_root)
    except tk.TclError:
        return None
