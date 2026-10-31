import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mkv_seekfix import core, names, web


def wait(job):
    deadline = time.monotonic() + 5
    while job.running and time.monotonic() < deadline:
        time.sleep(0.01)
    return job


class WebCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.media = self.base / "media"
        self.media.mkdir()
        self.cfg = web.Config(self.media, self.base / "config", "ffmpeg", "ffprobe")
        self.app = web.App(self.cfg)


class BackupEmptyTests(WebCase):
    def test_refuses_library_folder_and_keeps_its_files(self):
        episode = self.media / "Serie/ep01.mkv"
        episode.parent.mkdir()
        episode.write_text("x")
        self.cfg.settings["folders"] = [{"path": str(self.media / "Serie"), "kind": "series"}]
        self.cfg.settings["backup_dir"] = str(self.media / "Serie")
        with self.assertRaises(ValueError):
            self.app.backup_empty()
        self.assertTrue(episode.exists())

    def test_refuses_unclaimed_folder_with_foreign_files(self):
        other = self.base / "compartida"
        other.mkdir()
        (other / "foto.jpg").write_text("x")
        self.cfg.settings["backup_dir"] = str(other)
        with self.assertRaises(ValueError):
            self.app.backup_empty()
        self.assertTrue((other / "foto.jpg").exists())

    def test_default_folder_is_emptied(self):
        backup = Path(self.cfg.settings["backup_dir"])
        (backup / "media/Serie").mkdir(parents=True)
        (backup / "media/Serie/ep01.mkv").write_text("x")
        self.assertEqual(self.app.backup_empty()["files"], 1)
        self.assertEqual(list(backup.iterdir()), [])

    def test_folder_empty_when_first_used_can_be_emptied_later(self):
        backup = self.base / "respaldo"
        backup.mkdir()
        self.cfg.settings["backup_dir"] = str(backup)
        source = self.media / "ep01.mkv"
        source.write_text("x")
        with mock.patch.object(core, "fix_file", return_value=core.Result(source, "ok")):
            wait(self.app.seek_fix([str(source)], False, 0))
        (backup / "media").mkdir()
        (backup / "media/ep01.mkv").write_text("x")
        self.assertEqual(self.app.backup_empty()["files"], 1)
        self.assertEqual(list(backup.iterdir()), [])

    def test_claim_does_not_follow_a_changed_setting(self):
        backup = self.base / "respaldo"
        backup.mkdir()
        self.cfg.settings["backup_dir"] = str(backup)
        source = self.media / "ep01.mkv"
        source.write_text("x")
        with mock.patch.object(core, "fix_file", return_value=core.Result(source, "ok")):
            wait(self.app.seek_fix([str(source)], False, 0))
        other = self.base / "otra"
        other.mkdir()
        (other / "doc.txt").write_text("x")
        self.cfg.settings["backup_dir"] = str(other)
        with self.assertRaises(ValueError):
            self.app.backup_empty()
        self.assertTrue((other / "doc.txt").exists())


class BackupDirValidationTests(WebCase):
    def assert_rejected(self, path):
        settings = {**self.cfg.settings, "backup_dir": str(path)}
        self.assertTrue(self.cfg.backup_dir_problem(settings), path)

    def test_rejects_dangerous_paths(self):
        library = self.media / "Serie"
        library.mkdir()
        self.cfg.settings["folders"] = [{"path": str(library), "kind": "series"}]
        trash = self.base / "papelera"
        self.cfg.settings["trash_dir"] = str(trash)
        for path in ("/", Path.home(), self.cfg.config_dir, self.media, library, library / "dentro",
                     self.base, trash, trash / "dentro", "relativa"):
            self.assert_rejected(path)

    def test_rejects_symlink(self):
        real = self.base / "real"
        real.mkdir()
        link = self.base / "enlace"
        link.symlink_to(real)
        self.assert_rejected(link)

    def test_accepts_dedicated_folder(self):
        self.assertIsNone(self.cfg.backup_dir_problem(self.cfg.settings))
        other = {**self.cfg.settings, "backup_dir": str(self.base / "respaldo")}
        self.assertIsNone(self.cfg.backup_dir_problem(other))

    def test_saving_dangerous_folder_is_refused_and_keeps_previous(self):
        before = self.cfg.settings["backup_dir"]
        with self.assertRaises(ValueError):
            self.app.save_settings({"backup_dir": str(self.media)})
        self.assertEqual(self.cfg.settings["backup_dir"], before)

    def test_saving_library_over_backup_is_refused(self):
        with self.assertRaises(ValueError):
            self.app.save_settings({"folders": [{"path": self.cfg.settings["backup_dir"], "kind": "series"}]})
        self.assertEqual(self.cfg.settings["folders"], [])

    def test_saving_dedicated_folder_works(self):
        target = str(self.base / "respaldo")
        self.app.save_settings({"backup_dir": target, "tmdb_language": "en-US"})
        self.assertEqual(self.cfg.settings["backup_dir"], target)
        self.assertEqual(self.cfg.settings["tmdb_language"], "en-US")

    def test_seek_fix_refuses_dangerous_folder(self):
        self.cfg.settings["backup_dir"] = str(self.media)
        source = self.media / "ep01.mkv"
        source.write_text("x")
        with self.assertRaises(ValueError):
            self.app.seek_fix([str(source)], False, 0)


if __name__ == "__main__":
    unittest.main()
