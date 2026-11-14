"""
Identification confidence, runtime matching, phone notifications, already-ripped and
disk space checks, and the manual-wait override.
"""
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import confidence, hooks, notify, prechecks, settings, store  # noqa: E402
from arm.ripper import utils  # noqa: E402


class TempStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp, "arm.db"),
                                                 "NTFY_SERVER": "", "NTFY_TOPIC": "", "TMDB_API_KEY": "key"})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


def job(**fields):
    values = dict(job_id=5, label="DONT_BREATHE", title="Don't Breathe", year="2016", hasnicetitle=True,
                  imdb_id="tt4160708", crc_id=None, disctype="dvd", devpath="/dev/sr0", title_manual=None,
                  poster_url=None, video_type="movie", config=SimpleNamespace(MANUAL_WAIT_TIME=300))
    values.update(fields)
    return SimpleNamespace(**values)


class TestLabels(unittest.TestCase):

    def test_clean_label(self):
        self.assertEqual(confidence.clean_label("DONT_BREATHE_WS_D1"), "dont breathe")
        self.assertEqual(confidence.clean_label("not identified"), "")
        self.assertEqual(confidence.clean_label("DVD_VIDEO"), "")
        self.assertEqual(confidence.clean_label(None), "")

    def test_similarity(self):
        self.assertEqual(confidence.similarity("DONT_BREATHE", "Don't Breathe"), 1.0)
        self.assertGreater(confidence.similarity("IT_CHAPTER_TWO", "It Chapter Two"), 0.85)
        self.assertLess(confidence.similarity("WWJD", "Spirited Away"), 0.4)
        self.assertEqual(confidence.similarity("not identified", "It"), 0.0)


class TestScore(unittest.TestCase):

    def test_good_label_and_runtime_is_high(self):
        value, reasons = confidence.score(True, True, 1.0, 1, None)
        self.assertGreaterEqual(value, 90)
        self.assertIn("The main title's length matches TMDb's runtime", reasons)

    def test_no_label_but_runtime_match_is_medium_or_low(self):
        value, _ = confidence.score(True, False, 0.0, 0, None)
        self.assertLess(value, 90)

    def test_unidentified_is_very_low(self):
        self.assertLess(confidence.score(False, True, 1.0, 0, None)[0], 10)

    def test_runtime_far_off_and_other_title_before_lower_it(self):
        good = confidence.score(True, True, 1.0, None, None)[0]
        self.assertLess(confidence.score(True, True, 1.0, 40, None)[0], good)
        self.assertLess(confidence.score(True, True, 1.0, None, "different")[0], good)
        self.assertGreater(confidence.score(True, True, 1.0, None, "same")[0], good)

    def test_levels_follow_settings(self):
        with patch.dict(cfg.arm_config, {"CONFIDENCE_SKIP_WAIT_ABOVE": 90, "CONFIDENCE_NOTIFY_BELOW": 70}):
            self.assertEqual(confidence.level(95), "high")
            self.assertEqual(confidence.level(80), "medium")
            self.assertEqual(confidence.level(40), "low")
        with patch.dict(cfg.arm_config, {"CONFIDENCE_SKIP_WAIT_ABOVE": 0}):
            self.assertNotEqual(confidence.level(100), "high")


