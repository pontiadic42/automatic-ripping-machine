"""
Putting finished files in place (names, extras, TV episodes, length checks), moving a
finished job when its title changes, Jellyfin paths, and the maintenance tasks.
"""
import datetime
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import jellyfin, maintenance, organize, store  # noqa: E402


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.completed = os.path.join(self.tmp, "completed")
        self.patch = patch.dict(cfg.arm_config, {
            "DBFILE": os.path.join(self.tmp, "db", "arm.db"), "COMPLETED_PATH": self.completed,
            "EXTRAS_SUB": "extras", "DEST_EXT": "mkv", "VERIFY_OUTPUT": True, "VERIFY_TOLERANCE_SECONDS": 90,
            "ORGANIZE_EXTRAS": True, "TV_EPISODE_MATCHING": True, "NTFY_SERVER": "", "NTFY_TOPIC": ""})
        self.patch.start()
        os.makedirs(os.path.join(self.tmp, "db"))

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path


def movie_job(**fields):
    values = dict(job_id=9, title="It Chapter Two", title_manual=None, year="2019", video_type="movie",
                  label="IT_CHAPTER_TWO", imdb_id="tt7349950", disctype="dvd", hasnicetitle=True)
    values.update(fields)
    return SimpleNamespace(**values)


def decision(tid, role, seconds, label="", rip=True):
    return {"tid": tid, "role": role, "seconds": seconds, "label": label, "rip": rip}


class TestOrganizeMovie(Temp):

    def test_names_cuts_languages_extras(self):
        for name in ("B1_t00.mkv", "B1_t01.mkv", "B1_t02.mkv", "B1_t04.mkv", "other.mkv"):
            self.touch("out", name)
        decisions = {0: decision(0, "main", 8100), 1: decision(1, "cut", 8760, "Extended cut (+11 min)"),
                     2: decision(2, "language", 8120, "French"), 4: decision(4, "extra", 900, "Making of")}
        final = os.path.join(self.completed, "movies", "It Chapter Two (2019)")
        lengths = {"B1_t00.mkv": 8100, "B1_t01.mkv": 8000, "B1_t02.mkv": 8120, "B1_t04.mkv": 900}
        with patch.object(organize, "duration_seconds", side_effect=lambda p: lengths.get(os.path.basename(p))):
            problems = organize.organize(movie_job(), os.path.join(self.tmp, "out"), final, decisions)
        files = sorted(os.path.relpath(os.path.join(r, f), final) for r, _d, fs in os.walk(final) for f in fs)
        self.assertEqual(files, ["It Chapter Two (2019) - Extended cut (+11 min).mkv",
                                 "It Chapter Two (2019) - French.mkv", "It Chapter Two (2019).mkv",
                                 os.path.join("extras", "Making of.mkv"), os.path.join("extras", "other.mkv")])
        self.assertEqual(len(problems), 1)
        self.assertIn("B1_t01.mkv is 133:20 long but the disc title is 146:00", problems[0])
        self.assertEqual(len(store.load_job(9)["files"]), 5)

    def test_existing_file_is_not_overwritten(self):
        final = os.path.join(self.completed, "movies", "X (2019)")
        self.touch("completed", "movies", "X (2019)", "X (2019).mkv")
        self.touch("out", "t_t00.mkv")
        with patch.object(organize, "duration_seconds", return_value=100):
            organize.organize(movie_job(title="X"), os.path.join(self.tmp, "out"), final, {0: decision(0, "main", 100)})
        self.assertTrue(os.path.exists(os.path.join(final, "X (2019) (2).mkv")))

    def test_check_length(self):
        with patch.object(organize, "duration_seconds", return_value=None):
            self.assertIn("couldn't read", organize.check_length("/x/a.mkv", 100))
        with patch.dict(cfg.arm_config, {"VERIFY_OUTPUT": False}):
            self.assertIsNone(organize.check_length("/x/a.mkv", 100))


