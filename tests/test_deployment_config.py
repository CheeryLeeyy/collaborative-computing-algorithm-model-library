import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class DeploymentConfigTests(unittest.TestCase):
    def test_local_file_is_literal_and_environment_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            root = Path(temporary)
            marker = root / "must-not-be-created"
            env = root / ".env"
            env.write_text(
                "# local settings\nALGO_USERNAME='fixture-account'\n"
                "ALGO_PASSWORD='fixture # secret'\n"
                f"ALGO_ROOT='$(touch {marker})'\n"
                "export ALGO_PORT=8099 # comment\nALGO_HOST=127.0.0.1\n",
                encoding="utf-8",
            )
            os.environ["ALGO_PORT"] = "8088"
            server.load_env_file(env)
            self.assertEqual(os.environ["ALGO_USERNAME"], "fixture-account")
            self.assertEqual(os.environ["ALGO_PASSWORD"], "fixture # secret")
            self.assertEqual(os.environ["ALGO_PORT"], "8088")
            self.assertEqual(os.environ["ALGO_ROOT"], f"$(touch {marker})")
            self.assertFalse(marker.exists())

    def test_missing_local_file_and_invalid_lines(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            env = Path(temporary) / ".env"
            server.load_env_file(env)
            self.assertNotIn("ALGO_PASSWORD", os.environ)
            for line in ("BAD_KEY=x", "ALGO_PASSWORD='unclosed", "ALGO_PASSWORD=two words"):
                env.write_text("ALGO_PORT=8099\n" + line, encoding="utf-8")
                with self.assertRaises(SystemExit):
                    server.load_env_file(env)
                self.assertNotIn("ALGO_PORT", os.environ)  # Do not apply a partial file.

    def test_cli_refuses_legacy_fallback_before_listening_or_log_migration(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            root = Path(temporary)
            with patch.object(server, "load_env_file"), \
                 patch("sys.argv", ["server.py", "--root", str(root), "--port", "0"]), \
                 patch.object(server, "AlgorithmServer") as listener, \
                 patch.object(server, "migrate_legacy_operation_log") as migrate:
                with self.assertRaisesRegex(SystemExit, "拒绝使用"):
                    server.main()
                listener.assert_not_called()
                migrate.assert_not_called()

    def test_default_data_root_is_portable(self):
        self.assertEqual(server.DEFAULT_DATA_ROOT, server.APP_DIR.parent / "unzips")
