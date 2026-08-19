"""זיהוי דוברים חוצה-הקלטות, לפי טביעת קול (embedding) מול פרופילים שמורים.

שני מקורות זיהוי, שניהם דרך אותו מנגנון התאמה/מיזוג (ראה firestore_store.py):

  - שיחת טלפון עם contact_name ודאי (אנשי קשר בטלפון) -> enroll_known:
    נכתב תמיד תחת השם הזה, בלי תלות בהתאמת קול - contact-list name סמכותי
    (ראה speakers.py / הכלל "השם מאנשי הקשר מדויק יותר מכל שם שיישמע
    באודיו"). זו ה"למידה": מעכשיו הקול הזה משויך לשם הזה גם בהקלטות אחרות.
  - כל דובר אחר (פגישה, או צד שני בשיחה בלי איש קשר שמור) -> resolve_or_enroll:
    מחפש התאמה מול הפרופילים הקיימים (מתויגים ולא); דובר לא-מזוהה חדש
    פותח פרופיל ללא שם, שממתין לתיוג במסך "פרופילי דוברים" (ראה main.py).

**"מהיום והלאה" בכוונה**: enroll/resolve לא נוגעים בהקלטות שכבר נשמרו -
הם רק מעדכנים פרופיל, וההקלטה הבאה שתעבור כאן תרוויח מהתיוג. תיוג פרופיל
במסך "פרופילי דוברים" לא מחפש ומתקן הקלטות ישנות (הוחלט במפורש 2026-08-15,
כדי לא לגרור עדכון מסמכי Drive היסטוריים). מה שכן חוזר אחורה הוא הכיוון
ההפוך: **תיקון ידני של שם דובר בהקלטה מלמד את הפרופיל** (ראה
recording_speaker_profiles ו-pipeline/edit.py) - זו העדות הכי אמינה שיש,
והיא הייתה נזרקת עד היום.

ההפרדה בין הדוברים בתוך ההקלטה עצמה כבר אומתה אקוסטית לפני שמגיעים לכאן
(ראה diarization.py), ולכן שתי תוויות שונות באותה הקלטה הן שני אנשים
שונים - עובדה שנאכפת כאן: תווית אחת בהקלטה לא יכולה "לגנוב" פרופיל שתווית
אחרת באותה הקלטה כבר תפסה.
"""

from app.config import settings
from app.models import TranscriptSegment
from app.pipeline import speaker_embedding
from app.pipeline.diarization import LabelVoice
from app.services import firestore_store

# כמה קטעי הדיבור הארוכים ביותר של דובר משמשים לחישוב טביעת הקול שלו
# (ממוצע ביניהם) - ראה speaker_embedding.average_embedding. תקרה, לא
# רצפה: אם יש פחות קטעים תקינים, פשוט משתמשים במה שיש.
_MAX_SAMPLE_SEGMENTS = 3

# תקרה למשקל של פרופיל ותיק במיזוג. בלעדיה פרופיל עם 40 דגימות כמעט לא זז
# יותר, וקול שהשתנה (הצטננות, מיקרופון אחר, שנתיים) נשאר תקוע על ממוצע
# היסטורי שכבר לא מייצג אותו. עם התקרה הפרופיל ממשיך להתעדכן לאט לנצח.
_MAX_MERGE_WEIGHT = 20


class SpeakerSample:
    """מצביע להשמעה: היכן בדיוק נשמע הדובר הזה, למסך פרופילי הדוברים.

    נשמר עם start **וגם** end, ולא רק start: בלי הסוף האפליקציה מנגנת
    מהנקודה הזו והלאה - כלומר את שאר ההקלטה, כולל כל שאר הדוברים - במקום
    את הקטע הנקי שאמור להשמיע את הקול שמתייגים.
    """

    __slots__ = ("recording_id", "channel", "start_seconds", "end_seconds")

    def __init__(
        self, recording_id: str, channel: int, start_seconds: float, end_seconds: float
    ):
        self.recording_id = recording_id
        self.channel = channel
        self.start_seconds = start_seconds
        self.end_seconds = end_seconds

    def as_fields(self) -> dict:
        return {
            "sample_recording_id": self.recording_id,
            "sample_channel": self.channel,
            "sample_start_seconds": self.start_seconds,
            "sample_end_seconds": self.end_seconds,
        }


def _merge(profile: dict, embedding: list[float]) -> tuple[list[float], int]:
    """ממצע embedding חדש לתוך מרכז-הכובד הקיים (ריצת ממוצע נע), כדי
    שהפרופיל ישתפר עם כל הקלטה נוספת במקום להישאר תקוע על הדגימה הראשונה."""
    count = profile.get("sample_count") or 1
    weight = min(count, _MAX_MERGE_WEIGHT)
    old = profile["embedding"]
    merged = [(o * weight + n) / (weight + 1) for o, n in zip(old, embedding)]
    return merged, count + 1


