"""זיהוי דוברים לפי קול, מול פרופילים שמורים.

שלב 1 (נוכחי): placeholder - משאיר את התיוג הגנרי "דובר 1"/"דובר 2" שמגיע
מ-transcription.py, ללא זיהוי חוצה-הקלטות.

שלב 2 (עתידי): לכל segment יחושב speaker embedding באמצעות pyannote.audio,
וההשוואה תיעשה מול הפרופילים השמורים ב-Firestore (services/firestore_store.py)
לפי דמיון קוסינוס. דובר שהניקוד שלו עובר סף - יקבל את השם השמור. דובר חדש
יישאר "דובר N" עד שהמשתמש יתייג אותו במסך "זיהוי דוברים" באפליקציה, ואז
ה-embedding שלו יישמר לזיהוי עתידי.
"""

from app.models import TranscriptSegment


def identify_speakers(
    segments: list[TranscriptSegment], user_id: str
) -> list[TranscriptSegment]:
    # TODO(שלב 2): חישוב embeddings והתאמה מול Firestore.
    # כרגע מחזיר את התיוג הגנרי כפי שהוא.
    return segments
