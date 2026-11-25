import os
import tempfile
import unittest
from pathlib import Path

from m_soundpad.library import (
    LibraryCache,
    clean_path,
    group_by_folder,
    pretty_relative,
    scan_folder,
)


class LibraryTests(unittest.TestCase):
    def test_scan_includes_nested_audio_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Root.WAV").write_bytes(b"")
            nested = root / "sub" / "deep"
            nested.mkdir(parents=True)
            (nested / "b.flac").write_bytes(b"")
            (nested / "notes.txt").write_text("skip", encoding="utf-8")
            hidden = root / ".secret"
            hidden.mkdir()
            (hidden / "c.ogg").write_bytes(b"")

            result = scan_folder(str(root))

        relative = [os.path.relpath(path, root) for path in result.files]
        self.assertEqual(
            relative,
            [
                os.path.join("Root.WAV"),
                os.path.join("sub", "deep", "b.flac"),
            ],
        )
        self.assertEqual(relative, sorted(relative, key=str.casefold))

    def test_groups_put_the_root_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = [
                str(root / "sub" / "b.wav"),
                str(root / "a.wav"),
                str(root / "sub" / "a.ogg"),
            ]
            groups = group_by_folder(str(root), files)
        self.assertEqual([key for key, _paths in groups], ["", "sub"])
        self.assertEqual(len(groups[1][1]), 2)

    def test_pretty_relative_uses_spaced_separators(self):
        self.assertEqual(pretty_relative(""), "")
        self.assertEqual(pretty_relative(r"memes\reactions"), "memes / reactions")

    def test_cache_remembers_updates_and_removes_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "sounds"
            second = root / "more"
            cache = LibraryCache(root / "library.json")
            cache.remember(str(first), 2)
            cache.remember(str(first), 5)
            cache.volume = 80
            cache.remember(str(second), 1)

            again = LibraryCache(root / "library.json")
            self.assertEqual(len(again.folders), 2)
            self.assertEqual(again.last_folder, clean_path(str(second)))
            self.assertEqual(again.folders[0].path, again.last_folder)
            self.assertEqual(again.volume, 80)
            remembered = next(item for item in again.folders if item.path == clean_path(str(first)))
            self.assertEqual(remembered.file_count, 5)

            again.remove(str(second))
            reloaded = LibraryCache(root / "library.json")
            self.assertEqual(len(reloaded.folders), 1)
            self.assertEqual(reloaded.last_folder, clean_path(str(first)))

    def test_cache_keeps_the_newest_forty_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = LibraryCache(root / "library.json")
            for index in range(45):
                cache.remember(str(root / str(index)), index)
            self.assertEqual(len(cache.folders), 40)
            self.assertEqual(cache.last_folder, clean_path(str(root / "44")))
            self.assertTrue(all(clean_path(str(root / "3")) != item.path for item in cache.folders))

    def test_corrupt_cache_starts_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "library.json"
            path.write_text("{", encoding="utf-8")
            cache = LibraryCache(path)
        self.assertEqual(cache.folders, [])
        self.assertEqual(cache.volume, 100)
        self.assertIsNone(cache.last_folder)

    def test_keybinds_round_trip_and_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one.wav"
            second = root / "two.wav"
            cache = LibraryCache(root / "library.json")
            cache.bind_key(str(first), "f8")
            cache.bind_key(str(second), "f8")
            cache.bind_key(str(first), "ctrl+shift+a")

            again = LibraryCache(root / "library.json")
            self.assertEqual(again.path_for_combo("f8"), clean_path(str(second)))
            self.assertEqual(again.combo_for(str(first)), "ctrl+shift+a")
            self.assertEqual(again.path_for_combo("f8"), clean_path(str(second)))
            again.unbind_path(str(first))
            self.assertIsNone(LibraryCache(root / "library.json").combo_for(str(first)))

    def test_volume_is_clamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = LibraryCache(Path(tmp) / "library.json")
            cache.volume = 900
            self.assertEqual(cache.volume, 200)
            cache.volume = -4
            self.assertEqual(cache.volume, 0)
