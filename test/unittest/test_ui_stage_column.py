"""
The web UI progress poller wrote display text ("Track 1/3<br>Saving to MKV file")
into job.stage and committed it. job.stage is the job's timestamp, which ARM uses
for unique log and folder names. The display text now only goes into the JSON.
"""
import datetime
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/opt/arm')
from arm.ui import app, json_api  # noqa: E402

STAMP = "176290000000"


def make_job(**kwargs):
    values = {"job_id": 7, "stage": STAMP, "no_of_titles": 3, "progress": None, "eta": None,
              "start_time": datetime.datetime.now() - datetime.timedelta(minutes=2)}
    values.update(kwargs)
    return SimpleNamespace(**values)


class LogDirTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "progress"))
        self.cfg = patch.dict(json_api.cfg.arm_config, {"LOGPATH": self.tmp.name})
        self.cfg.start()

    def tearDown(self):
        self.cfg.stop()
        self.tmp.cleanup()

    def write(self, name, text):
        with open(os.path.join(self.tmp.name, name), "w") as f:
            f.write(text)


class TestMakemkvStage(LogDirTest):

    def test_stage_column_is_not_overwritten(self):
        self.write("progress/7.log", 'PRGC:5018,0,"Saving to MKV file"\nPRGV:16384,0,65536\n')
        self.write("progress/7.log.batchinfo", "BINF:1762900000,1,3,0000")
        job = make_job()
        results = {}
        with patch.object(json_api.db.session, "commit"):
            json_api.process_makemkv_logfile(job, results)
        self.assertEqual(job.stage, STAMP)
        self.assertEqual(results["stage"], "Track 1/3<br>Saving to MKV file")

    def test_missing_batch_info_shows_unknown_without_touching_stage(self):
        self.write("progress/7.log", 'PRGC:5018,0,"Saving to MKV file"\n')
        job = make_job()
        results = {}
        with patch.object(json_api.db.session, "commit"):
            json_api.process_makemkv_logfile(job, results)
        self.assertEqual(job.stage, STAMP)
        self.assertTrue(results["stage"].startswith("Unknown"))


class TestHandbrakeStage(LogDirTest):

    def test_stage_column_is_not_overwritten(self):
        self.write("job.log", "Processing track #2 of 3\n"
                              "Encoding: task 1 of 1, 45.23 % (30.10 fps, avg 29.50 fps, ETA 00h10m00s)\n")
        job = make_job()
        results = json_api.process_handbrake_logfile(os.path.join(self.tmp.name, "job.log"), job, {})
        self.assertEqual(job.stage, STAMP)
        self.assertEqual(results["stage"], "1 of 1 - 2/3")


class TestAudioStage(LogDirTest):

    def test_stage_column_is_not_overwritten(self):
        self.write("cd.log", "Grabbing track 02: Song (track02.wav)\n")
        job = make_job()
        results = {}
        json_api.process_audio_logfile("cd.log", job, results)
        self.assertEqual(job.stage, STAMP)
        self.assertIn("stage", results)


class TestJobListStage(unittest.TestCase):

    def _joblist(self, parser_stage):
        job = MagicMock(job_id=7, logfile="job.log", stage=STAMP)
        job.config.get_d.return_value = {}
        job.get_d.return_value = {"job_id": 7, "stage": STAMP, "status": "ripping"}

        def fake_process(_log, _job, results):
            if parser_stage is not None:
                results["stage"] = parser_stage
            return results

        query = MagicMock()
        query.filter.return_value.all.return_value = [job]
        with app.app_context(), \
                patch.object(json_api.db.session, "query", return_value=query), \
                patch.object(json_api, "process_logfile", side_effect=fake_process), \
                patch.object(json_api, "authenticated_state", return_value=True):
            return json_api.get_x_jobs("joblist")["results"][0]

    def test_display_stage_from_log_is_returned(self):
        self.assertEqual(self._joblist("Track 1/3<br>Saving to MKV file")["stage"],
                         "Track 1/3<br>Saving to MKV file")

    def test_without_log_stage_the_column_is_returned(self):
        self.assertEqual(self._joblist(None)["stage"], STAMP)


if __name__ == '__main__':
    unittest.main()
