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

    # נתיב מודל טביעת-הקול (ONNX) - יורד ב-Dockerfile. ראה
    # pipeline/speaker_embedding.py.
    speaker_embedding_model_path: str = "./models/speaker_embedding.onnx"
    # סף דמיון-קוסינוס לצבירת קול לא-מזוהה לתוך פרופיל קיים (מיזוג "שקט" -
    # לא מציג שום שם למשתמש, רק מאחד קולות דומים לשורה אחת במסך "דוברים
    # לא מזוהים" במקום לפזר אותם לכמה שורות). טעות כאן זולה - ברירת המחדל
    # בדוגמה הרשמית של sherpa-onnx לאותה משפחת מודלים (wespeaker).
    speaker_match_threshold: float = 0.6
    # סף מחמיר יותר, ספציפית להצמדה אוטומטית של שם קיים ("דנה", "אמא") לדובר
    # בתמלול/בסיכום. טעות כאן יקרה - שם שגוי גרוע יותר מ"דובר N" (ראה
    # summarize.py:UNKNOWN_SPEAKER, ואירוע 2026-08-17: קול של אישה זוהה
    # בטעות כ"אמא"), אז כשלא בטוחים עדיף להשאיר תווית גנרית ולתת למשתמש
    # לתייג ידנית במסך "דוברים לא מזוהים" (ראה speaker_id._best_match).
    speaker_match_threshold_named: float = 0.82


settings = Settings()