class TestEpisodes(Temp):

    def season_data(self, count=10, runtime=44):
        return {"episodes": [{"episode_number": n, "runtime": runtime} for n in range(1, count + 1)]}

    def run_match(self, label, titles, runtime=44, state=None):
        job = movie_job(video_type="series", title="The Office", label=label, imdb_id="tt0386676")
        if state:
            store.update_state(tv_progress=state)
        with patch("arm.automation.tmdb.find_by_imdb", return_value=("tv", 2316)), \
                patch("arm.automation.tmdb.season", return_value=self.season_data(runtime=runtime)), \
                patch("arm.automation.tmdb.tv_details", return_value={"seasons": [{"season_number": 1}]}):
            return organize.match_episodes(job, titles)

    def test_disc_one_starts_at_episode_one_and_disc_two_continues(self):
        eps = [decision(1, "episode", 44 * 60), decision(2, "episode", 43 * 60), decision(3, "episode", 45 * 60)]
        numbers, note = self.run_match("THE_OFFICE_S2_D1", eps)
        self.assertEqual(numbers, {1: (2, 1), 2: (2, 2), 3: (2, 3)})
        self.assertEqual(note, "Season 2, episodes 1-3")
        numbers, _ = self.run_match("THE_OFFICE_S2_D2", eps)
        self.assertEqual(numbers[1], (2, 4))

    def test_lengths_that_dont_fit_are_not_numbered(self):
        eps = [decision(1, "episode", 22 * 60), decision(2, "episode", 22 * 60)]
        numbers, note = self.run_match("SHOW_S1_D1", eps, runtime=60)
        self.assertEqual(numbers, {})
        self.assertIn("don't line up", note)

    def test_series_files_go_to_season_folders(self):
        self.touch("out", "t_t01.mkv")
        self.touch("out", "t_t02.mkv")
        final = os.path.join(self.completed, "tv", "The Office")
        job = movie_job(video_type="series", title="The Office", year="", label="THE_OFFICE_S1_D1")
        with patch.object(organize, "match_episodes", return_value=({1: (1, 1)}, "Season 1, episodes 1-1")), \
                patch.object(organize, "duration_seconds", return_value=2640):
            organize.organize(job, os.path.join(self.tmp, "out"), final,
                              {1: decision(1, "episode", 2640), 2: decision(2, "episode", 2640)})
        self.assertTrue(os.path.exists(os.path.join(final, "Season 01", "The Office S01E01.mkv")))
        self.assertTrue(os.path.exists(os.path.join(final, "The Office - title 2.mkv")))

    def test_season_and_disc(self):
        self.assertEqual(organize.season_and_disc("FRIENDS_SEASON_3_DISC_2"), (3, 2))
        self.assertEqual(organize.season_and_disc("SHOW_S01D3"), (1, 3))
        self.assertEqual(organize.season_and_disc("SPIRITED_AWAY"), (None, None))


class TestRetitle(Temp):

    def test_moves_folder_renames_files_and_changes_type_folder(self):
        old = os.path.join(self.completed, "unidentified", "When Marnie was There (None)")
        self.touch("completed", "unidentified", "When Marnie was There (None)", "When Marnie was There (None).mkv")
        self.touch("completed", "unidentified", "When Marnie was There (None)", "extras", "t_t03.mkv")
        job = movie_job(title="When Marnie Was There", year="2014", video_type="movie")
        new = organize.retitle(job, old, "When Marnie was There (None)")
        self.assertEqual(new, os.path.join(self.completed, "movies", "When Marnie Was There (2014)"))
        self.assertTrue(os.path.isfile(os.path.join(new, "When Marnie Was There (2014).mkv")))
        self.assertTrue(os.path.isfile(os.path.join(new, "extras", "t_t03.mkv")))
        self.assertFalse(os.path.exists(old))

    def test_same_place_or_missing_does_nothing(self):
        self.assertIsNone(organize.retitle(movie_job(), "/nope", "x"))
        folder = os.path.join(self.completed, "movies", "It Chapter Two (2019)")
        os.makedirs(folder)
        self.assertIsNone(organize.retitle(movie_job(), folder, "It Chapter Two (2019)"))


class TestJellyfin(Temp):

    def test_paths(self):
        with patch.dict(cfg.arm_config, {"JELLYFIN_MEDIA_ROOT": "/media"}):
            self.assertEqual(jellyfin.jellyfin_path(os.path.join(self.completed, "movies", "X (2019)")),
                             "/media/Movies/X (2019)")
            self.assertEqual(jellyfin.jellyfin_path(os.path.join(self.completed, "tv", "Show")), "/media/TV Shows/Show")
            self.assertIsNone(jellyfin.jellyfin_path("/elsewhere/x"))

    def test_announce_and_verify(self):
        folder = os.path.join(self.completed, "movies", "X (2019)")
        os.makedirs(folder)
        with patch.dict(cfg.arm_config, {"JELLYFIN_URL": "http://jf:8096", "JELLYFIN_API_KEY": "k"}), \
                patch.object(jellyfin.requests, "post", return_value=MagicMock(status_code=204)) as post:
            self.assertTrue(jellyfin.announce(movie_job(), folder))
        self.assertEqual(post.call_args.kwargs["json"]["Updates"][0]["Path"], "/media/Movies/X (2019)")
        self.assertEqual(post.call_args.kwargs["headers"]["X-Emby-Token"], "k")
        data = store.load_job(9)["jellyfin"]
        since = datetime.datetime.now() - datetime.timedelta(minutes=5)
        store.update_job(9, jellyfin=dict(data, since=since.isoformat()))
        item = {"Name": "It (2017)", "Path": "/media/Movies/X (2019)/X (2019).mkv",
                "ProviderIds": {"Imdb": "tt1396484"}}
        with patch.dict(cfg.arm_config, {"JELLYFIN_URL": "http://jf:8096", "JELLYFIN_API_KEY": "k"}), \
                patch.object(jellyfin.requests, "get") as get:
            get.return_value.json.return_value = {"Items": [item]}
            self.assertFalse(jellyfin.verify(9, movie_job()))
        self.assertEqual(store.load_job(9)["jellyfin"]["status"], "mismatch")
        self.assertEqual(store.load_job(9)["attention"][0]["kind"], "jellyfin")


