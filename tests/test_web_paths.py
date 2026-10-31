import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mkv_seekfix import core, web


def wait(job):
    deadline = time.monotonic() + 5
    while job.running and time.monotonic() < deadline:
        time.sleep(0.01)
    return job


class SeekFixAfterRenameTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.media = Path(tmp.name).resolve() / "media"
        self.old = self.media / "Show/T1/3.mkv"
        self.old.parent.mkdir(parents=True)
        self.old.write_text("x")
        cfg = web.Config(self.media, self.media.parent / "config", "ffmpeg", "ffprobe")
        self.app = web.App(cfg)

    def fixed_paths(self, files):
        def fake(_tools, path, _backup, **_kw):
            return core.Result(path, "ok")
        with mock.patch.object(core, "fix_file", side_effect=fake) as fix:
            job = wait(self.app.seek_fix(files, False, 0))
        self.assertEqual(job.error, "")
        return [call.args[1] for call in fix.call_args_list]

    def rename(self, src, dst):
        job = wait(self.app.names_apply([{"src": str(src), "dst": str(dst), "kind": "rename"}]))
        self.assertEqual(job.result["results"][0]["status"], "ok")

    def test_old_path_follows_file_and_folder_renames(self):
        self.rename(self.old, self.old.with_name("Show - S01E03.mkv"))
        self.rename(self.media / "Show/T1", self.media / "Show/Season 01")
        new = self.media / "Show/Season 01/Show - S01E03.mkv"
        self.assertEqual(self.fixed_paths([str(self.old)]), [new])

    def test_existing_path_is_not_translated(self):
        self.rename(self.old, self.old.with_name("nuevo.mkv"))
        self.old.write_text("otro")
        self.assertEqual(self.fixed_paths([str(self.old)]), [self.old])

    def test_scan_forgets_moves(self):
        self.rename(self.old, self.old.with_name("nuevo.mkv"))
        wait(self.app.names_scan([]))
        self.assertEqual(self.fixed_paths([str(self.old)]), [self.old])


if __name__ == "__main__":
    unittest.main()
