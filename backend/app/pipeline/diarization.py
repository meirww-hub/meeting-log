"""אימות אקוסטי של הפרדת הדוברים שהתמלול החזיר, לפני שמשייכים לה שמות.

התמלול מגיע מ-Gemini, שמחלק את הקטעים לדוברים לפי שמיעה ומחזיר speaker_tag
לכל קטע (ראה transcription.py). זה הניחוש היחיד בצנרת שאיש לא בדק אחריו,
והוא טועה בשתי צורות הפוכות שנראות למשתמש בדיוק אותו דבר - "השיוך לא נכון":

  • **פיצול**: אותו אדם מקבל שתי תוויות ("דובר 1" בתחילת הפגישה, "דובר 4"
    אחריה). קורה בעיקר סביב בקשות ההמשך בהקלטה ארוכה, שבהן המספור נקבע
    מחדש (ראה _RESUME_SPEAKER_RULE ב-transcription.py): מנקודה כזו **כל**
    יתרת התמלול מיוחסת לאדם הלא נכון.
  • **מיזוג**: שני אנשים נדחסים לתווית אחת, ואז הסיכום מייחס לאחד מהם
    אמירות של השני.

כאן זה נבדק מול האודיו עצמו: לכל קטע מחושבת טביעת קול (אותו מודל wespeaker
של speaker_embedding.py), הקטעים מקובצים לפי דמיון-קוסינוס, ותוויות התמלול
נקבעות מחדש לפי האשכולות. המדידה האקוסטית מנצחת את הניחוש מתוך התוכן.

**מה שלא ניתן לקבוע נשאר מסומן ולא מנוחש.** קטע שיושב בין שני אשכולות
(דמיון קרוב לשניים) מקבל speaker_confident=False, וממנו נגזרים סימון "(?)"
בתמלול והוראה לסיכום לכתוב "אחד הדוברים" במקום שם (ראה summarize.py). זו
דרישה מפורשת של המשתמש: ייחוס עמום עדיף על ייחוס שגוי.

ההפרדה אינה מוחלפת אלא מתוקנת - מיזוג ופיצול נעשים רק כשהמדידה חד-משמעית
(הספים ב-config.py), ואחרת החלוקה שהתמלול החזיר נשארת כמות שהיא. שיחת טלפון
לא עוברת כאן כלל: שם ההפרדה פיזית (ערוץ לכל צד) וודאית מלכתחילה, וניחוש
אקוסטי יכול רק לקלקל אותה (ראה pipeline.process_call_recording).
"""

from dataclasses import dataclass, field

import numpy as np

from app.config import settings
from app.models import TranscriptSegment
from app.pipeline import speaker_embedding

# קטע קצר מזה לא נמדד: טביעת קול על פחות מזה רועשת מדי מכדי להסיק ממנה
# זהות, והיא בדיוק מה שגורם ל"מתאים בטעות לכל אחד". גבוה מ-_MIN_SECONDS של
# speaker_embedding (1.0) בכוונה - שם זו מגבלת המודל, כאן זו דרישת איכות:
# אשכול שנבנה מקטעי סף הוא אשכול רועש.
_MIN_EMBED_SECONDS = 1.5

# תקרת מספר הקטעים שנמדדים בפועל, מהארוך לקצר. כל מדידה היא תהליך ffmpeg +
# מעבר במודל, ובהקלטה של שעה יש מאות קטעים; התקרה חוסמת את זמן העיבוד בלי
# לפגוע באיכות - האשכולות מתייצבים הרבה לפני 240 דגימות.
_MAX_EMBEDDED_SEGMENTS = 240

# כמה סבבי k-means (בקוסינוס) לרוץ. מתכנס כמעט תמיד ב-2-3 סבבים בסדר הגודל
# הזה; התקרה רק חוסמת ריצה שמתנדנדת בין שתי חלוקות שקולות.
_KMEANS_ROUNDS = 6

# כמה חברים לפחות צריך כל צד בפיצול תווית אחת לשתיים. פיצול שמוציא קטע בודד
# הצידה הוא כמעט תמיד קטע חריג (רעש, דיבור חופף) ולא אדם שני.
_MIN_SPLIT_MEMBERS = 3


@dataclass
class LabelVoice:
    """טביעת הקול המסכמת של תווית דובר אחת בהקלטה, ודגימה נשמעת שלה.

    נבנית כאן ממילא (כל הקטעים כבר נמדדו) ומועברת הלאה ל-speaker_id.py
    במקום שיחשב אותה שוב - חישוב חוזר היה מריץ את ffmpeg ואת המודל פעם
    נוספת על אותם קטעים בדיוק.
    """

    embedding: list[float]
    sample: TranscriptSegment


