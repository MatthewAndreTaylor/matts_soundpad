"""Build a windowed executable with PyInstaller.

Run from the project root:

    uv run --group build python scripts/build_exe.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "PyInstaller is not installed.\n"
            "Build with: uv run --group build python scripts/build_exe.py",
            file=sys.stderr,
        )
        return 1

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--noupx",
        "--onefile",
        "--windowed",
        "--name",
        "m-soundpad",
        "--paths",
        str(ROOT / "src"),
        str(ROOT / "src" / "m_soundpad" / "__main__.py"),
    ]
    executable = "m-soundpad.exe" if sys.platform == "win32" else "m-soundpad"
    print(f"Building dist/{executable}")
    result = subprocess.call(command, cwd=ROOT)
    if result == 0:
        print(ROOT / "dist" / executable)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
