"""Manage a per-user Google token without coupling the core to OAuth SDKs."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def _save_token(path: Path, serialized: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_token(path: Path) -> Any:
    """Load or refresh an existing token; never open an interactive browser."""

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as error:
        raise RuntimeError("Install Google support: pip install 'fccsv3[sheets]'.") from error
    if not path.is_file():
        raise RuntimeError(f"Google token not found: {path}. Run fccsv3 auth login.")
    try:
        credentials = Credentials.from_authorized_user_file(str(path))
        if not credentials.valid:
            if not credentials.refresh_token:
                raise RuntimeError("Google token expired; run fccsv3 auth login --reauth.")
            credentials.refresh(Request())
            _save_token(path, credentials.to_json())
        return credentials
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError(f"Cannot use Google token {path}: {error}") from error


def login(client_path: Path, token_path: Path, *, reauth: bool = False) -> Path:
    """Reuse a valid token or run the local browser OAuth flow."""

    if not reauth and token_path.is_file():
        try:
            load_token(token_path)
            return token_path
        except RuntimeError:
            pass
    if not client_path.is_file():
        raise RuntimeError(
            f"Google OAuth client not found: {client_path}. "
            "Pass --credentials <file>."
        )
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as error:
        raise RuntimeError("Install Google support: pip install 'fccsv3[sheets]'.") from error
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_path), scopes=[SHEETS_SCOPE]
        )
        credentials = flow.run_local_server(port=0)
        _save_token(token_path, credentials.to_json())
    except Exception as error:
        raise RuntimeError(f"Google sign-in failed: {error}") from error
    return token_path
