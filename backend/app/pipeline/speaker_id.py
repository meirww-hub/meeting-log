"""זיהוי דוברים חוצה-הקלטות, לפי טביעת קול (embedding) מול פרופילים שמורים.

שני מקורות זיהוי, שניהם דרך אותו מנגנון התאמה/מיזוג (ראה firestore_store.py):

  - שיחת טלפון עם contact_name ודאי (אנשי קשר בטלפון) -> enroll_known:
    נכתב תמיד תחת השם הזה, בלי תלות בהתאמת קול - contact-list name סמכותי
    (ראה speakers.py / הכלל "השם מאנשי הקשר מדויק יותר מכל שם שיישמע
    באודיו"). זו ה"למידה": מעכשיו הקול הזה משויך לשם הזה גם בהקלטות אחרות.
  - כל דובר אחר (פגישה, או צד שני בשיחה בלי איש קשר שמור) -> resolve_or_enroll:
    מחפש התאמה מול הפרופילים הקיימים (מתויגים ולא); דובר לא-מזוהה חדש
    פותח פרופיל ללא שם, שממתין לתיוג במסך "דוברים לא מזוהים" (ראה main.py).

**"מהיום והלאה" בכוונה**: enroll/resolve לא נוגעים בהקלטות שכבר נשמרו -
הם רק מעדכנים פרופיל, וההקלטה הבאה שתעבור כאן תרוויח מהתיוג. תיוג פרופיל
במסך "דוברים לא מזוהים" לא מחפש ומתקן הקלטות ישנות (הוחלט במפורש 2026-08-15,
כדי לא לגרור עדכון מסמכי Drive היסטוריים).
"""

from app.config import settings
from app.models import TranscriptSegment
from app.pipeline import speaker_embedding
from app.services import firestore_store

# כמה קטעי הדיבור הארוכים ביותר של דובר משמשים לחישוב טביעת הקול שלו
# (ממוצע ביניהם) - ראה speaker_embedding.average_embedding. תקרה, לא
# רצפה: אם יש פחות קטעים תקינים, פשוט משתמשים במה שיש.
_MAX_SAMPLE_SEGMENTS = 3


def _merge(profile: dict, embedding: list[float]) -> tuple[list[float], int]:
    """ממצע embedding חדש לתוך מרכז-הכובד הקיים (ריצת ממוצע נע), כדי
    שהפרופיל ישתפר עם כל הקלטה נוספת במקום להישאר תקוע על הדגימה הראשונה."""
    count = profile.get("sample_count") or 1
    old = profile["embedding"]
    merged = [(o * count + n) / (count + 1) for o, n in zip(old, embedding)]
    return merged, count + 1


def _best_match(embedding: list[float], profiles: list[dict]) -> dict | None:
    """הפרופיל הכי דומה, אם דמיון-הקוסינוס שלו עובר את הסף המתאים - אחרת
    None. שני ספים נפרדים (ראה config.py): פרופיל עם שם צריך את הסף המחמיר
    (speaker_match_threshold_named) כי התאמה שגויה מציגה שם שגוי למשתמש;
    פרופיל בלי שם מסתפק בסף הרגיל, כי הטעות היחידה האפשרית שם היא צבירה
    לקבוצת "לא מזוהה" הלא-נכונה - לא מוצג שום שם, בלי נזק.
    נבדקים פרופילי-שם קודם: הצמדת שם ודאית עדיפה על צבירה אנונימית."""
    best_named, best_named_score = None, 0.0
    best_unnamed, best_unnamed_score = None, 0.0
    for profile in profiles:
        score = speaker_embedding.cosine_similarity(embedding, profile["embedding"])
        if profile.get("name"):
            if score > best_named_score:
                best_named, best_named_score = profile, score
        elif score > best_unnamed_score:
            best_unnamed, best_unnamed_score = profile, score

    if best_named is not None and best_named_score >= settings.speaker_match_threshold_named:
        return best_named
    if best_unnamed is not None and best_unnamed_score >= settings.speaker_match_threshold:
        return best_unnamed
    return None


