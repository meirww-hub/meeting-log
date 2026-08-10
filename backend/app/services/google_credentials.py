"""פרטי הזדהות מפורשים לחשבון השירות, ל-Firestore בלבד.

לא מסתמכים על משתנה הסביבה GOOGLE_APPLICATION_CREDENTIALS של מערכת ההפעלה
(ה-Application Default Credentials של גוגל) - כי הוא לא בהכרח מוגדר בתהליך
שמריץ את uvicorn, גם אם הערך קיים ב-.env/settings. בונים credentials ישירות
מהקובץ, כמו שכבר עושים ב-services/drive.py ל-OAuth.
"""

from functools import lru_cache

from google.oauth2 import service_account

from app.config import settings


@lru_cache
def get_service_account_credentials() -> service_account.Credentials:
    return service_account.Credentials.from_service_account_file(
        settings.google_application_credentials
    )
