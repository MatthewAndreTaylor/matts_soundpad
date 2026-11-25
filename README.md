# Matt's Soundpad

A desktop soundboard for playing audio clips into a SteelSeries Sonar virtual microphone, with optional monitoring on your speakers.

## Run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then from this folder:

```bash
uv run m-soundpad
```

## Build an exe

```bash
uv run --group build python scripts/build_exe.py
```

The executable is written to `dist/m-soundpad` (`dist/m-soundpad.exe` on Windows). It uses the same folder library as `uv run m-soundpad`.

## Library

Add a folder and the app scans it recursively for `.wav`, `.flac`, and `.ogg` files, including files in subfolders. Every folder you open is remembered in the sidebar. The library file is `%APPDATA%\m-soundpad\library.json` on Windows and `~/.m-soundpad/library.json` elsewhere. The last folder opens again on the next launch. Rescan picks up files added since the last open. Removing a folder from the library does not delete anything on disk.

Mono files are played as stereo. Sample rate follows the file.

## Controls

- Click a pad to play. Click it again to stop.
- `Esc` stops playback. `Esc` in the search box clears the search first.
- `Ctrl+F` focuses search. `Ctrl+O` adds a folder. `F5` rescans the current folder.
- Volume runs from 0% to 200%.
- Right-click a pad and choose `Set key` to bind a keyboard shortcut. The shortcut plays that clip even when the window is in the background. `Esc` cancels binding. `Clear key` removes it.

Point your voice app's microphone at the SteelSeries Sonar microphone device. If Sonar is not installed, clips play through the default output instead.

Startup failures are written to `error.log` in that same folder.
