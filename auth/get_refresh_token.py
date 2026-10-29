"""One-time local helper that mints the OAuth refresh token used by GitHub Actions.

Usage:
  1. In Google Cloud Console, enable "YouTube Data API v3" and create an
     OAuth client of type "Desktop app", then download the JSON.
  2. python auth/get_refresh_token.py /path/to/client_secret_xxx.json
  3. Copy the printed values into your GitHub repo secrets.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    secrets_path = Path(sys.argv[1])
    if not secrets_path.exists():
        print(f"client secret file not found: {secrets_path}")
        return 2

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    credentials = flow.run_local_server(port=0, prompt="consent")

    payload = json.loads(secrets_path.read_text(encoding="utf-8"))
    installed = payload.get("installed") or payload.get("web") or {}

    refresh_token = credentials.refresh_token
    if not refresh_token:
        print(
            "No refresh token returned. Re-run after revoking prior access at "
            "https://myaccount.google.com/permissions so a fresh consent screen appears."
        )
        return 1

    print("\nAdd these as GitHub repository secrets:\n")
    print(f"YT_CLIENT_ID     = {installed.get('client_id', '')}")
    print(f"YT_CLIENT_SECRET = {installed.get('client_secret', '')}")
    print(f"YT_REFRESH_TOKEN = {refresh_token}")
    print("\nThe refresh token is a credential - treat it like a password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