@dataclass
class DiarizationResult:
    segments: list[TranscriptSegment]
    # None כשלא הייתה מדידה אקוסטית בכלל (אין די קטעים ארוכים) - אז
    # speaker_id יחשב טביעות בעצמו, בדיוק כמו קודם.
    label_voices: dict[str, LabelVoice] | None = None
    stats: dict = field(default_factory=dict)


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector if norm == 0.0 else vector / norm


def _unit_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


def _measure(
    segments: list[TranscriptSegment], audio_path: str
) -> tuple[list[int], np.ndarray]:
    """מודד את הקטעים הארוכים ביותר. מחזיר (אינדקסי קטעים בסדר עולה, מטריצת
    טביעות מנורמלת) - שורה i במטריצה שייכת ל-segments[indices[i]].

    קטע ששקט בפועל נזרק גם כשיש לו טקסט: טביעת קול משקט היא רעש טהור, והיא
    זו שפתחה בעבר פרופיל דובר שלא השמיע כלום (ראה transcription.py,
    HallucinatedTranscriptError). מדידה אחת מחזירה את שתי התשובות
    (speaker_embedding.analyze_segment) כדי לא לפענח כל קטע פעמיים.
    """
    longest = sorted(
        (
            i
            for i, s in enumerate(segments)
            if s.end_seconds - s.start_seconds >= _MIN_EMBED_SECONDS
        ),
        key=lambda i: segments[i].end_seconds - segments[i].start_seconds,
        reverse=True,
    )[:_MAX_EMBEDDED_SEGMENTS]

    indices: list[int] = []
    vectors: list[list[float]] = []
    for i in sorted(longest):
        segment = segments[i]
        embedding, silent = speaker_embedding.analyze_segment(
            audio_path, segment.start_seconds, segment.end_seconds
        )
        if embedding is None or silent:
            continue
        indices.append(i)
        vectors.append(embedding)

    if not vectors:
        return [], np.zeros((0, 0))
    return indices, _unit_rows(np.array(vectors, dtype=np.float64))


def _centroids(clusters: list[list[int]], vectors: np.ndarray) -> np.ndarray:
    return np.array([_unit(vectors[members].mean(axis=0)) for members in clusters])


