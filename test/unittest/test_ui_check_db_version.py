"""
ui.utils.check_db_version: the version check was indented inside
"if not os.path.isfile(db_file)", so an existing database was never checked
or upgraded, and a failed creation raised NameError on the cursor.
"""
import glob
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/opt/arm')
from arm.ui import utils as ui_utils  # noqa: E402

HEAD = "head_rev"


def make_db(path, version):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    conn.execute("INSERT INTO alembic_version VALUES (?)", (version,))
    conn.commit()
    conn.close()


def db_version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()


class TestCheckDbVersion(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.tmp.name, "arm.db")
        script = MagicMock()
        script.get_current_head.return_value = HEAD
        self.patches = [patch("alembic.script.ScriptDirectory.from_config", return_value=script)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _run(self, upgrade_effect=None):
        with patch("flask_migrate.upgrade", side_effect=upgrade_effect) as upgrade:
            ui_utils.check_db_version("/opt/arm", self.db_file)
        return upgrade

    def test_existing_current_db_is_left_alone(self):
        make_db(self.db_file, HEAD)
        upgrade = self._run()
        upgrade.assert_not_called()
        self.assertEqual(glob.glob(self.db_file + "_*"), [])

    def test_existing_old_db_is_backed_up_and_upgraded(self):
        make_db(self.db_file, "old_rev")

        def migrate(_mig_dir):
            conn = sqlite3.connect(self.db_file)
            conn.execute("UPDATE alembic_version SET version_num = ?", (HEAD,))
            conn.commit()
            conn.close()

        upgrade = self._run(migrate)
        upgrade.assert_called_once()
        backups = glob.glob(self.db_file + "_*")
        self.assertEqual(len(backups), 1)
        self.assertEqual(db_version(backups[0]), "old_rev")
        self.assertEqual(db_version(self.db_file), HEAD)

    def test_missing_db_is_created(self):
        upgrade = self._run(lambda _mig_dir: make_db(self.db_file, HEAD))
        upgrade.assert_called_once()
        self.assertEqual(db_version(self.db_file), HEAD)

    def test_failed_creation_does_not_raise(self):
        # upgrade() leaves no file behind (e.g. permissions): log and return, no NameError
        upgrade = self._run()
        upgrade.assert_called_once()
        self.assertFalse(os.path.exists(self.db_file))


if __name__ == '__main__':
    unittest.main()