class TestAssess(TempStore):

    def test_runtime_match_replaces_a_weak_title(self):
        weak = job(label="IT_CHAPTER_TWO", title="It", year="2017", imdb_id="tt1396484")
        candidate = {"title": "It Chapter Two", "year": "2019", "runtime": 169, "imdb_id": "tt7349950",
                     "poster_url": None, "popularity": 50, "tmdb_id": 474350}
        runtimes = {"tt1396484": 135, "tt7349950": 169}

        def update(args, target):
            for key, value in args.items():
                setattr(target, key, value)

        with patch.object(confidence, "main_title_seconds", return_value=169 * 60), \
                patch.object(confidence.tmdb, "runtime_minutes", side_effect=runtimes.get), \
                patch.object(confidence.tmdb, "search_movies", return_value=[candidate]), \
                patch.object(confidence, "prior_rip", return_value=None), \
                patch.object(utils, "database_updater", side_effect=update):
            result = confidence.assess(weak)
        self.assertEqual(weak.title, "It Chapter Two")
        self.assertEqual(weak.imdb_id, "tt7349950")
        self.assertGreaterEqual(result["score"], 90)
        self.assertIn("Picked by runtime", result["reasons"][0])
        self.assertEqual(store.load_job(5)["confidence"]["score"], result["score"])

    def test_unlabelled_disc_ripped_before_gets_its_earlier_title(self):
        """Real case: an unlabelled It Chapter Two DVD; the same disc (same CRC) was ripped as It Chapter Two"""
        disc = job(label="not identified", title="not identified", year=None, imdb_id=None, hasnicetitle=False,
                   crc_id="fb33966dc04c6fac", video_type="unknown")
        earlier = SimpleNamespace(job_id=22, title="It Chapter Two", title_manual=None, year="2019",
                                  imdb_id="tt7349950", poster_url="https://image.tmdb.org/x.jpg", video_type="movie")

        def update(args, target):
            for key, value in args.items():
                setattr(target, key, value)

        with patch.object(confidence, "main_title_seconds", return_value=10152), \
                patch.object(confidence.tmdb, "runtime_minutes", return_value=169), \
                patch.object(confidence, "earlier_identity", return_value=earlier), \
                patch.object(confidence, "prior_rip", side_effect=lambda j: "same" if j.title == "It Chapter Two"
                             else None), \
                patch.object(utils, "database_updater", side_effect=update):
            result = confidence.assess(disc)
        self.assertEqual((disc.title, disc.year, disc.video_type), ("It Chapter Two", "2019", "movie"))
        self.assertEqual(result["level"], "high")
        self.assertIn("Same disc as job 22", result["reasons"][0])

    def test_unidentified_disc_asks_which_disc(self):
        disc = job(label="not identified", title="not identified", hasnicetitle=False)
        with patch.object(notify, "in_app") as in_app:
            confidence.ask_user(disc, {"score": 5, "reasons": ["ARM couldn't find a title for this disc"]}, 900)
        self.assertEqual(in_app.call_args.args[0], "Which disc is this?")
        self.assertIn("couldn't identify", store.load_job(5)["attention"][0]["message"])

    def test_manual_title_is_trusted(self):
        result = confidence.assess(job(title_manual="Whatever"))
        self.assertEqual(result["level"], "high")

    def test_disabled(self):
        with patch.dict(cfg.arm_config, {"CONFIDENCE_ENABLED": False}):
            self.assertIsNone(confidence.assess(job()))


class TestNotify(TempStore):

    def test_send_posts_json_with_actions(self):
        with patch.dict(cfg.arm_config, {"NTFY_SERVER": "https://ntfy.example/", "NTFY_TOPIC": "arm",
                                         "NTFY_TOKEN": "tk_secret", "ARM_PHONE_URL": "http://192.168.0.121:8080"}), \
                patch.object(notify.requests, "post", return_value=MagicMock(status_code=200)) as post:
            ok = notify.send("Is this It?", "body", priority="high", tags=["dvd"],
                             actions=[notify.http("Yes", notify.action_url(5, "confirm"))])
        self.assertTrue(ok)
        url = post.call_args.args[0]
        payload = post.call_args.kwargs["json"]
        self.assertEqual(url, "https://ntfy.example")
        self.assertEqual(payload["topic"], "arm")
        self.assertEqual(payload["priority"], 4)
        self.assertTrue(payload["actions"][0]["url"].startswith("http://192.168.0.121:8080/automation/act?job=5"))
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer tk_secret")

    def test_send_is_off_without_server_and_never_raises(self):
        self.assertFalse(notify.send("t", "m"))
        with patch.dict(cfg.arm_config, {"NTFY_SERVER": "https://x", "NTFY_TOPIC": "t"}), \
                patch.object(notify.requests, "post", side_effect=notify.requests.ConnectionError("down")):
            self.assertFalse(notify.send("t", "m"))

    def test_signatures(self):
        token = notify.sign(5, "confirm")
        self.assertTrue(notify.check_signature(5, "confirm", token))
        self.assertFalse(notify.check_signature(6, "confirm", token))
        self.assertFalse(notify.check_signature(5, "confirm", "nope"))
        self.assertFalse(notify.check_signature(5, "confirm", None))


