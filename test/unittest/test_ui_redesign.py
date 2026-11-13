"""
New home page (drive bays) and library: the dashboard JSON, the library page,
the "identified" rule, and the cached HandBrake / GitHub checks.
"""
import datetime
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/opt/arm')
from arm.ui import app, json_api, utils as ui_utils  # noqa: E402
from arm.ui.history import history as history_routes  # noqa: E402
from arm.ui.settings import settings as settings_routes  # noqa: E402


def make_job(**fields):
    job = dict(job_id=7, title="Spirited Away", year="2001", label="SPIRITED_AWAY", video_type="movie",
               disctype="dvd", status="success", poster_url="https://image.tmdb.org/t/p/original/x.jpg",
               logfile="SPIRITED_AWAY.log", errors=None, devpath="/dev/sr0", hasnicetitle=True,
               title_manual=None, stop_time=datetime.datetime(2026, 11, 12, 3, 4, 5))
    job.update(fields)
    return SimpleNamespace(**job)


class TestIdentifiedRule(unittest.TestCase):

    def test_matched_movie_is_identified(self):
        self.assertTrue(json_api._is_identified(make_job()))

    def test_no_metadata_match_is_unidentified_even_with_custom_title(self):
        job = make_job(title="When Marnie was There", title_manual="When Marnie was There",
                       video_type="unknown", hasnicetitle=False)
        self.assertFalse(json_api._is_identified(job))

    def test_placeholder_titles_are_unidentified(self):
        for title in (None, "", "None", "not identified"):
            self.assertFalse(json_api._is_identified(make_job(title=title)), title)

    def test_summary_has_iso_stop_time_and_flags(self):
        summary = json_api._job_summary(make_job(status="fail", stop_time=None))
        self.assertIsNone(summary["stop_time"])
        self.assertEqual(json_api._job_summary(make_job())["stop_time"], "2026-11-12T03:04:05")
        self.assertTrue(json_api._job_summary(make_job(disctype="music", hasnicetitle=False))["identified"])


class TestDashboardJson(unittest.TestCase):

    def test_dashboard_lists_drives_recent_jobs_and_stats(self):
        drive = SimpleNamespace(name="HL-DT-ST", mount="/dev/sr1", model="BD-RE WH14NS40 ", type="CD/DVD/BluRay",
                                job_id_current=23)
        unmounted = SimpleNamespace(name="old", mount="", model="", type="", job_id_current=None)
        drives_query = MagicMock()
        drives_query.filter.return_value.order_by.return_value.all.return_value = [drive, unmounted]
        jobs_query = MagicMock()
        jobs_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [make_job()]
        server = SimpleNamespace(cpu_util=3.0, cpu_temp=41.0, memory_percent=15.0, storage_completed_free=365.6,
                                 storage_completed_percent=13.1, storage_transcode_free=365.6)
        from arm.models.system_drives import SystemDrives
        from arm.ui.settings import ServerUtil
        with app.app_context(), \
                patch.object(SystemDrives, "query", drives_query), \
                patch.object(json_api.Job, "query", jobs_query), \
                patch.object(ServerUtil, "ServerUtil", return_value=server), \
                patch.object(settings_routes, "check_hw_transcode_support",
                             return_value={"intel": True, "nvidia": False, "amd": False}):
            data = json_api.get_dashboard()
        self.assertTrue(data["success"])
        self.assertEqual([d["mount"] for d in data["drives"]], ["/dev/sr1"])
        self.assertEqual(data["drives"][0]["model"], "BD-RE WH14NS40")
        self.assertEqual(data["recent"][0]["title"], "Spirited Away")
        self.assertTrue(data["stats"]["hw"]["intel"])
        self.assertEqual(data["stats"]["completed_free"], 365.6)

    def test_dashboard_mode_is_routed(self):
        with app.test_request_context("/json?mode=dashboard"), \
                patch.object(ui_utils, "authenticated_state", return_value=True), \
                patch.object(json_api, "get_dashboard", return_value={"success": True, "mode": "dashboard"}), \
                patch.object(json_api, "get_notifications", return_value=[]):
            from arm.ui.jobs import jobs as jobs_routes
            response = jobs_routes.feed_json()
        self.assertIn(b'"mode": "dashboard"', response.data)


class TestLibraryPage(unittest.TestCase):

    def _render(self, url, jobs):
        pages = SimpleNamespace(items=jobs, total=len(jobs), pages=1, page=1, has_prev=False, has_next=False,
                                prev_num=None, next_num=None)
        query = MagicMock()
        query.filter.return_value = query
        query.count.return_value = len(jobs)
        query.order_by.return_value.paginate.return_value = pages
        with app.test_request_context(url), patch.object(history_routes.Job, "query", query), \
                patch.dict(app.config, {"LOGIN_DISABLED": True}):
            return history_routes.library()

    def test_library_renders_small_posters_and_badges(self):
        html = self._render("/library", [
            make_job(),
            make_job(job_id=8, title="not identified", label="WWJD", hasnicetitle=False, video_type="unknown",
                     poster_url="None"),
            make_job(job_id=9, status="fail", title="The Silence of the Lambs")])
        self.assertIn("/t/p/w342/x.jpg", html)
        self.assertNotIn("/t/p/original/", html)
        self.assertIn("WWJD", html)
        self.assertIn("Unidentified", html)
        self.assertIn("Failed", html)
        self.assertIn('href="/jobdetail?job_id=9"', html)

    def test_unknown_filter_falls_back_to_all_and_search_is_escaped(self):
        html = self._render('/library?filter=evil&q=<script>', [])
        self.assertRegex(html, r'arm-chip active"\s+href="/library\?filter=all')
        self.assertNotIn('value="<script>"', html)
        self.assertIn('value="&lt;script&gt;"', html)


class TestCachedChecks(unittest.TestCase):

    def test_handbrake_hardware_check_runs_once(self):
        settings_routes.check_hw_transcode_support.cache_clear()
        with app.app_context(), patch.object(settings_routes, "arm_subprocess",
                                             return_value="qsv: is available on GPU 0") as run:
            first = settings_routes.check_hw_transcode_support()
            second = settings_routes.check_hw_transcode_support()
        settings_routes.check_hw_transcode_support.cache_clear()
        self.assertEqual(run.call_count, 1)
        self.assertTrue(first["intel"])
        self.assertEqual(first, second)

    def test_github_update_check_is_cached(self):
        ui_utils._git_update_cache.clear()
        response = MagicMock()
        response.json.return_value = {"sha": "abc1234ffff"}
        with app.app_context(), patch.object(ui_utils.requests, "get", return_value=response) as get:
            self.assertTrue(ui_utils.git_check_updates("abc1234"))
            self.assertTrue(ui_utils.git_check_updates("abc1234"))
        ui_utils._git_update_cache.clear()
        self.assertEqual(get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
