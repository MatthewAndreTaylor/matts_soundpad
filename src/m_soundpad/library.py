"""Folder memory and recursive audio discovery."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

AUDIO_EXTENSIONS = {".wav", ".flac", ".ogg"}
MAX_FOLDERS = 40


@dataclass(frozen=True)
class ScanResult:
    files: list[str]
    skipped_dirs: int


@dataclass
class FolderRecord:
    path: str
    last_opened: str
    file_count: int


def default_cache_dir() -> Path:
    override = os.environ.get("M_SOUNDPAD_CACHE_DIR")
    if override:
        path = Path(override)
        path.mkdir(parents=True, exist_ok=True)
        return path

    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "m-soundpad")
    candidates.append(Path.home() / ".m-soundpad")
    candidates.append(Path.cwd() / ".m-soundpad")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return Path.cwd()


def default_cache_path() -> Path:
    return default_cache_dir() / "library.json"


def clean_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def folder_key(path: str) -> str:
    return os.path.normcase(clean_path(path))


def pretty_relative(path: str) -> str:
    parts = [part for part in path.replace("\\", "/").split("/") if part and part != "."]
    return " / ".join(parts)


def scan_folder(root: str) -> ScanResult:
    """Return supported audio files under root, including subfolders."""
    root = clean_path(root)
    if not os.path.isdir(root):
        raise FileNotFoundError(root)

    files: list[str] = []
    skipped = 0

    def onerror(_err: OSError) -> None:
        nonlocal skipped
        skipped += 1

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and name not in {"__pycache__", "node_modules"}
        )
        for name in filenames:
            if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                files.append(os.path.join(dirpath, name))

    files.sort(key=lambda path: os.path.relpath(path, root).casefold())
    return ScanResult(files, skipped)


def group_by_folder(root: str, files: list[str]) -> list[tuple[str, list[str]]]:
    """Group files by folder relative to root. The root group comes first."""
    groups: dict[str, list[str]] = {}
    for path in files:
        relative_dir = os.path.relpath(os.path.dirname(path), root)
        key = "" if relative_dir == "." else relative_dir
        groups.setdefault(key, []).append(path)
    ordered = sorted(groups, key=lambda key: (key != "", key.casefold()))
    return [(key, groups[key]) for key in ordered]


class LibraryCache:
    """Persists folders that have been opened, plus a few window preferences."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_cache_path()
        self.folders: list[FolderRecord] = []
        self.last_folder: str | None = None
        self.window_state = "normal"
        self.keybinds: dict[str, str] = {}
        self._volume = 100
        self.load()

    @property
    def volume(self) -> int:
        return self._volume

    @volume.setter
    def volume(self, value: int) -> None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = 100
        self._volume = max(0, min(200, number))

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError, UnicodeError):
            return
        if not isinstance(raw, dict):
            return

        parsed: list[FolderRecord] = []
        for item in raw.get("folders") or []:
            record = _parse_folder(item)
            if record is not None:
                parsed.append(record)

        seen: set[str] = set()
        unique: list[FolderRecord] = []
        for item in sorted(parsed, key=lambda entry: entry.last_opened, reverse=True):
            key = folder_key(item.path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        self.folders = unique[:MAX_FOLDERS]

        last = raw.get("last_folder")
        self.last_folder = clean_path(last) if isinstance(last, str) and last.strip() else None
        state = raw.get("window_state")
        self.window_state = state if state in {"normal", "zoomed"} else "normal"
        self.volume = raw.get("volume", 100)
        self.keybinds = _parse_keybinds(raw.get("keybinds"))

    def save(self) -> bool:
        payload = {
            "last_folder": self.last_folder,
            "volume": self.volume,
            "window_state": self.window_state,
            "keybinds": self.keybinds,
            "folders": [
                {
                    "path": record.path,
                    "last_opened": record.last_opened,
                    "file_count": record.file_count,
                }
                for record in self.folders
            ],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            return False
        return True

    def remember(self, path: str, file_count: int) -> None:
        cleaned = clean_path(path)
        key = folder_key(cleaned)
        opened = datetime.now().astimezone().isoformat(timespec="seconds")
        others = [record for record in self.folders if folder_key(record.path) != key]
        others.append(FolderRecord(cleaned, opened, max(0, int(file_count))))
        others.sort(key=lambda record: record.last_opened, reverse=True)
        self.folders = others[:MAX_FOLDERS]
        self.last_folder = cleaned
        self.save()

    def combo_for(self, path: str) -> str | None:
        key = folder_key(path)
        for combo, bound in self.keybinds.items():
            if folder_key(bound) == key:
                return combo
        return None

    def path_for_combo(self, combo: str) -> str | None:
        return self.keybinds.get(combo)

    def bind_key(self, path: str, combo: str) -> None:
        cleaned = clean_path(path)
        target = folder_key(cleaned)
        self.keybinds = {
            existing: bound
            for existing, bound in self.keybinds.items()
            if existing != combo and folder_key(bound) != target
        }
        self.keybinds[combo] = cleaned
        self.save()

    def unbind_path(self, path: str) -> None:
        target = folder_key(path)
        self.keybinds = {
            combo: bound for combo, bound in self.keybinds.items() if folder_key(bound) != target
        }
        self.save()

    def remove(self, path: str) -> None:
        key = folder_key(path)
        self.folders = [record for record in self.folders if folder_key(record.path) != key]
        if self.last_folder and folder_key(self.last_folder) == key:
            self.last_folder = self.folders[0].path if self.folders else None
        self.save()


def _parse_keybinds(raw) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    binds: dict[str, str] = {}
    for combo, path in raw.items():
        if not isinstance(combo, str) or not isinstance(path, str):
            continue
        combo = combo.strip().lower()
        if combo and path.strip():
            binds[combo] = clean_path(path)
    return binds


def _parse_folder(item) -> FolderRecord | None:
    if isinstance(item, str) and item.strip():
        return FolderRecord(clean_path(item), "", 0)
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        return None
    try:
        count = int(item.get("file_count") or 0)
    except (TypeError, ValueError):
        count = 0
    opened = item.get("last_opened")
    return FolderRecord(
        clean_path(item["path"]),
        opened if isinstance(opened, str) else "",
        max(0, count),
    )
