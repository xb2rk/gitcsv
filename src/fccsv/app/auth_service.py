"""OAuth desktop flow. Google imports stay inside functions.

Client identity resolution (teammates need NO credentials.json file):
1. explicit --credentials flag / $FCCSV_CREDENTIALS file path,
2. $FCCSV_CLIENT_JSON inline client-secrets JSON (bake at package time),
3. $FCCSV_CLIENT_ID + $FCCSV_CLIENT_SECRET pair (same, split form),
4. credentials.json search: project root > tools/fccsv dir > working dir.

token.json is always per-user ($FCCSV_TOKEN / --token / project root) and
must never be shared: it grants API access as that user.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fccsv.domain.exceptions import AuthError

# tools/fccsv/src/fccsv/app/auth_service.py -> parents[3] == tools/fccsv.
_TOOL_DIR = Path(__file__).resolve().parents[3]


def resolve_auth_paths(
    project_root: Path,
    credentials_name: str,
    token_name: str,
    *,
    credentials: str | None = None,
    token: str | None = None,
) -> tuple[Path, Path]:
    """Locate credentials.json / token.json without forcing one directory."""

    def first(*cands: Path | None) -> Path | None:
        return next((p for p in cands if p is not None and p.exists()), None)

    def pick(flag: str | None, env_var: str, name: str) -> Path:
        env = os.environ.get(env_var)
        if flag:
            return Path(flag).expanduser()
        if env:
            return Path(env).expanduser()
        found = first(
            project_root / name,
            _TOOL_DIR / name,
            Path.cwd() / name,
        )
        return found if found is not None else project_root / name

    return (
        pick(credentials, "FCCSV_CREDENTIALS", credentials_name),
        pick(token, "FCCSV_TOKEN", token_name),
    )


def client_config_from_env() -> dict | None:
    """Baked client identity: full JSON or id+secret pair, else None."""
    inline = os.environ.get("FCCSV_CLIENT_JSON")
    if inline:
        try:
            return json.loads(inline)
        except json.JSONDecodeError as exc:
            raise ValueError(f"$FCCSV_CLIENT_JSON is not valid JSON: {exc}") from exc
    client_id = os.environ.get("FCCSV_CLIENT_ID")
    client_secret = os.environ.get("FCCSV_CLIENT_SECRET")
    if client_id and client_secret:
        return {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
    return None


def load_credentials(token_file: Path, credentials_file: Path, scopes: list[str]):
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise ImportError("Missing Sheets extra: pip install 'fccsv[sheets]'") from exc

    creds = None
    if token_file.exists():
        # Do not pass the current requested scopes here. A stored token may
        # predate an added optional scope (such as Drive metadata); refresh
        # tokens cannot be broadened and Google returns invalid_scope. Its
        # recorded scopes continue to authorize Sheets; `fccsv auth` upgrades
        # the grant when the user wants the Drive fast path.
        creds = Credentials.from_authorized_user_file(str(token_file))
    if creds is None or not creds.valid:
        if creds is not None and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                raise AuthError(
                    "OAuth token refresh failed. Run 'fccsv auth' to create a new user grant."
                ) from exc
        else:
            config = client_config_from_env()
            if config is not None:
                flow = InstalledAppFlow.from_client_config(config, scopes)
            else:
                if not credentials_file.exists():
                    raise FileNotFoundError(
                        f"OAuth client identity not found: {credentials_file}. "
                        "Either save credentials.json there (see README), or set "
                        "$FCCSV_CLIENT_JSON (or $FCCSV_CLIENT_ID/$FCCSV_CLIENT_SECRET)."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), scopes)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds
