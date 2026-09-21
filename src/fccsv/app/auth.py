"""OAuth client identity: 5-tier resolution, per-user token. Ported from v1.

1. `--credentials` path / `$FCCSV_CREDENTIALS`
2. File search: project root > tool dir > cwd (`credentials.json`)
3. `$FCCSV_CLIENT_JSON` inline document
4. `$FCCSV_CLIENT_ID` + `$FCCSV_CLIENT_SECRET` pair
5. Bundled with the release (PyInstaller onefile). Lowest priority on
   purpose: any override above wins, so rotation never needs a rebuild.

Tokens are per-user (`token.json`, never committed). Stored tokens are
reused WITHOUT passing scopes so old tokens survive scope upgrades.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from fccsv.core.constants import DEFAULT_CREDENTIALS_FILENAME, DEFAULT_TOKEN_FILENAME, SCOPES


def resolve_client_file(
    explicit: str | None,
    *,
    project_root: Path,
    tool_dir: Path,
    cwd: Path,
) -> Path | None:
    if explicit:
        return Path(explicit)
    env_path = os.environ.get("FCCSV_CREDENTIALS", "").strip()
    if env_path:
        return Path(env_path)
    for base in (project_root, tool_dir, cwd):
        candidate = base / DEFAULT_CREDENTIALS_FILENAME
        if candidate.exists():
            return candidate
    return bundled_client_file()


def bundled_client_file() -> Path | None:
    """Tier 5: client identity shipped inside the release executable.

    Next-to-exe wins over inside-the-exe so a rotated file dropped beside
    `fccsv.exe` takes effect on next run without a rebuild. Returns None
    outside a frozen build: dev runs already see the working-copy file
    through the tier-2 search above.
    """
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).resolve().parent
    meipass = Path(str(getattr(sys, "_MEIPASS", str(exe_dir))))
    for candidate in (
        exe_dir / DEFAULT_CREDENTIALS_FILENAME,
        meipass / DEFAULT_CREDENTIALS_FILENAME,
        meipass / "assets" / DEFAULT_CREDENTIALS_FILENAME,
    ):
        if candidate.is_file():
            return candidate
    return None


def _client_id_tail(client_file: Path) -> str:
    """Last 4 chars of the client id for `doctor` display. Never the secret."""
    try:
        data = json.loads(client_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    if not isinstance(data, dict):
        return ""
    section = data.get("installed", data.get("web", {}))
    if not isinstance(section, dict):
        return ""
    client_id = section.get("client_id", "")
    if not isinstance(client_id, str) or not client_id:
        return ""
    bare = client_id.split(".")[0]  # strip ".apps.googleusercontent.com"
    return f" (…{bare[-4:]})"


def describe_client_origin(client_file: Path | None) -> str:
    """One line for `doctor`: which tier supplies the active identity."""
    if client_file is None:
        try:
            has_env = inline_client_config() is not None
        except json.JSONDecodeError:
            return "client: env ($FCCSV_CLIENT_JSON is not valid JSON)"
        if has_env:
            return "client: env ($FCCSV_CLIENT_JSON / $FCCSV_CLIENT_ID+$FCCSV_CLIENT_SECRET)"
        return "client: none found"
    try:
        resolved = client_file.resolve()
    except OSError:
        resolved = client_file
    bundled = bundled_client_file()
    try:
        is_bundled = bundled is not None and resolved == bundled.resolve()
    except OSError:
        is_bundled = False
    label = "bundled with release" if is_bundled else f"file {resolved}"
    return f"client: {label}{_client_id_tail(client_file)}"


def resolve_token_file(explicit: str | None, *, project_root: Path) -> Path:
    if explicit:
        return Path(explicit)
    env_path = os.environ.get("FCCSV_TOKEN", "").strip()
    if env_path:
        return Path(env_path)
    return project_root / DEFAULT_TOKEN_FILENAME


def inline_client_config() -> dict[str, Any] | None:
    raw = os.environ.get("FCCSV_CLIENT_JSON", "").strip()
    if raw:
        data: Any = json.loads(raw)
        return data if isinstance(data, dict) else None
    client_id = os.environ.get("FCCSV_CLIENT_ID", "").strip()
    client_secret = os.environ.get("FCCSV_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uris": ["http://localhost"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
    return None


def load_credentials(client_file: Path | None, token_file: Path) -> Any:
    """Google libs imported lazily; raises AuthError with actionable hints."""
    from fccsv.domain.errors import AuthError

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise AuthError(
            "Google client libraries are not installed. "
            "Run: pip install fccsv[sheets] (or pip install ./tools/fccsv-v2[sheets])"
        ) from exc

    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                str(token_file)
            )
        except Exception as exc:
            raise AuthError(
                f"Token file corrupt: {token_file}. Delete it and re-run auth."
            ) from exc
    if creds is not None and creds.valid:
        return creds
    if creds is not None and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_file.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception as exc:
            raise AuthError(
                f"Token refresh failed ({exc}). Delete {token_file} and re-run auth."
            ) from exc
    config: dict[str, Any] | None = None
    if client_file is not None and client_file.exists():
        try:
            config = json.loads(client_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise AuthError(f"Cannot read client file {client_file}: {exc}") from exc
    else:
        try:
            config = inline_client_config()
        except json.JSONDecodeError as exc:
            raise AuthError(f"$FCCSV_CLIENT_JSON is not valid JSON: {exc}") from exc
    if config is None:
        raise AuthError(
            "No OAuth client found. Provide credentials.json "
            "(--credentials, $FCCSV_CREDENTIALS, next to the project, "
            "next to fccsv.exe, or bundled with the release), "
            "or $FCCSV_CLIENT_JSON / $FCCSV_CLIENT_ID+$FCCSV_CLIENT_SECRET."
        )
    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    creds = flow.run_local_server(port=0)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds
