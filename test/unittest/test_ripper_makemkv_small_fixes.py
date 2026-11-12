"""
makemkv.progress_log() returned a shell-quoted path although no shell is used,
and makemkv_info() could pass an empty range to random.randrange().
"""
import random
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
from arm.ripper import makemkv  # noqa: E402


class TestProgressLogPath(unittest.TestCase):
    """progress_log: shlex.quote() put literal quotes into the argv element and the .batchinfo name"""

    def test_path_with_spaces_is_not_quoted(self):
        job = SimpleNamespace(job_id=12, config=SimpleNamespace(LOGPATH="/home/arm/my logs"))
        self.assertEqual(makemkv.progress_log(job), "/home/arm/my logs/progress/12.log")

    def test_plain_path_unchanged(self):
        job = SimpleNamespace(job_id=3, config=SimpleNamespace(LOGPATH="/home/arm/logs"))
        self.assertEqual(makemkv.progress_log(job), "/home/arm/logs/progress/3.log")


class TestMakemkvInfoSleepRange(unittest.TestCase):
    """makemkv_info: sleep=(10, MANUAL_WAIT_TIME, 10) is an empty range when MANUAL_WAIT_TIME <= 10"""

    def _first_sleep_arg(self, wait_time):
        job = SimpleNamespace(job_id=1, status=None,
                              config=SimpleNamespace(MINLENGTH=600, MANUAL_WAIT_TIME=wait_time))
        with patch.dict(makemkv.cfg.arm_config, {"MAX_CONCURRENT_MAKEMKVINFO": 1}), \
                patch.object(makemkv.utils, "sleep_check_process") as check, \
                patch.object(makemkv, "run", return_value=iter([])), \
                patch.object(makemkv, "sleep"), \
                patch.object(makemkv.db.session, "commit"):
            list(makemkv.makemkv_info(job, index=0))
        return check.call_args_list[0].kwargs["sleep"]

    def test_small_wait_time_gives_a_valid_range(self):
        for wait_time in (0, 5, 10):
            sleep = self._first_sleep_arg(wait_time)
            random.randrange(*sleep)  # raised ValueError: empty range

    def test_large_wait_time_is_kept(self):
        self.assertEqual(self._first_sleep_arg(300), (10, 300, 10))


if __name__ == '__main__':
    unittest.main()