class TestPrechecks(TempStore):

    def previous(self, path):
        return SimpleNamespace(job_id=3, title="Don't Breathe", title_manual=None, path=path)

    def test_already_ripped_skip_raises(self):
        folder = os.path.join(self.tmp, "Dont Breathe (2016)")
        os.makedirs(folder)
        open(os.path.join(folder, "Dont Breathe (2016).mkv"), "w").close()
        with patch.dict(cfg.arm_config, {"ALREADY_RIPPED_ACTION": "skip"}), \
                patch.object(prechecks, "find_previous_rip", return_value=self.previous(folder)), \
                patch.object(prechecks.notify, "in_app"):
            with self.assertRaises(prechecks.JobSkipped):
                prechecks.check_already_ripped(job())
        self.assertEqual(store.load_job(5)["already_ripped"]["job_id"], 3)

    def test_already_ripped_notify_continues_and_rip_does_nothing(self):
        with patch.dict(cfg.arm_config, {"ALREADY_RIPPED_ACTION": "notify"}), \
                patch.object(prechecks, "find_previous_rip", return_value=self.previous("/x")), \
                patch.object(prechecks.notify, "in_app") as in_app:
            self.assertIsNotNone(prechecks.check_already_ripped(job()))
        in_app.assert_called_once()
        with patch.dict(cfg.arm_config, {"ALREADY_RIPPED_ACTION": "rip"}), \
                patch.object(prechecks, "find_previous_rip") as find:
            self.assertIsNone(prechecks.check_already_ripped(job()))
        find.assert_not_called()

    def test_previous_rip_needs_files(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        other = SimpleNamespace(job_id=3, title="Don't Breathe", path=empty)
        query = MagicMock()
        query.filter.return_value = query
        query.order_by.return_value.all.return_value = [other]
        from arm.models.job import Job
        with patch.object(Job, "query", query):
            self.assertIsNone(prechecks.find_previous_rip(job(crc_id="abc")))
            open(os.path.join(empty, "x.mkv"), "w").close()
            self.assertIs(prechecks.find_previous_rip(job(crc_id="abc")), other)

    def test_disk_space(self):
        with patch.dict(cfg.arm_config, {"RAW_PATH": self.tmp, "TRANSCODE_PATH": self.tmp, "COMPLETED_PATH": self.tmp,
                                         "MIN_FREE_GB_DVD": 10 ** 9, "DISK_SPACE_ACTION": "fail"}), \
                patch.object(prechecks.notify, "in_app"):
            with self.assertRaises(utils.RipperException):
                prechecks.check_disk_space(job())
        with patch.dict(cfg.arm_config, {"RAW_PATH": self.tmp, "MIN_FREE_GB_DVD": 10 ** 9,
                                         "DISK_SPACE_ACTION": "wait"}), \
                patch.object(prechecks.notify, "in_app"), \
                patch.object(prechecks, "space_shortfall", side_effect=[(10, [("x", 1)]), (10, [])]):
            self.assertTrue(prechecks.check_disk_space(job(), sleep=lambda _s: None))
        with patch.dict(cfg.arm_config, {"MIN_FREE_GB_DVD": 1, "RAW_PATH": self.tmp}):
            self.assertTrue(prechecks.check_disk_space(job()))


class TestWaitOverride(TempStore):

    def test_high_confidence_skips_low_waits_longer(self):
        with patch.dict(cfg.arm_config, {"LOW_CONFIDENCE_WAIT_TIME": 900}), \
                patch.object(hooks.confidence, "ask_user") as ask:
            self.assertEqual(hooks.wait_for_user(job(), {"level": "high", "score": 95}), (0, False))
            self.assertEqual(hooks.wait_for_user(job(), {"level": "medium", "score": 80}), (None, False))
            self.assertEqual(hooks.wait_for_user(job(), {"level": "low", "score": 30}), (900, True))
        ask.assert_called_once()
        self.assertEqual(hooks.wait_for_user(job(), None), (None, False))

    def test_check_for_wait_honours_override(self):
        target = job(config=SimpleNamespace(MANUAL_WAIT=False, MANUAL_WAIT_TIME=300), title_manual=None)
        with patch.object(utils, "database_updater") as update, patch.object(utils.time, "sleep"), \
                patch.object(utils.db.session, "refresh"):
            utils.check_for_wait(target)                      # MANUAL_WAIT off: no wait
            update.assert_not_called()
            utils.check_for_wait(target, 10, force=True)      # low confidence: waits anyway
            self.assertEqual(update.call_args_list[0].args[0], {"status": "waiting"})
            update.reset_mock()
            target.config.MANUAL_WAIT = True
            utils.check_for_wait(target, 0)                   # high confidence: no wait
            update.assert_not_called()


class TestSettings(unittest.TestCase):

    def test_coerce(self):
        self.assertTrue(settings.coerce(settings.BY_KEY["TITLE_ANALYSIS"], "on"))
        self.assertEqual(settings.coerce(settings.BY_KEY["DB_BACKUP_KEEP"], "x"), 14)
        self.assertEqual(settings.coerce(settings.BY_KEY["ALREADY_RIPPED_ACTION"], "SKIP"), "skip")
        self.assertEqual(settings.coerce(settings.BY_KEY["ALREADY_RIPPED_ACTION"], "nuke"), "notify")
        self.assertEqual(settings.coerce(settings.BY_KEY["DB_BACKUP_TIME"], "7:5"), "07:05")
        self.assertEqual(settings.coerce(settings.BY_KEY["DB_BACKUP_TIME"], "25:00"), "03:00")
        self.assertEqual(settings.coerce(settings.BY_KEY["KEEP_AUDIO_LANGUAGES"], " ENG, fra ,"), "eng,fra")

    def test_template_has_every_setting_with_the_same_default(self):
        template = cfg._load_config(os.path.join(cfg.arm_config["INSTALLPATH"], "setup/arm.yaml"))
        for setting in settings.SCHEMA:
            self.assertIn(setting.key, template)
            self.assertEqual(settings.coerce(setting, template[setting.key]), setting.default, setting.key)

    def test_secrets_are_hidden_in_logs(self):
        from arm.models.config import hidden_attribs
        for key in settings.SECRET_KEYS:
            self.assertIn(key, hidden_attribs)


if __name__ == '__main__':
    unittest.main()
