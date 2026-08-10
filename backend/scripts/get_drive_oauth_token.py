"""הפקת refresh_token חד-פעמית ל-Drive, בשם החשבון האישי של המשתמש.

הרצה (פעם אחת, מקומית, עם דפדפן זמין):

    python scripts/get_drive_oauth_token.py path/to/oauth_client_secret.json

קובץ ה-JSON מתקבל מ-GCP Console: APIs & Services > Credentials >
Create Credentials > OAuth client ID > Desktop app > Download JSON.

הסקריפט יפתח דפדפן לאישור הרשאה, ואז ידפיס client_id / client_secret /
refresh_token - יש להעתיק את השלושה ל-backend/.env.
"""

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

_SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> None:
    if len(sys.argv) != 2:
        print("שימוש: python get_drive_oauth_token.py <oauth_client_secret.json>")
        sys.exit(1)

    client_secret_path = sys.argv[1]
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, _SCOPES)
    credentials = flow.run_local_server(port=0)

    print("\nהעתיקו את השורות הבאות ל-backend/.env:\n")
    print(f"DRIVE_OAUTH_CLIENT_ID={credentials.client_id}")
    print(f"DRIVE_OAUTH_CLIENT_SECRET={credentials.client_secret}")
    print(f"DRIVE_OAUTH_REFRESH_TOKEN={credentials.refresh_token}")


if __name__ == "__main__":
    main()
