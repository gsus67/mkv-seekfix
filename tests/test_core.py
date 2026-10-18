import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mkv_seekfix import cli, core

HAVE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def make_mkv(path: Path, *, seekable: bool) -> None:
    """seekable=False escribe a una tuberia: el muxer no puede reescribir el
    SeekHead y el archivo queda sin referencia al indice al principio."""
    base = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=6:r=25",
            "-f", "lavfi", "-i", "sine=d=6", "-c:v", "libx264", "-c:a", "aac"]
    if seekable:
        subprocess.run(base + [str(path)], check=True)
    else:
        with open(path, "wb") as fh:
            subprocess.run(base + ["-f", "matroska", "pipe:1"], check=True, stdout=fh)


class PureTests(unittest.TestCase):
    def test_has_front_cues_detects_id(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a.mkv"), Path(d, "b.mkv")
            a.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 50)
            b.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 20 + core.CUES_ID + b"\x00" * 5)
            self.assertFalse(core.has_front_cues(a))
            self.assertTrue(core.has_front_cues(b))

    def test_path_map(self):
        t = core.Tools.from_strings(path_maps=["/data/media=/media"])
        self.assertEqual(t.translate(Path("/data/media/x/y.mkv")), "/media/x/y.mkv")
        self.assertEqual(t.translate(Path("/data/mediax/y.mkv")), "/data/mediax/y.mkv")
        with self.assertRaises(ValueError):
            core.Tools.from_strings(path_maps=["sin-igual"])

    def test_find_mkv_ignores_other_files(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.mkv").touch()
            Path(d, "b.mp4").touch()
            Path(d, "c.mkv" + core.TMP_SUFFIX).touch()
            sub = Path(d, "s")
            sub.mkdir()
            Path(sub, "d.MKV").touch()
            names = sorted(p.name for p in core.find_mkv([Path(d)]))
            self.assertEqual(names, ["a.mkv", "d.MKV"])


@unittest.skipUnless(HAVE_FFMPEG, "requiere ffmpeg y ffprobe")
class FfmpegTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.backup = self.dir / "backup"
        self.tools = core.Tools()

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixture_is_actually_defective(self):
        bad = self.dir / "bad.mkv"
        make_mkv(bad, seekable=False)
        self.assertFalse(core.has_front_cues(bad))

    def test_fix_repairs_and_backs_up(self):
        f = self.dir / "serie" / "ep.mkv"
        f.parent.mkdir()
        make_mkv(f, seekable=False)
        original = f.read_bytes()
        res = core.fix_file(self.tools, f, self.backup, min_age=0)
        self.assertEqual(res.status, "ok", res.detail)
        self.assertTrue(core.has_front_cues(f))
        bk = core.backup_path(self.backup, f)
        self.assertEqual(bk.read_bytes(), original)
        self.assertFalse((f.parent / (f.name + core.TMP_SUFFIX)).exists())
        d0, s0 = self.tools.probe(bk)
        d1, s1 = self.tools.probe(f)
        self.assertEqual(s0, s1)
        if d0 is not None and d1 is not None:
            self.assertAlmostEqual(d0, d1, delta=0.5)

    def test_skips_good_file_and_dry_run(self):
        good, bad = self.dir / "good.mkv", self.dir / "bad.mkv"
        make_mkv(good, seekable=True)
        make_mkv(bad, seekable=False)
        self.assertEqual(core.fix_file(self.tools, good, self.backup, min_age=0).status, "skip")
        before = bad.read_bytes()
        self.assertEqual(core.fix_file(self.tools, bad, self.backup, dry_run=True, min_age=0).status, "dry-run")
        self.assertEqual(bad.read_bytes(), before)

    def test_recent_file_is_skipped(self):
        bad = self.dir / "bad.mkv"
        make_mkv(bad, seekable=False)
        self.assertEqual(core.fix_file(self.tools, bad, self.backup, min_age=300).status, "skip")

    def test_corrupt_file_leaves_original_untouched(self):
        junk = self.dir / "junk.mkv"
        junk.write_bytes(b"no soy un mkv" * 100)
        res = core.fix_file(self.tools, junk, self.backup, min_age=0)
        self.assertEqual(res.status, "error")
        self.assertEqual(junk.read_bytes(), b"no soy un mkv" * 100)
        self.assertFalse(list(self.dir.glob("*" + core.TMP_SUFFIX)))

    def test_restore_returns_original(self):
        f = self.dir / "ep.mkv"
        make_mkv(f, seekable=False)
        original = f.read_bytes()
        self.assertEqual(core.fix_file(self.tools, f, self.backup, min_age=0).status, "ok")
        results = core.restore(self.backup)
        self.assertTrue(all(r.status == "ok" for r in results))
        self.assertEqual(f.read_bytes(), original)

    def test_cli_scan_and_fix(self):
        f = self.dir / "ep.mkv"
        make_mkv(f, seekable=False)
        self.assertEqual(cli.main(["scan", str(self.dir)]), 1)
        os.utime(f, (0, 0))
        self.assertEqual(cli.main(["fix", str(self.dir), "--backup-dir", str(self.backup),
                                   "--min-free-gb", "0"]), 0)
        self.assertEqual(cli.main(["scan", str(f)]), 0)
        self.assertEqual(cli.main(["fix", str(self.dir)]), 2)  # sin backup explicito


if __name__ == "__main__":
    unittest.main()