class TestMaintenance(Temp):

    def test_due_once_a_day(self):
        now = datetime.datetime(2026, 11, 14, 8, 30)
        self.assertFalse(maintenance.due("summary", "09:00", now))
        self.assertTrue(maintenance.due("summary", "08:00", now))
        maintenance.mark_done("summary", now)
        self.assertFalse(maintenance.due("summary", "08:00", now))
        self.assertTrue(maintenance.due("summary", "08:00", now + datetime.timedelta(days=1)))

    def test_daily_tasks_use_the_local_clock(self):
        """The container runs on UTC; 08:00 means 08:00 where the user is"""
        utc_now = datetime.datetime(2026, 11, 14, 12, 0)     # 07:00 in Indianapolis
        local = datetime.datetime(2026, 11, 14, 7, 0)
        with patch.dict(cfg.arm_config, {"DAILY_SUMMARY_TIME": "08:00", "DB_BACKUP_ENABLED": False,
                                         "CLEANUP_ENABLED": False, "STUCK_JOB_MINUTES": 0, "JELLYFIN_URL": ""}), \
                patch.object(maintenance, "send_summary") as send:
            maintenance.tick(utc_now, local)
            send.assert_not_called()
            maintenance.tick(utc_now + datetime.timedelta(hours=1), local + datetime.timedelta(hours=1))
            send.assert_called_once()
        with patch.dict(os.environ, {"ARM_LOCAL_TZ": "America/Indiana/Indianapolis"}):
            offset = datetime.datetime.now() - maintenance._local_now()
        self.assertGreater(offset.total_seconds(), 3 * 3600)  # container clock is UTC

    def test_backup_copies_db_and_prunes(self):
        db_file = cfg.arm_config["DBFILE"]
        conn = sqlite3.connect(db_file)
        conn.execute("create table job (job_id int)")
        conn.execute("insert into job values (1)")
        conn.commit()
        conn.close()
        store.update_job(1, label="x")
        with patch.dict(cfg.arm_config, {"DB_BACKUP_KEEP": 2, "DB_BACKUP_DIR": ""}):
            for minute in range(3):
                target = maintenance.backup(datetime.datetime(2026, 11, 14, 3, minute))
        backups = sorted(os.listdir(os.path.join(self.tmp, "db", "backups")))
        self.assertEqual([b for b in backups if b.endswith(".db")], ["arm-20261114-0301.db", "arm-20261114-0302.db"])
        self.assertEqual(sqlite3.connect(target).execute("select count(*) from job").fetchone()[0], 1)

    def test_watchdog_flags_idle_jobs_once(self):
        log = self.touch("logs", "job.log")
        old = datetime.datetime.now().timestamp() - 3600
        os.utime(log, (old, old))
        job = SimpleNamespace(job_id=4, logfile="job.log", title="X", title_manual=None, label="X", status="ripping",
                              pid=None, pid_hash=None, errors=None, eject=MagicMock())
        query = MagicMock()
        query.filter.return_value.all.return_value = [job]
        from arm.models.job import Job
        with patch.dict(cfg.arm_config, {"LOGPATH": os.path.join(self.tmp, "logs"), "RAW_PATH": "",
                                         "TRANSCODE_PATH": "",
                                         "STUCK_JOB_MINUTES": 30, "STUCK_JOB_ACTION": "abandon"}), \
                patch.object(Job, "query", query), patch("arm.ui.db.session.commit"), \
                patch.object(maintenance.notify, "in_app"):
            self.assertEqual(maintenance.watchdog(), [4])
            self.assertEqual(maintenance.watchdog(), [])
        self.assertEqual(job.status, "fail")
        self.assertIn("no progress for 60 minutes", job.errors)
        job.eject.assert_called_once()

    def test_leftover_folders(self):
        raw = os.path.join(self.tmp, "raw")
        old_dir = os.path.dirname(self.touch("raw", "Old Movie", "t00.mkv"))
        new_dir = os.path.dirname(self.touch("raw", "New Movie", "t00.mkv"))
        old = datetime.datetime.now().timestamp() - 10 * 86400
        os.utime(os.path.join(old_dir, "t00.mkv"), (old, old))
        query = MagicMock()
        query.filter.return_value.all.return_value = []
        from arm.models.job import Job
        with patch.dict(cfg.arm_config, {"RAW_PATH": raw, "TRANSCODE_PATH": "", "CLEANUP_LEFTOVER_DAYS": 3}), \
                patch.object(Job, "query", query):
            found = maintenance.leftover_folders()
        self.assertIn(old_dir, found)
        self.assertNotIn(new_dir, found)


if __name__ == '__main__':
    unittest.main()
