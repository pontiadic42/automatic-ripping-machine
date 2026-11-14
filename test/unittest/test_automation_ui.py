"""
Automation web pages: phone buttons (signed links), Settings > Automation, the job card,
the home page data, and moving files when a finished job's title is corrected.
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
from arm.automation import notify, store  # noqa: E402
from arm.ui import app  # noqa: E402
from arm.ui.automation import automation as routes  # noqa: E402
from arm.ui.jobs import jobs as jobs_routes  # noqa: E402


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp, "arm.db")})
        self.patch.start()
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

    def tearDown(self):
        self.patch.stop()
        app.config["WTF_CSRF_ENABLED"] = True
        shutil.rmtree(self.tmp, ignore_errors=True)


def a_job():
    return SimpleNamespace(job_id=7, title="It", year="2017", video_type="movie", imdb_id="tt1396484",
                           poster_url=None, label="IT", status="waiting", title_manual=None, year_manual=None,
                           video_type_manual=None, imdb_id_manual=None, poster_url_manual=None, hasnicetitle=False)


class TestPhoneButtons(Temp):

    def test_bad_token_is_refused(self):
        response = self.client.post("/automation/act", data={"job": 7, "action": "confirm", "token": "x"})
        self.assertEqual(response.status_code, 403)
        form = {"job": 7, "action": "delete", "token": notify.sign(7, "delete")}
        response = self.client.post("/automation/act", data=form)
        self.assertEqual(response.status_code, 403)

    def test_confirm_makes_the_title_manual(self):
        job = a_job()
        store.add_attention(7, "confidence", "Not sure")
        query = MagicMock()
        query.get.return_value = job
        with patch.object(routes.Job, "query", query), patch.object(routes.db.session, "commit"):
            page = self.client.get(f"/automation/act?job=7&action=confirm&token={notify.sign(7, 'confirm')}")
            self.assertEqual(page.status_code, 200)
            self.assertIsNone(job.title_manual)  # GET only shows the button
            form = {"job": 7, "action": "confirm", "token": notify.sign(7, "confirm")}
            response = self.client.post("/automation/act", data=form)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["success"], True)
        self.assertEqual((job.title_manual, job.year_manual, job.imdb_id_manual), ("It", "2017", "tt1396484"))
        self.assertTrue(job.hasnicetitle)
        self.assertEqual(store.load_job(7).get("attention"), [])


class TestJobActions(Temp):
    """Banner buttons: accept, cancel, delete files, dismiss"""

    def post(self, job, action, **extra):
        query = MagicMock()
        query.get.return_value = job
        query.filter.return_value.count.return_value = 0
        with patch.object(routes.Job, "query", query), patch.object(routes.db.session, "commit"), \
                patch.dict(app.config, {"LOGIN_DISABLED": True}):
            return self.client.post("/automation/job_action", data=dict(job=job.job_id, action=action, **extra))

    def job(self, **fields):
        job = a_job()
        job.finished = False
        job.path = None
        for key, value in fields.items():
            setattr(job, key, value)
        return job

    def test_accept_while_waiting_confirms_the_title(self):
        job = self.job()
        store.add_attention(7, "confidence", "Not sure")
        response = self.post(job, "accept")
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(job.title_manual, "It")
        self.assertEqual(store.load_job(7)["attention"], [])

    def test_accept_after_finishing_only_clears_the_banner(self):
        job = self.job(finished=True, status="success")
        store.add_attention(7, "confidence", "Not sure")
        self.assertTrue(self.post(job, "accept").get_json()["success"])
        self.assertIsNone(job.title_manual)
        self.assertEqual(store.load_job(7)["attention"], [])

    def test_dismiss_only_that_kind(self):
        store.add_attention(7, "confidence", "Not sure")
        store.add_attention(7, "length", "Short file", level="bad")
        self.post(self.job(), "dismiss", kind="confidence")
        self.assertEqual([i["kind"] for i in store.load_job(7)["attention"]], ["length"])

    def test_dismiss_a_failure_banner_is_remembered(self):
        self.post(self.job(finished=True, status="fail"), "dismiss", kind="failed")
        self.assertTrue(store.load_job(7)["dismissed_failure"])

    def test_cancel_stops_a_running_job_only(self):
        store.add_attention(7, "confidence", "Not sure")
        with patch("arm.ui.json_api.abandon_job", return_value={"success": True}) as abandon:
            self.assertTrue(self.post(self.job(), "cancel").get_json()["success"])
        abandon.assert_called_once_with("7")
        self.assertEqual(store.load_job(7)["attention"], [])
        with patch("arm.ui.json_api.abandon_job") as abandon:
            response = self.post(self.job(finished=True), "cancel")
        self.assertEqual(response.status_code, 400)
        abandon.assert_not_called()

    def test_delete_files_removes_only_a_safe_finished_folder(self):
        completed = os.path.join(self.tmp, "completed")
        folder = os.path.join(completed, "movies", "It (2017)")
        os.makedirs(folder)
        open(os.path.join(folder, "It (2017).mkv"), "w").close()
        with patch.dict(cfg.arm_config, {"COMPLETED_PATH": completed, "JELLYFIN_URL": ""}):
            self.assertEqual(self.post(self.job(path=folder), "delete_files").status_code, 400)  # still running
            self.assertTrue(os.path.isdir(folder))
            for unsafe in (completed, os.path.join(completed, "movies"), "/etc"):
                self.assertEqual(self.post(self.job(finished=True, path=unsafe), "delete_files").status_code, 400)
            response = self.post(self.job(finished=True, path=folder), "delete_files")
        self.assertTrue(response.get_json()["success"])
        self.assertFalse(os.path.exists(folder))
        self.assertTrue(os.path.isdir(os.path.join(completed, "movies")))
        self.assertEqual(store.load_job(7)["files_deleted"], folder)

    def test_unknown_action_and_job(self):
        self.assertEqual(self.post(self.job(), "explode").status_code, 400)
        query = MagicMock()
        query.get.return_value = None
        with patch.object(routes.Job, "query", query), patch.dict(app.config, {"LOGIN_DISABLED": True}):
            self.assertEqual(self.client.post("/automation/job_action", data={"job": 99, "action": "accept"})
                             .status_code, 404)


class TestSettingsPage(Temp):

    def test_save_writes_arm_yaml_and_keeps_secrets(self):
        config_path = os.path.join(self.tmp, "arm.yaml")
        shutil.copy(cfg.arm_config_path, config_path)
        with open(config_path, "a") as handle:
            handle.write('\nNTFY_TOKEN: "tk_keep"\n')
        form = {"NTFY_SERVER": "https://ntfy.example", "NTFY_TOPIC": "arm", "NTFY_TOKEN": "",
                "CONFIDENCE_NOTIFY_BELOW": "60", "ALREADY_RIPPED_ACTION": "skip", "DB_BACKUP_TIME": "04:30",
                "TITLE_ANALYSIS": "on"}
        with patch.object(cfg, "arm_config_path", config_path), patch.dict(app.config, {"LOGIN_DISABLED": True}), \
                patch.dict(cfg.arm_config, {}):
            response = self.client.post("/automation/settings", data=form)
            self.assertEqual(response.status_code, 302)
            saved = cfg._load_config(config_path)
            self.assertEqual(cfg.arm_config["NTFY_SERVER"], "https://ntfy.example")
        self.assertEqual(saved["NTFY_SERVER"], "https://ntfy.example")
        self.assertEqual(saved["NTFY_TOKEN"], "tk_keep")
        self.assertEqual(saved["CONFIDENCE_NOTIFY_BELOW"], 60)
        self.assertEqual(saved["ALREADY_RIPPED_ACTION"], "skip")
        self.assertEqual(saved["DB_BACKUP_TIME"], "04:30")
        self.assertIs(saved["TITLE_ANALYSIS"], True)
        self.assertIs(saved["KEEP_ALTERNATE_CUTS"], False)  # unticked checkbox
        self.assertIn("MINLENGTH", saved)                    # the rest of arm.yaml is kept

    def test_page_renders(self):
        with patch.dict(app.config, {"LOGIN_DISABLED": True}), \
                patch.dict(cfg.arm_config, {"NTFY_TOKEN": "tk_storedsecret", "JELLYFIN_API_KEY": "jfsecret"}):
            html = self.client.get("/automation/settings").get_data(as_text=True)
        self.assertIn("Phone notifications (ntfy)", html)
        self.assertIn('name="NTFY_TOKEN"', html)
        self.assertNotIn("tk_storedsecret", html)
        self.assertNotIn("jfsecret", html)
        self.assertIn("Saved - leave empty to keep", html)


class TestJobCard(Temp):

    def test_card_renders_confidence_and_titles(self):
        reasons = ["The disc label doesn't say what it is"]
        store.update_job(3, confidence={"score": 42, "level": "low", "reasons": reasons},
                         titles={"ambiguous_language": False, "titles": [
                             {"tid": 0, "seconds": 8100, "role": "main", "rip": True, "label": "", "reason": "Longest",
                              "language": "eng", "audio": ["eng"]},
                             {"tid": 3, "seconds": 8100, "role": "duplicate", "rip": False, "label": "Copy of title 0",
                              "reason": "Same segments", "language": "", "audio": []}]},
                         attention=[{"kind": "confidence", "message": "Not sure this is It", "level": "warn"}])
        job = SimpleNamespace(job_id=3)
        data = jobs_routes._automation_view(job)
        with app.test_request_context("/jobdetail?job_id=3"):
            html = app.jinja_env.get_template("automation_card.html").render(automation=data, jobs=job)
        self.assertIn("<b>42</b>", html)
        self.assertIn("ARM isn't sure about this title", html)
        self.assertIn("Duplicate", html)
        self.assertIn("2:15:00", html)
        self.assertIn("Not sure this is It", html)


class TestRetitleRoute(Temp):

    def test_finished_job_files_move_when_title_is_corrected(self):
        completed = os.path.join(self.tmp, "completed")
        old = os.path.join(completed, "unidentified", "not identified")
        os.makedirs(old)
        store.add_attention(8, "confidence", "ARM couldn't identify this disc. Pick the title.")
        open(os.path.join(old, "not identified.mkv"), "w").close()
        job = SimpleNamespace(job_id=8, title="not identified", year="", video_type="unknown", status="success",
                              path=old, title_manual=None, year_manual=None, imdb_id=None, poster_url=None)
        query = MagicMock()
        query.get.return_value = job
        with patch.dict(cfg.arm_config, {"COMPLETED_PATH": completed, "JELLYFIN_URL": ""}), \
                patch.object(jobs_routes.Job, "query", query), \
                patch.object(jobs_routes.ui_utils, "job_id_validator"), \
                patch.object(jobs_routes.db.session, "commit"), patch.object(jobs_routes.db.session, "add"), \
                patch.object(jobs_routes, "Notifications"), patch.dict(app.config, {"LOGIN_DISABLED": True}):
            self.client.get("/updatetitle?job_id=8&title=Spirited Away&year=2001&type=movie&imdbID=tt0245429&poster=")
        new = os.path.join(completed, "movies", "Spirited Away (2001)")
        self.assertEqual(job.path, new)
        self.assertEqual(store.load_job(8).get("attention"), [])
        self.assertTrue(os.path.isfile(os.path.join(new, "Spirited Away (2001).mkv")))


if __name__ == '__main__':
    unittest.main()
