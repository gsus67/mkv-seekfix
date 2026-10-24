import tempfile
import unittest
from pathlib import Path

from mkv_seekfix import names, tmdb


def touch(root: Path, *rels: str) -> None:
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def renames(plan) -> dict:
    """{origen relativo: destino relativo} de las operaciones rename."""
    return {o.src: o.dst for o in plan.ops if o.kind == "rename"}


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self):
        p = names.plan_series(self.root)
        r = {str(Path(s).relative_to(self.root)): str(Path(d).relative_to(self.root))
             for s, d in renames(p).items()}
        return p, r

    def test_number_only_in_season_folder(self):
        touch(self.root, "Spy x Family/Season 1/1.mp4", "Spy x Family/Season 1/12.mp4")
        _, r = self.plan()
        self.assertEqual(r["Spy x Family/Season 1/1.mp4"], "Spy x Family/Season 1/Spy x Family - S01E01.mp4")
        self.assertEqual(r["Spy x Family/Season 1/12.mp4"], "Spy x Family/Season 1/Spy x Family - S01E12.mp4")

    def test_loose_episodes_get_season_folder(self):
        touch(self.root, "Horimiya/01.mp4")
        _, r = self.plan()
        self.assertEqual(r["Horimiya/01.mp4"], "Horimiya/Season 01/Horimiya - S01E01.mp4")

    def test_patterns(self):
        touch(self.root,
              "Halo (2022)/Season 01/Halo - 1x01 - Contacto.mkv",
              "Fire Force (2019)/Temporada 2/Fire.Force.S02.E24.Cr.Web-Dl.1080P..mkv",
              "La que se avecina/Season 04/La que se avecina Temporada 4 Capitulo 12.mp4",
              "Anillos (2022)/Season 01/Anillos 1.1.mkv",
              "Lioness/Season 02/SOL.2.8.1080p.x264.mkv.mkv",
              "Dress/Season 01/Sono Bisque Doll Wa Koi Wo Suru - 03 [Bd-1080P][Lat+Cast].mkv",
              "Kamisama Hajimemashita/Season 2/Kamisama Hajimemashita 2.mp4")
        _, r = self.plan()
        self.assertEqual(r["Halo (2022)/Season 01/Halo - 1x01 - Contacto.mkv"],
                         "Halo (2022)/Season 01/Halo - S01E01 - Contacto.mkv")
        self.assertTrue(r["Fire Force (2019)/Temporada 2/Fire.Force.S02.E24.Cr.Web-Dl.1080P..mkv"]
                        .endswith("Fire Force - S02E24.mkv"))
        self.assertTrue(r["La que se avecina/Season 04/La que se avecina Temporada 4 Capitulo 12.mp4"]
                        .endswith("La que se avecina - S04E12.mp4"))
        self.assertTrue(r["Anillos (2022)/Season 01/Anillos 1.1.mkv"].endswith("Anillos - S01E01.mkv"))
        self.assertTrue(r["Lioness/Season 02/SOL.2.8.1080p.x264.mkv.mkv"].endswith("Lioness - S02E08.mkv"))
        self.assertTrue(r["Dress/Season 01/Sono Bisque Doll Wa Koi Wo Suru - 03 [Bd-1080P][Lat+Cast].mkv"]
                        .endswith("Dress - S01E03.mkv"))
        self.assertTrue(r["Kamisama Hajimemashita/Season 2/Kamisama Hajimemashita 2.mp4"]
                        .endswith("Kamisama Hajimemashita - S02E02.mp4"))

    def test_absolute_numbering(self):
        touch(self.root, *[f"Korra/TEMPORADA 1/La leyenda de Korra - {n:02d} [AB].mkv" for n in range(1, 13)],
              *[f"Korra/TEMPORADA 2/La leyenda de Korra - {n:02d} [AB].mkv" for n in range(13, 27)])
        _, r = self.plan()
        self.assertTrue(r["Korra/TEMPORADA 2/La leyenda de Korra - 13 [AB].mkv"].endswith("Korra - S02E01.mkv"))
        self.assertTrue(r["Korra/TEMPORADA 1/La leyenda de Korra - 12 [AB].mkv"].endswith("Korra - S01E12.mkv"))

    def test_multi_episode_and_padding(self):
        touch(self.root, "Naruto/Season 1/Naruto 001.avi", "Naruto/Season 1/Naruto 026-027.avi",
              "Naruto/Season 1/Naruto 120.avi")
        _, r = self.plan()
        self.assertTrue(r["Naruto/Season 1/Naruto 026-027.avi"].endswith("Naruto - S01E026-E027.avi"))
        self.assertTrue(r["Naruto/Season 1/Naruto 001.avi"].endswith("Naruto - S01E001.avi"))

    def test_good_names_untouched_and_issues(self):
        touch(self.root, "Bleach (2004)/Season 01/Bleach - S01E01.mkv",
              "One Piece/East Blue (1-61)/episodio.mkv", "theme.mp3",
              "SAO/extras/OP 01.mkv")
        p, r = self.plan()
        self.assertEqual(r, {})
        problems = {i.problem for i in p.issues}
        self.assertIn("archivo suelto en la raiz", problems)
        self.assertIn("no se pudo deducir el episodio", problems)

    def test_missing_episodes(self):
        touch(self.root, "Show/Season 01/Show - S01E01.mkv", "Show/Season 01/Show - S01E02.mkv",
              "Show/Season 01/5.mkv", "Show/Season 01/Show - S01E06-E07.mkv")
        p, _ = self.plan()
        miss = [i for i in p.issues if i.problem == "faltan episodios"]
        self.assertEqual(len(miss), 1)
        self.assertEqual(miss[0].data["missing"], [3, 4])
        self.assertIn("E03-E04", miss[0].hint)
        self.assertEqual(p.shows[0]["seasons"][1], {"have": 5, "last": 7})

    def test_sidecar_follows_video(self):
        touch(self.root, "Show/Season 1/3.mkv", "Show/Season 1/3.es.srt")
        _, r = self.plan()
        self.assertEqual(r["Show/Season 1/3.es.srt"], "Show/Season 1/Show - S01E03.es.srt")