def enroll_known(
    user_id: str,
    name: str,
    embedding: list[float],
    recording_id: str,
    channel: int,
    start_seconds: float,
) -> None:
    """מוסיף/מעדכן פרופיל תחת שם ודאי (contact_name משיחת טלפון). לא
    תלוי בהתאמת קול - השם כבר ידוע בוודאות (ראה pipeline.py:
    process_call_recording). פרופיל קיים באותו שם מתעדכן (מיזוג
    embedding); אחרת נפתח פרופיל חדש עם השם מההתחלה."""
    existing = firestore_store.find_speaker_profile_by_name(user_id, name)
    if existing is None:
        firestore_store.create_speaker_profile(
            user_id, embedding, recording_id, channel, start_seconds, name=name
        )
        return
    merged, count = _merge(existing, embedding)
    firestore_store.update_speaker_profile(
        existing["profile_id"],
        embedding=merged,
        sample_count=count,
        sample_recording_id=recording_id,
        sample_channel=channel,
        sample_start_seconds=start_seconds,
    )


def resolve_or_enroll(
    user_id: str,
    embedding: list[float],
    recording_id: str,
    channel: int,
    start_seconds: float,
) -> str | None:
    """מזהה קול מול הפרופילים הקיימים (מתויגים ולא-מתויגים כאחד), או פותח
    פרופיל חדש ללא שם. מחזיר את השם אם נמצאה התאמה לפרופיל מתויג; None אם
    אין התאמה כלל, או שההתאמה היא לפרופיל שעדיין לא תויג (הדובר עדיין
    "לא מזוהה", אבל אותו קול נצבר תחת אותו פרופיל אחד - ראה החלטת
    "מיקבוץ אחד" למסך "דוברים לא מזוהים")."""
    profiles = firestore_store.list_speaker_profiles(user_id)
    match = _best_match(embedding, profiles)

    if match is None:
        firestore_store.create_speaker_profile(
            user_id, embedding, recording_id, channel, start_seconds
        )
        return None

    merged, count = _merge(match, embedding)
    firestore_store.update_speaker_profile(
        match["profile_id"],
        embedding=merged,
        sample_count=count,
        sample_recording_id=recording_id,
        sample_channel=channel,
        sample_start_seconds=start_seconds,
    )
    return match.get("name")


def representative_embedding(
    audio_path: str, segments: list[TranscriptSegment]
) -> tuple[list[float] | None, TranscriptSegment | None]:
    """טביעת קול אחת שמייצגת דובר, מתוך הקטעים הארוכים ביותר שלו (ממוצע
    ביניהם - קטע קצר בודד עלול להישמע דומה לכל אחד). מחזיר גם את הקטע
    הארוך מביניהם, כמצביע השמעה לפרופיל (ראה firestore_store.create_speaker_profile).

    (None, None) אם אין אף קטע ארוך מספיק לטביעת קול אמינה - הדובר נשאר
    "דובר N"/"הצד השני" כרגיל, בלי ניסיון זיהוי הפעם."""
    if not segments:
        return None, None
    ranked = sorted(segments, key=lambda s: s.end_seconds - s.start_seconds, reverse=True)
    sample_segment = ranked[0]

    embeddings = []
    for segment in ranked[:_MAX_SAMPLE_SEGMENTS]:
        embedding = speaker_embedding.extract_embedding(
            audio_path, segment.start_seconds, segment.end_seconds
        )
        if embedding is not None:
            embeddings.append(embedding)

    if not embeddings:
        return None, None
    return speaker_embedding.average_embedding(embeddings), sample_segment


def identify_speakers(
    segments: list[TranscriptSegment], user_id: str, recording_id: str, audio_path: str
) -> list[TranscriptSegment]:
    """מזהה דוברי פגישה חוצי-הקלטות לפי קול, ומחליף את התווית הגנרית בשם
    שמור כשנמצאת התאמה. דובר שלא נמצאה לו התאמה נשאר "דובר N" כרגיל, אבל
    טביעת הקול שלו נשמרת כפרופיל חדש (ראה resolve_or_enroll) - כך שבפעם
    הבאה שהוא ידבר, בהקלטה כלשהי, הוא כבר ייצבר לאותו פרופיל.

    "אני" תמיד ודאי (ראה pipeline.py) ולא עובר כאן בכלל."""
    by_label: dict[str, list[TranscriptSegment]] = {}
    for segment in segments:
        if segment.speaker_label == "אני":
            continue
        by_label.setdefault(segment.speaker_label, []).append(segment)

    for label, label_segments in by_label.items():
        embedding, sample_segment = representative_embedding(audio_path, label_segments)
        if embedding is None:
            continue
        name = resolve_or_enroll(
            user_id, embedding, recording_id, channel=0,
            start_seconds=sample_segment.start_seconds,
        )
        if name:
            for segment in label_segments:
                segment.speaker_label = name

    return segments
