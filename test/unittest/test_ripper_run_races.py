"""
Real problems seen on 2026-11-14 with two drives busy:
- two runs for one disc started 0.3 s apart and both passed the duplicate check
- the title plan was left uncommitted, keeping SQLite write-locked for the whole rip
"""
import multiprocessing
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
from arm.automation import hooks  # noqa: E402
from arm.ripper import utils  # noqa: E402


def hold_lock(dev, seconds, ready):
    with utils.device_lock(dev):
        ready.set()
        time.sleep(seconds)


class TestDeviceLock(unittest.TestCase):

    def test_second_run_waits_for_the_first(self):
        ready = multiprocessing.Event()
        holder = multiprocessing.Process(target=hold_lock, args=("/dev/srtest", 1.0, ready))
        holder.start()
        ready.wait(5)
        started = time.time()
        with utils.device_lock("/dev/srtest", timeout=10):
            waited = time.time() - started
        holder.join()
        self.assertGreater(waited, 0.5)

    def test_gives_up_after_timeout(self):
        ready = multiprocessing.Event()
        holder = multiprocessing.Process(target=hold_lock, args=("/dev/srtest2", 2.0, ready))
        holder.start()
        ready.wait(5)
        with self.assertRaises(utils.RipperException):
            with utils.device_lock("/dev/srtest2", timeout=0.3):
                pass
        holder.join()

    def test_other_drives_dont_wait(self):
        ready = multiprocessing.Event()
        holder = multiprocessing.Process(target=hold_lock, args=("/dev/srtest3", 1.5, ready))
        holder.start()
        ready.wait(5)
        started = time.time()
        with utils.device_lock("/dev/srtest4", timeout=5):
            self.assertLess(time.time() - started, 0.3)
        holder.join()


class TestPlanIsSaved(unittest.TestCase):

    def test_apply_plan_commits(self):
        tracks = [SimpleNamespace(track_number="0", process=None), SimpleNamespace(track_number="1", process=None)]
        job = SimpleNamespace(tracks=tracks)
        decisions = {0: {"rip": True, "role": "main"}, 1: {"rip": False, "role": "duplicate"}}
        with patch("arm.ui.db.session.commit") as commit:
            self.assertTrue(hooks.apply_plan(job, decisions))
        commit.assert_called_once()
        self.assertEqual([t.process for t in tracks], [True, False])


if __name__ == '__main__':
    unittest.main()
