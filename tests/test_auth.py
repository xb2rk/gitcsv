"""Tier-5 bundled identity + `doctor` origin line (release packaging).

Proves: dev runs never see a bundled file; frozen runs prefer next-to-exe
over inside-the-exe; explicit tiers beat bundled; the origin line names
the active identity without ever printing a secret.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fccsv.app import auth as auth_mod

CLIENT_DOC = {
    "installed": {
        "client_id": "abc123XYZ.apps.googleusercontent.com",
        "client_secret": "s3cr3t",
    }
}


def _write_client(path: Path) -> Path:
    path.write_text(json.dumps(CLIENT_DOC), encoding="utf-8")
    return path


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("FCCSV_CREDENTIALS", "FCCSV_CLIENT_JSON", "FCCSV_CLIENT_ID", "FCCSV_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)


def _freeze(monkeypatch: pytest.MonkeyPatch, meipass: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)


def test_bundled_none_in_dev() -> None:
    assert auth_mod.bundled_client_file() is None  # pytest is not frozen


def test_bundled_frozen_meipass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_client(tmp_path / "credentials.json")
    _freeze(monkeypatch, tmp_path)
    assert auth_mod.bundled_client_file() == tmp_path / "credentials.json"


def test_bundled_frozen_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _freeze(monkeypatch, tmp_path)
    assert auth_mod.bundled_client_file() is None


def test_resolve_prefers_project_file_over_bundled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundled_dir = tmp_path / "bundle"
    bundled_dir.mkdir()
    _write_client(bundled_dir / "credentials.json")
    _freeze(monkeypatch, bundled_dir)
    project = tmp_path / "proj"
    project.mkdir()
    _write_client(project / "credentials.json")
    _clear_env(monkeypatch)
    resolved = auth_mod.resolve_client_file(
        None, project_root=project, tool_dir=tmp_path, cwd=tmp_path
    )
    assert resolved == project / "credentials.json"


def test_resolve_falls_back_to_bundled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundled_dir = tmp_path / "bundle"
    bundled_dir.mkdir()
    _write_client(bundled_dir / "credentials.json")
    _freeze(monkeypatch, bundled_dir)
    empty = tmp_path / "empty"
    empty.mkdir()
    _clear_env(monkeypatch)
    resolved = auth_mod.resolve_client_file(None, project_root=empty, tool_dir=empty, cwd=empty)
    assert resolved == bundled_dir / "credentials.json"


def test_origin_names_file_and_id_tail(tmp_path: Path) -> None:
    client = _write_client(tmp_path / "credentials.json")
    origin = auth_mod.describe_client_origin(client)
    assert origin.startswith("client: file ")
    assert origin.endswith("(…3XYZ)")
    assert "s3cr3t" not in origin


def test_origin_none_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    assert auth_mod.describe_client_origin(None) == "client: none found"


def test_origin_env_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("FCCSV_CLIENT_ID", "x")
    monkeypatch.setenv("FCCSV_CLIENT_SECRET", "y")
    assert auth_mod.describe_client_origin(None) == (
        "client: env ($FCCSV_CLIENT_JSON / $FCCSV_CLIENT_ID+$FCCSV_CLIENT_SECRET)"
    )


def test_origin_bad_env_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("FCCSV_CLIENT_JSON", "{bad")
    assert "not valid JSON" in auth_mod.describe_client_origin(None)


def test_missing_sheets_extra_is_friendly_auth_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without the `sheets` extra, auth fails with a pip hint, not a traceback."""
    for mod in [m for m in sys.modules if m.split(".")[0] in ("google", "googleapiclient")]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.delitem(sys.modules, "google_auth_oauthlib", raising=False)
    monkeypatch.setitem(sys.modules, "google", None)
    from fccsv.domain.errors import AuthError

    with pytest.raises(AuthError, match=r"fccsv\[sheets\]"):
        auth_mod.load_credentials(None, tmp_path / "token.json")
