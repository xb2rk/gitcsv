"""Authentication CLI contracts without starting a browser or using Google."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fccsv3.adapters.oauth import _save_token, login  # noqa: E402
from fccsv3.interfaces.cli import main  # noqa: E402


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".fccsv.json").write_text(
            json.dumps({
                "token_file": "private/google-token.json",
                "credentials_file": "private/client.json",
                "sources": [{"spreadsheet_id": "test", "tables": []}],
            }), encoding="utf-8",
        )

    def invoke(self, *args: str) -> tuple[int, str, str]:
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(args)
        return code, output.getvalue(), error.getvalue()

    def test_status_reports_missing_and_present_token_without_google(self) -> None:
        code, output, error = self.invoke(
            "auth", "status", "--project", str(self.root)
        )
        self.assertEqual(code, 1)
        self.assertIn("fccsv auth login", output)
        self.assertEqual(error, "")
        token = self.root / "private" / "google-token.json"
        _save_token(token, '{"token":"example"}')
        code, output, error = self.invoke(
            "--project", str(self.root), "auth", "status", "--format", "json"
        )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["exists"])
        self.assertEqual(error, "")
        self.assertFalse((self.root / ".fccsv3").exists())

    def test_login_resolves_project_paths_and_reauth_flag(self) -> None:
        with patch("fccsv3.interfaces.cli.oauth_login") as oauth:
            code, output, error = self.invoke(
                "auth", "login", "--project", str(self.root), "--reauth"
            )
        self.assertEqual(code, 0)
        oauth.assert_called_once_with(
            self.root.resolve() / "private" / "client.json",
            self.root.resolve() / "private" / "google-token.json",
            reauth=True,
        )
        self.assertIn("Google token ready", output)
        self.assertEqual(error, "")

    def test_local_env_paths_and_precedence(self) -> None:
        (self.root / ".env.local").write_text(
            "# Per-user paths\n"
            "FCCSV3_TOKEN_FILE=private/local-token.json\n"
            "FCCSV3_CREDENTIALS_FILE=private/local-client.json\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {}, clear=True):
            code, output, error = self.invoke(
                "auth", "status", "--project", str(self.root)
            )
            self.assertEqual((code, error), (1, ""))
            self.assertIn("private\\local-token.json", output.replace("/", "\\"))
            with patch("fccsv3.interfaces.cli.oauth_login") as oauth:
                code, _, error = self.invoke(
                    "auth", "login", "--project", str(self.root)
                )
            self.assertEqual((code, error), (0, ""))
            oauth.assert_called_once_with(
                (self.root / "private" / "local-client.json").resolve(),
                (self.root / "private" / "local-token.json").resolve(),
                reauth=False,
            )
        with patch.dict(os.environ, {"FCCSV3_TOKEN_FILE": "private/process.json"}):
            code, output, _ = self.invoke(
                "auth", "status", "--project", str(self.root)
            )
            self.assertEqual(code, 1)
            self.assertIn("process.json", output)
            code, output, _ = self.invoke(
                "auth", "status", "--project", str(self.root),
                "--token", "private/cli.json",
            )
            self.assertEqual(code, 1)
            self.assertIn("cli.json", output)
            self.assertNotIn("process.json", output)

    def test_missing_client_error_names_credentials_flag(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "--credentials"):
            login(self.root / "missing.json", self.root / "token.json")

    def test_token_replacement_leaves_no_temporary_file(self) -> None:
        token = self.root / "private" / "token.json"
        _save_token(token, "old")
        _save_token(token, "new")
        self.assertEqual(token.read_text(encoding="utf-8"), "new")
        self.assertEqual(tuple(token.parent.iterdir()), (token,))


if __name__ == "__main__":
    unittest.main()
