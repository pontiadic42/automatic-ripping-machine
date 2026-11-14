"""
Title analysis: main feature, other cuts, language versions, duplicates, extras,
TV episodes and play-all titles, from what MakeMKV reports.
"""
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import titles  # noqa: E402
from arm.automation.titles import Title  # noqa: E402

MIN = 60


def audio(*langs, commentary=()):
    """Audio streams; ``commentary`` lists the positions of commentary tracks"""
    return [{"lang": lang, "name": "", "commentary": index in commentary} for index, lang in enumerate(langs)]


def settings(**overrides):
    values = {"MINLENGTH": 600, "SKIP_DUPLICATE_TITLES": True, "KEEP_ALTERNATE_CUTS": True,
              "KEEP_OTHER_LANGUAGE_VERSIONS": False, "TITLE_ANALYSIS": True, "PREFERRED_LANGUAGE": "eng",
              "MAINFEATURE": False}
    values.update(overrides)
    return patch.dict(cfg.arm_config, values)


def roles(decisions):
    return {tid: (d["role"], d["rip"]) for tid, d in decisions.items()}


class TestParsing(unittest.TestCase):

    def test_segments(self):
        self.assertEqual(titles.parse_segments("1-3,7,9-10"), (1, 2, 3, 7, 9, 10))
        self.assertEqual(titles.parse_segments(""), ())
        self.assertEqual(titles.parse_segments("x,2"), (2,))

    def test_from_makemkv(self):
        raw = {0: {"tinfo": {9: "2:49:03", 8: "20", 11: "7849852928", 16: "00800.mpls", 26: "1-40",
                             28: "eng", 2: "It Chapter Two"},
                   "streams": [{1: "Video"}, {1: "Audio", 3: "eng", 2: "Surround 5.1"},
                               {1: "Audio", 3: "eng", 2: "Director's Comments", 22: "1"},
                               {1: "Subtitles", 3: "fra", 22: "4096"}]}}
        title = titles.from_makemkv(raw)[0]
        self.assertEqual(title.seconds, 2 * 3600 + 49 * 60 + 3)
        self.assertEqual(title.segments, tuple(range(1, 41)))
        self.assertEqual(title.source, "00800.mpls")
        self.assertEqual(title.language, "eng")
        self.assertEqual([a["commentary"] for a in title.audio], [False, True])
        self.assertTrue(title.subtitles[0]["forced"])


