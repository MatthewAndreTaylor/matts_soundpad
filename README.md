# Matts SoundPad

Matts SoundPad is a Tkinter-based desktop soundboard for quickly playing audio clips from a folder. It is designed around a SteelSeries Sonar virtual microphone output so you can trigger clips into voice chat while also optionally monitoring them on your local speakers.

## Features

- Play a clip by clicking its pad.
- Click the same pad again to stop playback.
- Stop all playback with the `STOP` button.
- Adjust playback volume from `0%` to `200%`.

## Requirements

- Python 3.10 or newer.
- A working audio setup with access to a SteelSeries Sonar microphone output device. (Steel Series GG app)
- Audio clips must be stereo. Mono files are not supported by the current loader.

## Quickstart

```bash
pip install numpy sounddevice soundfile
```

```bash
python main.py
```

## Supported Audio

The app loads files with these extensions:

- `.wav`
- `.flac`
- `.ogg`

Each file must contain 2 channels. If a file is mono or otherwise not stereo, playback will fail.

## Device Notes

- The app searches for a device whose name contains `SteelSeries Sonar - Microphone`.
- If that device is not found, the app shows `No output device found`.
- When a separate speaker device is available, the app mirrors playback to it as well.

## Project Files

- `main.py`: current app entry point.
- `main-v0.py`, `main-v1.py`, `main-v2.py`: earlier versions kept in the workspace for reference.