def _ranked(embedding: list[float], profiles: list[dict]) -> list[tuple[float, dict]]:
    scored = [
        (speaker_embedding.cosine_similarity(embedding, p["embedding"]), p)
        for p in profiles
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def _best_match(
    embedding: list[float], profiles: list[dict], taken: set[str] | None = None
) -> dict | None:
    """הפרופיל הכי דומה, אם דמיון-הקוסינוס שלו עובר את הסף המתאים - אחרת
    None. שני ספים נפרדים (ראה config.py): פרופיל עם שם צריך את הסף המחמיר
    (speaker_match_threshold_named) כי התאמה שגויה מציגה שם שגוי למשתמש;
    פרופיל בלי שם מסתפק בסף הרגיל, כי הטעות היחידה האפשרית שם היא צבירה
    לקבוצת "לא מזוהה" הלא-נכונה - לא מוצג שום שם, בלי נזק.
    נבדקים פרופילי-שם קודם: הצמדת שם ודאית עדיפה על צבירה אנונימית.

    **סף לבדו לא מספיק כשיש כמה שמות מועמדים.** שני פרופילים מתויגים
    ששניהם מעל הסף ובמרחק כמעט זהה הם הטלת מטבע בין שני שמות - וזה בדיוק
    מה שקרה כשקול של אישה זוהה כ"אמא" (2026-08-17): הסף עלה ל-0.82 אבל
    ההכרעה בין מועמדים קרובים נשארה "הגבוה ביותר מנצח". לכן נדרש גם מרווח
    (speaker_match_margin) מול המועמד הבא. אין מרווח - אין הצמדת שם, והדובר
    יורד למסלול הרגיל של פרופיל לא-מתויג לתיוג ידני.

    [taken] הם פרופילים שתווית אחרת **באותה הקלטה** כבר תפסה. ההפרדה בתוך
    ההקלטה אומתה אקוסטית (ראה diarization.py), ולכן שתי תוויות בה הן שני
    אנשים - ואי אפשר ששניהם יהיו אותו פרופיל. בלי החסימה הזו פגישה עם שני
    קולות דומים הייתה מציגה את אותו שם על שני דוברים שונים.
    """
    taken = taken or set()
    available = [p for p in profiles if p["profile_id"] not in taken]

    named = _ranked(embedding, [p for p in available if p.get("name")])
    if named and named[0][0] >= settings.speaker_match_threshold_named:
        runner_up = named[1][0] if len(named) > 1 else -1.0
        ambiguous = (
            runner_up >= settings.speaker_match_threshold_named
            and named[0][0] - runner_up < settings.speaker_match_margin
        )
        if not ambiguous:
            return named[0][1]

    unnamed = _ranked(embedding, [p for p in available if not p.get("name")])
    if unnamed and unnamed[0][0] >= settings.speaker_match_threshold:
        return unnamed[0][1]
    return None


def enroll_known(
    user_id: str,
    name: str,
    embedding: list[float],
    sample: SpeakerSample,
) -> str:
    """מוסיף/מעדכן פרופיל תחת שם ודאי (contact_name משיחת טלפון), ומחזיר את
    מזהה הפרופיל. לא תלוי בהתאמת קול - השם כבר ידוע בוודאות (ראה pipeline.py:
    process_call_recording). פרופיל קיים באותו שם מתעדכן (מיזוג embedding);
    אחרת נפתח פרופיל חדש עם השם מההתחלה."""
    existing = firestore_store.find_speaker_profile_by_name(user_id, name)
    if existing is None:
        return firestore_store.create_speaker_profile(
            user_id, embedding, sample.as_fields(),
            name=name, name_source=NAME_SOURCE_CONTACT,
        )
    merged, count = _merge(existing, embedding)
    firestore_store.update_speaker_profile(
        existing["profile_id"],
        embedding=merged,
        sample_count=count,
        name_source=NAME_SOURCE_CONTACT,
        **sample.as_fields(),
    )
    return existing["profile_id"]


def resolve_or_enroll(
    user_id: str,
    embedding: list[float],
    sample: SpeakerSample,
    profiles: list[dict] | None = None,
    taken: set[str] | None = None,
) -> tuple[str | None, str]:
    """מזהה קול מול הפרופילים הקיימים (מתויגים ולא-מתויגים כאחד), או פותח
    פרופיל חדש ללא שם. מחזיר (שם, מזהה פרופיל): השם הוא None כשאין התאמה
    כלל, או כשההתאמה היא לפרופיל שעדיין לא תויג (הדובר עדיין "לא מזוהה",
    אבל אותו קול נצבר תחת אותו פרופיל אחד - ראה החלטת "מיקבוץ אחד" למסך
    פרופילי הדוברים). מזהה הפרופיל מוחזר תמיד, וזה מה שמאפשר לתיקון ידני
    של השם בהקלטה ללמד את הפרופיל בדיעבד (ראה pipeline/edit.py).

    [profiles] הוא רשימת הפרופילים שכבר נטענה, כדי שכמה דוברים באותה הקלטה
    ייבדקו מול קריאה אחת ל-Firestore ולא אחת לכל דובר. הרשימה מתעדכנת
    במקום - פרופיל שנוצר לדובר אחד גלוי מיד לדוברים שאחריו - אחרת שתי
    תוויות של אותו קול היו פותחות שני פרופילים כפולים באותה הקלטה.
    """
    owned = profiles is None
    if owned:
        profiles = firestore_store.list_speaker_profiles(user_id)

    match = _best_match(embedding, profiles, taken)

    if match is None:
        profile_id = firestore_store.create_speaker_profile(
            user_id, embedding, sample.as_fields()
        )
        profiles.append(
            {
                "profile_id": profile_id,
                "user_id": user_id,
                "name": None,
                "embedding": embedding,
                "sample_count": 1,
            }
        )
        return None, profile_id

    merged, count = _merge(match, embedding)
    firestore_store.update_speaker_profile(
        match["profile_id"], embedding=merged, sample_count=count, **sample.as_fields()
    )
    match["embedding"], match["sample_count"] = merged, count
    return match.get("name"), match["profile_id"]


# מאיפה הגיע השם שעל הפרופיל, לפי סדר אמינות עולה. השדה נשמר כדי שמקור
# חלש לא ידרוס מקור חזק: שם שהמשתמש הקליד בעצמו הוא הסמכות העליונה, ואחריו
# שם מרשימת אנשי הקשר של הטלפון; ניחוש מתוך תוכן השיחה ("דנה, תכיני את
# הדוח") ממלא רק פרופיל שאין לו שם כלל.
NAME_SOURCE_AUDIO = "audio"
NAME_SOURCE_CONTACT = "contact"
NAME_SOURCE_MANUAL = "manual"


def learn_names_from_content(profile_ids: dict[str, str], renames: dict[str, str]) -> None:
    """מלמד פרופיל **חסר שם** את השם שנשמע בשיחה עצמה.

    עד היום פרופיל דובר יכול היה לקבל שם רק משתי דרכים: איש קשר בשיחת
    טלפון, או תיוג ידני. בפגישה רגילה - שבה אין אנשי קשר - כל דובר נשאר
    "לא מזוהה" לנצח, גם כשבשיחה עצמה קראו לו בשמו והתמלול כבר הציג את השם.
    זו הייתה למידה שנזרקה בכל הקלטה מחדש.

    רק על פרופיל שאין לו שם, ולעולם לא כדריסה: השם הזה הוא ניחוש של המודל
    מתוך התוכן (ראה speaker_names ב-summarize.py), חזק מספיק כדי למלא ריק
    וחלש מכדי לדרוס שם שהמשתמש או אנשי הקשר קבעו.
    """
    for name in renames.values():
        profile_id = profile_ids.get(name)
        if not profile_id:
            continue
        profile = firestore_store.get_speaker_profile(profile_id)
        if profile is None or profile.get("name"):
            continue
        firestore_store.update_speaker_profile(
            profile_id, name=name, name_source=NAME_SOURCE_AUDIO
        )


def learn_name_from_correction(profile_id: str, name: str) -> None:
    """קובע שם על פרופיל לפי תיקון ידני של המשתמש - הסמכות העליונה, דורסת
    כל שם קודם (ראה pipeline/edit.py ו-PATCH /speaker-profiles)."""
    firestore_store.update_speaker_profile(
        profile_id, name=name, name_source=NAME_SOURCE_MANUAL
    )


def representative_embedding(
    audio_path: str, segments: list[TranscriptSegment]
) -> tuple[list[float] | None, TranscriptSegment | None]:
    """טביעת קול אחת שמייצגת דובר, מתוך הקטעים הארוכים ביותר שלו (ממוצע
    ביניהם - קטע קצר בודד עלול להישמע דומה לכל אחד). מחזיר גם את הקטע
    הארוך מבין אלה **שנשמעים בפועל**, כמצביע השמעה לפרופיל.

    קטע שקט נזרק משתי הסיבות גם יחד: טביעת קול משקט היא רעש טהור שמזהם את
    הפרופיל, וכמצביע השמעה הוא בדיוק הבאג שהמשתמש דיווח עליו - לחיצה על
    "נגן" בפרופיל דובר שלא משמיעה כלום (ראה גם transcription.py,
    HallucinatedTranscriptError).

    (None, None) אם אין אף קטע ארוך ונשמע מספיק לטביעת קול אמינה - הדובר
    נשאר "דובר N"/"הצד השני" כרגיל, בלי ניסיון זיהוי הפעם.
    """
    if not segments:
        return None, None
    ranked = sorted(segments, key=lambda s: s.end_seconds - s.start_seconds, reverse=True)

    embeddings = []
    sample_segment = None
    for segment in ranked[:_MAX_SAMPLE_SEGMENTS]:
        embedding, silent = speaker_embedding.analyze_segment(
            audio_path, segment.start_seconds, segment.end_seconds
        )
        if embedding is None or silent:
            continue
        embeddings.append(embedding)
        if sample_segment is None:
            sample_segment = segment

    if not embeddings:
        return None, None
    return speaker_embedding.average_embedding(embeddings), sample_segment


def _label_voices(
    segments_by_label: dict[str, list[TranscriptSegment]], audio_path: str
) -> dict[str, LabelVoice]:
    """טביעת קול ודגימה לכל תווית, כשהאימות האקוסטי לא סיפק אותן מראש
    (הקלטה קצרה מדי למדידה - ראה diarization.refine_speaker_labels)."""
    voices: dict[str, LabelVoice] = {}
    for label, label_segments in segments_by_label.items():
        embedding, sample = representative_embedding(audio_path, label_segments)
        if embedding is not None and sample is not None:
            voices[label] = LabelVoice(embedding=embedding, sample=sample)
    return voices


def identify_speakers(
    segments: list[TranscriptSegment],
    user_id: str,
    recording_id: str,
    audio_path: str,
    label_voices: dict[str, LabelVoice] | None = None,
) -> dict[str, str]:
    """מזהה דוברי פגישה חוצי-הקלטות לפי קול, ומחליף את התווית הגנרית בשם
    שמור כשנמצאת התאמה. דובר שלא נמצאה לו התאמה נשאר "דובר N" כרגיל, אבל
    טביעת הקול שלו נשמרת כפרופיל חדש (ראה resolve_or_enroll) - כך שבפעם
    הבאה שהוא ידבר, בהקלטה כלשהי, הוא כבר ייצבר לאותו פרופיל.

    מחזיר מיפוי תווית **סופית** -> מזהה פרופיל, שנשמר על ההקלטה כדי שתיקון
    ידני של שם דובר ילמד את הפרופיל (ראה pipeline/edit.py).

    [label_voices] מגיע מהאימות האקוסטי, שכבר מדד את כל הקטעים ממילא - כך
    שאותם קטעים לא מפוענחים ולא עוברים במודל פעם שנייה.

    הדוברים נבדקים לפי כמות הדיבור שלהם, מהמדבר ביותר ומטה: לדובר עם יותר
    חומר יש טביעת קול אמינה יותר, ולכן זכות הראשונים על פרופיל שגם דובר
    אחר בהקלטה דומה לו (ראה _best_match / taken).

    "מאיר" (ערוץ ה-uplink בשיחת טלפון) תמיד ודאי (ראה pipeline.py) ולא עובר
    כאן בכלל.
    """
    by_label: dict[str, list[TranscriptSegment]] = {}
    for segment in segments:
        if segment.speaker_label == "מאיר":
            continue
        by_label.setdefault(segment.speaker_label, []).append(segment)

    voices = label_voices if label_voices is not None else _label_voices(by_label, audio_path)
    if not voices:
        return {}

    def speech_seconds(label: str) -> float:
        return sum(s.end_seconds - s.start_seconds for s in by_label.get(label, []))

    profiles = firestore_store.list_speaker_profiles(user_id)
    taken: set[str] = set()
    renames: dict[str, str] = {}
    profile_ids: dict[str, str] = {}

    for label in sorted(voices, key=speech_seconds, reverse=True):
        if label not in by_label:
            continue
        voice = voices[label]
        sample = SpeakerSample(
            recording_id=recording_id,
            channel=0,
            start_seconds=voice.sample.start_seconds,
            end_seconds=voice.sample.end_seconds,
        )
        name, profile_id = resolve_or_enroll(
            user_id, voice.embedding, sample, profiles=profiles, taken=taken
        )
        taken.add(profile_id)
        # שם שכבר הודבק לדובר אחר בהקלטה הזו לא יודבק שוב: זו הייתה אותה
        # תקלה כמו פרופיל תפוס, רק דרך שני פרופילים נפרדים שנושאים במקרה
        # את אותו שם.
        if name and name not in renames.values():
            renames[label] = name
            profile_ids[name] = profile_id
        else:
            profile_ids[label] = profile_id

    for segment in segments:
        new_label = renames.get(segment.speaker_label)
        if new_label:
            segment.speaker_label = new_label

    return profile_ids