class TestMovies(unittest.TestCase):

    def test_cuts_languages_duplicates_extras(self):
        main_segments = tuple(range(1, 41))
        disc = [
            Title(0, 135 * MIN, segments=main_segments, audio=audio("eng", "eng", commentary=(1,))),
            Title(1, 146 * MIN, segments=main_segments + tuple(range(100, 110)), audio=audio("eng")),
            Title(2, 135 * MIN + 20, segments=(200, 201) + main_segments[2:], metadata_lang="fra",
                  audio=audio("fra")),
            Title(3, 135 * MIN, segments=main_segments, audio=audio("eng")),          # copy of 0
            Title(4, 15 * MIN, segments=(300, 301), name="Making of"),
            Title(5, 3 * MIN, segments=(400,)),                                       # short
        ]
        with settings():
            decisions, ambiguous = titles.analyze_movie(disc, "eng", tmdb_runtime=135)
        self.assertFalse(ambiguous)
        self.assertEqual(roles(decisions), {0: ("main", True), 1: ("cut", True), 2: ("language", False),
                                            3: ("duplicate", False), 4: ("extra", True), 5: ("short", False)})
        self.assertEqual(decisions[1]["label"], "Extended cut (+11 min)")
        self.assertEqual(decisions[2]["label"], "French")
        self.assertEqual(decisions[4]["label"], "Making of")
        self.assertEqual(decisions[3]["duplicate_of"], 0)

    def test_tmdb_runtime_picks_the_theatrical_cut_as_main(self):
        disc = [Title(0, 146 * MIN, segments=tuple(range(1, 51))), Title(1, 135 * MIN, segments=tuple(range(1, 41)))]
        with settings():
            decisions, _ = titles.analyze_movie(disc, "eng", tmdb_runtime=136)
        self.assertEqual(decisions[1]["role"], "main")
        self.assertEqual(decisions[0]["label"], "Extended cut (+11 min)")
        with settings():
            decisions, _ = titles.analyze_movie(disc, "eng", tmdb_runtime=None)
        self.assertEqual(decisions[0]["role"], "main")  # no runtime: longest, like ARM
        self.assertEqual(decisions[1]["label"], "Shorter cut (-11 min)")

    def test_preferred_language_wins_even_when_listed_second(self):
        base = tuple(range(1, 31))
        disc = [Title(0, 100 * MIN, segments=(90,) + base, audio=audio("deu")),
                Title(1, 100 * MIN, segments=(91,) + base, audio=audio("eng"))]
        with settings():
            decisions, ambiguous = titles.analyze_movie(disc, "eng", None)
        self.assertFalse(ambiguous)
        self.assertEqual(decisions[1]["role"], "main")
        german = decisions[0]
        self.assertEqual((german["role"], german["label"], german["rip"]), ("language", "German", False))

    def test_language_versions_that_cant_be_told_apart_are_all_kept(self):
        base = tuple(range(1, 31))
        disc = [Title(0, 100 * MIN, segments=(90,) + base, audio=audio("eng", "fra")),
                Title(1, 100 * MIN, segments=(91,) + base, audio=audio("eng", "fra"))]
        with settings():
            decisions, ambiguous = titles.analyze_movie(disc, "eng", None)
        self.assertTrue(ambiguous)
        self.assertEqual(roles(decisions), {0: ("main", True), 1: ("language", True)})
        self.assertTrue(decisions[1]["ambiguous"])

    def test_keep_settings(self):
        base = tuple(range(1, 31))
        disc = [Title(0, 100 * MIN, segments=base, audio=audio("eng")),
                Title(1, 110 * MIN, segments=base + (50, 51), audio=audio("eng")),
                Title(2, 100 * MIN, segments=(99,) + base[1:], audio=audio("spa"))]
        with settings(KEEP_ALTERNATE_CUTS=False, KEEP_OTHER_LANGUAGE_VERSIONS=True):
            decisions, _ = titles.analyze_movie(disc, "eng", 100)
        self.assertEqual(roles(decisions), {0: ("main", True), 1: ("cut", False), 2: ("language", True)})

    def test_double_feature(self):
        disc = [Title(0, 95 * MIN, segments=tuple(range(1, 20))), Title(1, 90 * MIN, segments=tuple(range(50, 70)))]
        with settings():
            decisions, _ = titles.analyze_movie(disc, "eng", 95)
        self.assertEqual(roles(decisions), {0: ("main", True), 1: ("feature", True)})

    def test_extras_follow_mainfeature(self):
        disc = [Title(0, 95 * MIN, segments=(1, 2)), Title(1, 20 * MIN, segments=(5,))]
        with settings():
            decisions, _ = titles.analyze_movie(disc, "eng", None, rip_extras=False)
        self.assertFalse(decisions[1]["rip"])


class TestSeries(unittest.TestCase):

    def test_episodes_play_all_and_extras(self):
        disc = [Title(0, 132 * MIN, segments=tuple(range(1, 13))),      # play all
                Title(1, 44 * MIN, segments=(1, 2, 3, 4)),
                Title(2, 44 * MIN, segments=(5, 6, 7, 8)),
                Title(3, 44 * MIN, segments=(9, 10, 11, 12)),
                Title(4, 12 * MIN, segments=(20,))]
        with settings():
            decisions = titles.analyze_series(disc)
        self.assertEqual(roles(decisions), {0: ("playall", False), 1: ("episode", True), 2: ("episode", True),
                                            3: ("episode", True), 4: ("extra", True)})


class TestPlan(unittest.TestCase):

    def test_off_returns_none(self):
        job = type("Job", (), {"job_id": 1, "video_type": "movie", "imdb_id": None})()
        with settings(TITLE_ANALYSIS=False):
            self.assertIsNone(titles.plan(job, {0: {"tinfo": {9: "1:40:00"}, "streams": []}}))

    def test_plan_saves_and_returns_decisions(self):
        job = type("Job", (), {"job_id": 1, "video_type": "movie", "imdb_id": "tt1"})()
        raw = {0: {"tinfo": {9: "1:40:00", 26: "1-30"}, "streams": []},
               1: {"tinfo": {9: "1:40:00", 26: "1-30"}, "streams": []}}
        with settings(), patch.object(titles.store, "update_job") as save, \
                patch("arm.automation.tmdb.runtime_minutes", return_value=100):
            decisions = titles.plan(job, raw)
        self.assertEqual(decisions[0]["role"], "main")
        self.assertEqual(decisions[1]["role"], "duplicate")
        self.assertEqual(save.call_args.kwargs["titles"]["titles"][0]["tid"], 0)
        self.assertEqual(titles.main_seconds(decisions), 6000)


if __name__ == '__main__':
    unittest.main()
