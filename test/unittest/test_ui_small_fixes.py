"""
Small web UI fixes: login check on /testapprise, cleaning of custom titles,
and a UI restart that only stops the UI's own process.
"""
import os
import signal
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from werkzeug.exceptions import HTTPException

sys.path.insert(0, '/opt/arm')
from arm.ui import app, json_api  # noqa: E402
from arm.ui.jobs import jobs as jobs_routes  # noqa: E402
from arm.ui.settings import settings as settings_routes  # noqa: E402


class TestTestAppriseNeedsLogin(unittest.TestCase):
    """/testapprise sent a notification for anyone who could reach the UI"""

    def test_route_is_login_protected(self):
        # login_required wraps the view; functools.wraps exposes the original as __wrapped__
        self.assertTrue(hasattr(settings_routes.testapprise, "__wrapped__"))

    def test_anonymous_request_sends_nothing(self):
        with patch.object(settings_routes.ripper_utils, "notify") as notify, \
                patch.object(settings_routes, "flash"), \
                patch.dict(app.config, {"LOGIN_DISABLED": False}), \
                app.test_request_context("/testapprise"):
            try:
                settings_routes.testapprise()
            except HTTPException:
                pass  # 401 when no login view is configured
        notify.assert_not_called()


class TestCustomTitleIsCleaned(unittest.TestCase):
    """customtitle saved the raw title, so '/' and ':' ended up in folder names"""

    def _run(self, title):
        job = SimpleNamespace(job_id=5, title="Old", year="2000")
        query = MagicMock()
        query.get.return_value = job
        with app.test_request_context(f"/customTitle?job_id=5&title={title}&year=2019"), \
                patch.object(jobs_routes.Job, "query", query), \
                patch.object(jobs_routes.ui_utils, "job_id_validator"), \
                patch.object(jobs_routes.ui_utils, "database_updater") as updater, \
                patch.object(jobs_routes, "TitleSearchForm"), \
                patch.object(jobs_routes, "Notifications"), \
                patch.object(jobs_routes.db.session, "add"), \
                patch.object(jobs_routes, "flash"), \
                patch.object(jobs_routes, "url_for", return_value="/"), \
                patch.object(jobs_routes, "redirect", return_value="redirected"):
            jobs_routes.customtitle.__wrapped__()
        return updater.call_args.args[0]

    def test_title_is_cleaned_for_filenames(self):
        args = self._run("Mission%3A Impossible %2F Part 1")
        self.assertEqual(args["title"], "Mission- Impossible  Part 1")
        self.assertNotIn("/", args["title"])
        self.assertNotIn(":", args["title"])
        self.assertEqual(args["title_manual"], args["title"])

    def test_plain_title_unchanged(self):
        args = self._run("It%20Chapter%20Two")
        self.assertEqual(args["title"], "It Chapter Two")
        self.assertEqual(args["year"], "2019")


class TestRestartUiOnlyStopsTheUi(unittest.TestCase):
    """restart_ui ran 'pkill python3', which also killed every running rip"""

    def test_signals_only_own_process(self):
        with patch.object(json_api.os, "kill") as kill, \
                patch("subprocess.check_output") as check_output, \
                app.app_context():
            json_api.restart_ui()
        kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
        check_output.assert_not_called()


if __name__ == '__main__':
    unittest.main()
