"""Global key bindings that keep working when the window is in the background."""

from __future__ import annotations

MODIFIER_ORDER = ("ctrl", "alt", "shift", "cmd")
_MODIFIER_NAMES = {
    "ctrl": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "alt_gr": "alt",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "cmd": "cmd",
    "cmd_l": "cmd",
    "cmd_r": "cmd",
}
_LABELS = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "cmd": "Cmd",
    "esc": "Esc",
    "space": "Space",
    "enter": "Enter",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "page_up": "Page Up",
    "page_down": "Page Down",
    "home": "Home",
    "end": "End",
    "caps_lock": "Caps Lock",
    "insert": "Insert",
    "print_screen": "PrtSc",
    "scroll_lock": "Scroll Lock",
    "pause": "Pause",
    "num_lock": "Num Lock",
    "menu": "Menu",
}


def display_combo(combo: str) -> str:
    parts = []
    for part in combo.split("+"):
        if part in _LABELS:
            parts.append(_LABELS[part])
        elif len(part) == 1:
            parts.append(part.upper())
        elif part.startswith("f") and part[1:].isdigit():
            parts.append(part.upper())
        else:
            parts.append(part.replace("_", " ").title())
    return "+".join(parts)


class HotkeyTracker:
    """Turns key presses into one combo per physical press, ignoring repeats."""

    def __init__(self, on_combo):
        self._on_combo = on_combo
        self._pressed: set[str] = set()
        self._modifiers: set[str] = set()

    def press(self, name: str, *, modifier: bool) -> None:
        if name in self._pressed:
            return
        self._pressed.add(name)
        if modifier:
            self._modifiers.add(name)
            return
        mods = [item for item in MODIFIER_ORDER if item in self._modifiers]
        self._on_combo("+".join([*mods, name]))

    def release(self, name: str) -> None:
        self._pressed.discard(name)
        self._modifiers.discard(name)


class GlobalHotkeys:
    def __init__(self, on_combo):
        self._on_combo = on_combo
        self._tracker = HotkeyTracker(on_combo)
        self._listener = None
        self._vk_names: dict[int, tuple[str, bool]] = {}

    def start(self) -> str | None:
        try:
            from pynput import keyboard
        except Exception as exc:
            return str(exc)
        self._vk_names = _vk_names(keyboard.Key)
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        try:
            self._listener.start()
        except Exception as exc:
            self._listener = None
            return str(exc)
        return None

    def stop(self) -> None:
        listener = self._listener
        self._listener = None
        if listener is not None:
            listener.stop()

    def _on_press(self, key) -> None:
        identified = self._identify(key)
        if identified is None:
            return
        name, modifier = identified
        self._tracker.press(name, modifier=modifier)

    def _on_release(self, key) -> None:
        identified = self._identify(key)
        if identified is None:
            return
        self._tracker.release(identified[0])

    def _identify(self, key) -> tuple[str, bool] | None:
        name = getattr(key, "name", None)
        if isinstance(name, str):
            canonical = _MODIFIER_NAMES.get(name, name)
            return canonical, canonical in MODIFIER_ORDER

        canonical = key
        listener = self._listener
        if listener is not None:
            try:
                canonical = listener.canonical(key)
            except Exception:
                canonical = key
        char = getattr(canonical, "char", None) or getattr(key, "char", None)
        if isinstance(char, str) and len(char) == 1 and char.isprintable() and not char.isspace():
            return char.lower(), False
        vk = getattr(key, "vk", None)
        if isinstance(vk, int) and vk in self._vk_names:
            return self._vk_names[vk]
        return None


def _vk_names(key_enum) -> dict[int, tuple[str, bool]]:
    names: dict[int, tuple[str, bool]] = {}
    for key in key_enum:
        vk = getattr(key.value, "vk", None)
        if not isinstance(vk, int) or vk in names:
            continue
        canonical = _MODIFIER_NAMES.get(key.name, key.name)
        names[vk] = (canonical, canonical in MODIFIER_ORDER)
    return names
