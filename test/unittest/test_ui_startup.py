"""
The web UI must start while rips hold the database: the startup drive rescan used to
crash it with "database is locked", and runit restarted it in a loop.
"""
import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError

sys.path.insert(0, '/opt/arm')
from arm import runui  # noqa: E402
from arm.models.job import Job  # noqa: E402


def locked():
    return OperationalError("UPDATE system_drives", {}, Exception("database is locked"))


class TestStartup(unittest.TestCase):

    def run_startup(self, active_jobs, update_effects):
        query = MagicMock()
        query.filter.return_value.count.return_value = active_jobs
        with patch.object(runui.arm.ui.utils, "arm_db_check", return_value={"db_current": True}), \
                patch.object(Job, "query", query), \
                patch.object(runui.arm.ui.settings.DriveUtils, "drives_update",
                             side_effect=update_effects) as update, \
                patch("arm.ui.db.session.rollback"), patch("time.sleep") as sleep:
            runui.startup(attempts=3, wait=1)
        return update, sleep

    def test_skips_rescan_while_a_rip_runs(self):
        update, _sleep = self.run_startup(2, [None])
        update.assert_not_called()

    def test_retries_when_locked_then_succeeds(self):
        update, sleep = self.run_startup(0, [locked(), None])
        self.assertEqual(update.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_gives_up_without_crashing(self):
        update, _sleep = self.run_startup(0, [locked(), locked(), locked()])
        self.assertEqual(update.call_count, 3)

    def test_other_database_errors_dont_loop(self):
        other = OperationalError("x", {}, Exception("no such table"))
        update, sleep = self.run_startup(0, [other])
        self.assertEqual(update.call_count, 1)
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
