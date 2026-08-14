from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    google_cloud_project: str = ""
    google_application_credentials: str = "./service-account.json"
    drive_root_folder_id: str = ""
    # מזהה מסד ה-Firestore בפועל (נראה ב-console כ-Database ID). לא "(default)"
    # למרות השם - כשיוצרים דרך ה-console זה נוצר עם ID פשוט "default".
    firestore_database_id: str = "default"
    gemini_api_key: str = ""

    # OAuth בשם המשתמש האישי (לא Service Account) - כי חשבון Gmail אישי אין
    # לו מכסת אחסון שמישה ל-Service Account. מופק פעם אחת ע"י
    # scripts/get_drive_oauth_token.py ולא מתחדש ידנית (refresh_token קבוע).
    drive_oauth_client_id: str = ""
    drive_oauth_client_secret: str = ""
    drive_oauth_refresh_token: str = ""

    # מפתח שיתופי פשוט שהאפליקציה שולחת בכותרת X-API-Key - כדי שהשרת (פרוס
    # פומבית ב-Cloud Run) לא יהיה פתוח לחלוטין לכל האינטרנט.
    backend_api_key: str = ""


settings = Settings()