def _two_means(vectors: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """מפצל קבוצת טביעות לשתיים (k-means בקוסינוס), או None אם צד אחד התרוקן.

    הזרעים הם שתי הטביעות הרחוקות ביותר זו מזו ולא זרע אקראי: התוצאה חייבת
    להיות דטרמיניסטית (אותה הקלטה, אותה חלוקה, גם בריצה חוזרת אחרי כישלון),
    ושני הקצוות הם גם הניחוש הטבעי ל"שני אנשים" אם באמת יש כאן שניים.
    """
    similarity = vectors @ vectors.T
    first, second = np.unravel_index(np.argmin(similarity), similarity.shape)
    centers = vectors[[first, second]]

    labels = np.zeros(len(vectors), dtype=int)
    for _ in range(_KMEANS_ROUNDS):
        labels = np.argmax(vectors @ centers.T, axis=1)
        updated = []
        for side in (0, 1):
            members = vectors[labels == side]
            if len(members) == 0:
                return None
            updated.append(_unit(members.mean(axis=0)))
        centers = np.array(updated)
    return labels, centers


def _split_mixed(clusters: list[list[int]], vectors: np.ndarray) -> list[list[int]]:
    """מפצל תווית שמכילה בבירור שני אנשים (מיזוג שגוי של התמלול).

    הפיצול מתקבל רק כששני המרכזים שנוצרו רחוקים באמת - מתחת ל-
    speaker_split_similarity - ושני הצדדים גדולים מספיק. אשכול של אדם אחד
    תמיד *אפשר* לפצל לשניים; מה שמבדיל פיצול אמיתי הוא שהמרחק בין החצאים
    גדול כמו בין שני אנשים, ולא כמו בין שני משפטים של אותו אדם.
    """
    result: list[list[int]] = []
    for members in clusters:
        split = (
            _two_means(vectors[members])
            if len(members) >= 2 * _MIN_SPLIT_MEMBERS
            else None
        )
        if split is None:
            result.append(members)
            continue

        labels, centers = split
        left = [members[i] for i in range(len(members)) if labels[i] == 0]
        right = [members[i] for i in range(len(members)) if labels[i] == 1]
        separated = float(centers[0] @ centers[1]) < settings.speaker_split_similarity
        if separated and min(len(left), len(right)) >= _MIN_SPLIT_MEMBERS:
            result.extend([left, right])
        else:
            result.append(members)
    return result


def _merge_duplicates(clusters: list[list[int]], vectors: np.ndarray) -> list[list[int]]:
    """מאחד תוויות שהן בבירור אותו אדם (פיצול שגוי של התמלול), מהזוג הדומה
    ביותר ומטה, עד שאין יותר זוג מעל speaker_merge_similarity."""
    clusters = [list(members) for members in clusters]
    while len(clusters) > 1:
        centers = _centroids(clusters, vectors)
        similarity = centers @ centers.T
        np.fill_diagonal(similarity, -1.0)
        first, second = np.unravel_index(np.argmax(similarity), similarity.shape)
        if float(similarity[first, second]) < settings.speaker_merge_similarity:
            break
        low, high = sorted((int(first), int(second)))
        clusters[low] = sorted(clusters[low] + clusters[high])
        clusters.pop(high)
    return clusters


def _settle(clusters: list[list[int]], vectors: np.ndarray) -> list[list[int]]:
    """משייך כל קטע מחדש למרכז הקרוב לו ביותר ומעדכן את המרכזים, כמה סבבים.

    זה מה שמתקן קטעים בודדים שהתמלול שייך לדובר הלא נכון בתוך תווית שאחרת
    נכונה: המיזוג והפיצול פועלים על תוויות שלמות, וכאן כל קטע נשפט לגופו.
    """
    for _ in range(_KMEANS_ROUNDS):
        centers = _centroids(clusters, vectors)
        assignment = np.argmax(vectors @ centers.T, axis=1)
        updated: list[list[int]] = [[] for _ in clusters]
        for position in range(len(vectors)):
            updated[int(assignment[position])].append(position)
        updated = [members for members in updated if members]
        if updated == clusters:
            break
        clusters = updated
    return clusters


def _confidence(vectors: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """True לכל קטע שהשיוך שלו חד-משמעי.

    שני תנאים, שניהם נחוצים: דמיון מוחלט סביר למרכז שנבחר (קטע שלא דומה
    לאיש - דיבור חופף, רעש - הוא ניחוש בכל מקרה), ומרווח מספיק מול המרכז
    השני הקרוב ביותר (קטע שיושב בדיוק בין שני אנשים הוא בדיוק המקום שבו
    ייחוס שגוי נולד). כשיש אשכול אחד בלבד אין במי להתבלבל.
    """
    similarity = vectors @ centers.T
    best = similarity.max(axis=1)
    if centers.shape[0] < 2:
        return best >= settings.speaker_assign_min_similarity
    ordered = np.sort(similarity, axis=1)
    margin = ordered[:, -1] - ordered[:, -2]
    return (best >= settings.speaker_assign_min_similarity) & (
        margin >= settings.speaker_assign_min_margin
    )


def _relabel(
    segments: list[TranscriptSegment],
    indices: list[int],
    clusters: list[list[int]],
    confident: np.ndarray,
    vectors: np.ndarray,
) -> dict[str, LabelVoice]:
    """כותב את התוויות החדשות על הקטעים ומחזיר טביעת קול לכל תווית סופית.

    התוויות ניתנות לפי **סדר הופעה ראשונה** בהקלטה, כי זה החוזה של מסך
    "עריכת דוברים" (ראה speakers.speakers_in_order): המשתמש ממלא שם לפי מי
    שדיבר ראשון.

    קטע שלא נמדד (קצר מדי / שקט) יורש את האשכול של התווית המקורית שלו, לפי
    רוב הקטעים שכן נמדדו בה - החלוקה של התמלול היא עדיין המידע הטוב ביותר
    שיש עליו. הוא נחשב ודאי רק אם אותה תווית הצביעה **פה אחד** על אשכול
    אחד; מרגע שהיא התפצלה, אין דרך לדעת לאיזה צד הקטע הקצר שייך.
    """
    original_labels = [s.speaker_label for s in segments]
    cluster_of_position = {
        position: cluster
        for cluster, members in enumerate(clusters)
        for position in members
    }

    # סדר ההופעה הראשונה: indices עולה, ולכן מספיק לעבור עליו כסדרו.
    order: list[int] = []
    for position in range(len(indices)):
        cluster = cluster_of_position[position]
        if cluster not in order:
            order.append(cluster)
    names = {cluster: f"דובר {rank + 1}" for rank, cluster in enumerate(order)}

    votes: dict[str, dict[int, int]] = {}
    for position, segment_index in enumerate(indices):
        tally = votes.setdefault(original_labels[segment_index], {})
        cluster = cluster_of_position[position]
        tally[cluster] = tally.get(cluster, 0) + 1
    inherited = {
        label: (max(tally, key=lambda c: tally[c]), len(tally) == 1)
        for label, tally in votes.items()
    }

    # תווית שאף קטע שלה לא נמדד (כולה קצרה או שקטה) לא נשארת עם המספר
    # שהתמלול נתן לה: המספור נקבע כאן מחדש, ו"דובר 2" של התמלול עלול להיות
    # בדיוק המספר שניתן זה עתה לאדם אחר - כלומר שני אנשים שונים תחת אותה
    # תווית. במקום זה היא מקבלת מספר פנוי משלה, אחרי המדודים, ומסומנת
    # לא-ודאית: היא נשארת דובר נפרד, אבל בלי טענה מי הוא.
    unmeasured: dict[str, str] = {}
    position_of_segment = {segment_index: p for p, segment_index in enumerate(indices)}
    for segment_index, segment in enumerate(segments):
        position = position_of_segment.get(segment_index)
        if position is not None:
            cluster = cluster_of_position[position]
            segment.speaker_confident = bool(confident[position])
        else:
            label = original_labels[segment_index]
            fallback = inherited.get(label)
            if fallback is None:
                if label not in unmeasured:
                    unmeasured[label] = len(names) + len(unmeasured) + 1
                segment.speaker_label = f"דובר {unmeasured[label]}"
                # התג נשמר עקבי עם התווית גם כאן, כדי ששני דוברים שונים לא
                # יישבו על אותו מספר בתוך הרשומה השמורה.
                segment.speaker_tag = unmeasured[label]
                segment.speaker_confident = False
                continue
            cluster, unanimous = fallback
            segment.speaker_confident = unanimous
        segment.speaker_label = names[cluster]
        segment.speaker_tag = cluster + 1

    voices: dict[str, LabelVoice] = {}
    for cluster, members in enumerate(clusters):
        # הדגימה להשמעה היא הקטע הארוך ביותר באשכול. כבר ידוע שהוא נשמע
        # (קטעים שקטים נזרקו במדידה) - וזה בדיוק מה שנשמר כמצביע ההשמעה של
        # פרופיל הדובר, שבעבר הצביע לפעמים לרגע שקט ולא השמיע כלום.
        longest = max(
            (indices[position] for position in members),
            key=lambda i: segments[i].end_seconds - segments[i].start_seconds,
        )
        voices[names[cluster]] = LabelVoice(
            embedding=_unit(vectors[members].mean(axis=0)).tolist(),
            sample=segments[longest],
        )
    return voices


def refine_speaker_labels(
    segments: list[TranscriptSegment], audio_path: str
) -> DiarizationResult:
    """מאמת ומתקן את הפרדת הדוברים מול האודיו. ראה תיאור המודול."""
    if not segments:
        return DiarizationResult(segments=segments)

    indices, vectors = _measure(segments, audio_path)
    if len(indices) < 2:
        # אין די חומר נמדד כדי להסיק משהו. החלוקה של התמלול נשארת בדיוק כפי
        # שהיא - וגם לא מסומנת כלא-ודאית, כי אין ממצא שסותר אותה; סימון
        # גורף היה מרוקן את הסימון ממשמעות בדיוק כשהוא נחוץ.
        return DiarizationResult(segments=segments)

    by_label: dict[str, list[int]] = {}
    for position, segment_index in enumerate(indices):
        by_label.setdefault(segments[segment_index].speaker_label, []).append(position)

    clusters = _split_mixed(list(by_label.values()), vectors)
    clusters = _merge_duplicates(clusters, vectors)
    clusters = [sorted(members) for members in _settle(clusters, vectors)]

    confident = _confidence(vectors, _centroids(clusters, vectors))
    voices = _relabel(segments, indices, clusters, confident, vectors)

    return DiarizationResult(
        segments=segments,
        label_voices=voices,
        stats={
            "measured_segments": len(indices),
            "labels_before": len(by_label),
            "labels_after": len(clusters),
            "uncertain_segments": sum(1 for s in segments if not s.speaker_confident),
        },
    )