class MovieTests(unittest.TestCase):
    def test_movie_title(self):
        self.assertEqual(names.movie_title("John.Wick.3.2019.1080p.WEB.mkv"), ("John Wick 3", 2019))
        self.assertEqual(names.movie_title("Papillon.mkv"), ("Papillon", None))
        self.assertEqual(names.movie_title("Divergente 1080p BluRay x265 10bit HEVC.mkv"), ("Divergente", None))
        self.assertEqual(names.movie_title("Hermanos (2009) WEB-DL 1080p Latino"), ("Hermanos", 2009))

    def test_pick_duplicate(self):
        mi = names.MediaInfo
        same_big, same_small = mi(1920, 1080, ["spa", "eng"], size=3), mi(1920, 872, ["spa", "eng"], size=2)
        self.assertEqual(names.pick_duplicate(same_big, same_small)[0], 0)
        sd = mi(1280, 720, ["spa"], size=1)
        self.assertEqual(names.pick_duplicate(same_big, sd)[0], 1)
        uhd = mi(3840, 2160, ["spa"], size=9)
        self.assertIsNone(names.pick_duplicate(uhd, same_small)[0])

    def test_plan_movies_and_year(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            touch(root, "Papillon.mkv", "Oppenheimer 2023.mp4", "Oppenheimer (2023) IMAX/opp.mkv",
                  "Coleccion (2003-2017)/Piratas (2003).mkv")
            p = names.plan_movies(root)
            self.assertEqual([i.path for i in p.issues if i.problem == "pelicula sin año"], [str(root / "Papillon.mkv")])
            self.assertTrue(any(i.problem == "pelicula duplicada" for i in p.issues))
            op = names.plan_movie_year(root / "Papillon.mkv", 1973, root)[0]
            self.assertEqual(Path(op.dst).name, "Papillon (1973).mkv")


class ApplyTests(unittest.TestCase):
    def test_apply_and_undo(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            touch(root, "lib/Horimiya/01.mp4", "lib/Dup.mkv")
            plan = names.plan_series(root / "lib")
            ops = plan.ops + [names.Op(str(root / "lib/Dup.mkv"), "", "trash")]
            log = root / "logs/a.jsonl"
            res = names.apply_ops(ops, log, root / "trash")
            self.assertTrue(all(r["status"] == "ok" for r in res), res)
            self.assertTrue((root / "lib/Horimiya/Season 01/Horimiya - S01E01.mp4").exists())
            self.assertFalse((root / "lib/Dup.mkv").exists())
            names.undo_log(log)
            self.assertTrue((root / "lib/Horimiya/01.mp4").exists())
            self.assertTrue((root / "lib/Dup.mkv").exists())
            self.assertFalse((root / "lib/Horimiya/Season 01").exists())


class TmdbMatchTests(unittest.TestCase):
    def test_best_match_by_runtime(self):
        c = tmdb.Candidate
        cands = [c("Papillon", "Papillon", 2017, 133, 1), c("Papillon", "Papillon", 1973, 151, 2)]
        best, _ = tmdb.best_match(cands, 150)
        self.assertEqual(best.year, 1973)
        self.assertIsNone(tmdb.best_match(cands, 100)[0])
        self.assertIsNone(tmdb.best_match([], 100)[0])


if __name__ == "__main__":
    unittest.main()
