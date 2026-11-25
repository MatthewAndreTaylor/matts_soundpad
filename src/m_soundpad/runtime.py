"""Failure logging for the soundpad window."""

from __future__ import annotations

import traceback
from datetime import datetime


def log_exception(exc_type, exc, tb) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        from m_soundpad.library import default_cache_dir

        path = default_cache_dir() / "error.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{stamp}]\n{text}\n")
    except Exception:
        return


def emergency_dialog(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Matt's Soundpad", message, parent=root)
        root.destroy()
    except Exception:
        return
