"""Google API Authentication manager supporting OAuth 2.0 and Service Accounts."""

from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google_auth_oauthlib.flow import InstalledAppFlow

from gitcsv.config.schema import AuthConfig

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


class AuthError(Exception):
    """Raised when Google API authentication fails."""


def resolve_credentials_file(project_root: Path, configured_path: str | Path | None = None) -> Path | None:
    """Find OAuth client secret or Service Account file with smart fallbacks.

    Checks:
    1. Explicit configured path.
    2. ./credentials.json or .gitcsv/credentials.json.
    3. client_secret_*.json in project root or .gitcsv/.
    4. ~/.gitcsv/credentials.json (global user credentials).
    5. ~/Downloads/client_secret_*.json (directly downloaded from GCP).
    """
    if configured_path:
        p = Path(configured_path)
        if not p.is_absolute():
            p = project_root / p
        if p.is_file():
            return p

    # 1. Project-local credentials.json
    root_client = project_root / "credentials.json"
    if root_client.is_file():
        return root_client

    dot_gitcsv_client = project_root / ".gitcsv" / "credentials.json"
    if dot_gitcsv_client.is_file():
        return dot_gitcsv_client

    # 2. Check for any client_secret_*.json downloaded directly from Google Cloud
    for folder in (project_root, project_root / ".gitcsv"):
        if folder.is_dir():
            secrets = list(folder.glob("client_secret_*.json"))
            if secrets:
                return secrets[0]

    # 3. User global ~/.gitcsv/credentials.json
    global_client = Path.home() / ".gitcsv" / "credentials.json"
    if global_client.is_file():
        return global_client

    # 4. User Downloads folder
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        downloaded = list(downloads.glob("client_secret_*.json"))
        if downloaded:
            return downloaded[0]

    return None


def get_credentials(auth_config: AuthConfig, project_root: Path) -> Credentials | ServiceAccountCredentials:
    """Resolve active credentials using OAuth 2.0 or Service Account."""
    # 1. Mode: Service Account
    if auth_config.mode == "service_account":
        if auth_config.credentials_json:
            try:
                info = json.loads(auth_config.credentials_json)
                return ServiceAccountCredentials.from_service_account_info(info, scopes=SCOPES)
            except Exception as err:
                raise AuthError(f"Failed to load Service Account from JSON string: {err}") from err

        cred_path = resolve_credentials_file(project_root, auth_config.credentials_file)
        if not cred_path or not cred_path.is_file():
            raise AuthError(
                "Service Account key file not found.\n"
                "Place your service account JSON in project root or specify it in .gitcsv/config.json"
            )

        return ServiceAccountCredentials.from_service_account_file(str(cred_path), scopes=SCOPES)

    # 2. Mode: OAuth 2.0
    token_path = Path(auth_config.token_file)
    if not token_path.is_absolute():
        token_path = project_root / token_path

    # Fall back to user-global token in ~/.gitcsv/token.json
    if not token_path.is_file():
        global_token = Path.home() / ".gitcsv" / "token.json"
        if global_token.is_file():
            token_path = global_token

    client_path = resolve_credentials_file(project_root, auth_config.credentials_file)
    if not client_path or not client_path.is_file():
        client_path = project_root / auth_config.credentials_file

    creds: Credentials | None = None

    # Try loading existing token
    if token_path.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            creds = None

    # Refresh token if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds

    # If no valid token, need login
    raise AuthError(
        f"Google OAuth token is missing or expired ({token_path}).\n"
        "Run 'gitcsv auth login' to sign in with your Google account."
    )


def interactive_oauth_login(
    client_credentials_path: Path,
    token_path: Path,
    *,
    client_json: str | None = None,
) -> None:
    """Run interactive local browser OAuth 2.0 flow and persist the token."""
    if client_json:
        info = json.loads(client_json)
        flow = InstalledAppFlow.from_client_config(info, SCOPES)
    else:
        if not client_credentials_path.is_file():
            raise AuthError(
                f"OAuth client secret file not found: {client_credentials_path}\n"
                "Download your OAuth Client JSON from Google Cloud Console and pass it to 'gitcsv auth login'."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_credentials_path), SCOPES)

    creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
