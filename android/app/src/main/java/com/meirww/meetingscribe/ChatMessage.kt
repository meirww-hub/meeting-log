package com.meirww.meetingscribe

/**
 * מקור לתשובה בצ'אט: מאיזו הקלטה, ובאיזה רגע בתוכה.
 *
 * [recordingId] ו-[startSeconds] הם מה שהופך את הציטוט לבר-ניגון בהקשה
 * אחת. שניהם יכולים להיות null - ציטוט שמצביע על קובץ מצורף אין לו מקום
 * בציר הזמן, וציטוט שהשרת לא הצליח לשייך להקלטה מסוימת עדיף שלא יהיה
 * לחיץ מאשר שיקפיץ את הנגן להקלטה הלא נכונה (ראה pipeline/chat.py).
 */
data class Citation(
    val recordingTitle: String,
    val timestamp: String,
    val quote: String,
    val recordingId: String? = null,
    val startSeconds: Double? = null,
)

data class ChatMessage(
    val isUser: Boolean,
    val text: String,
    val citations: List<Citation> = emptyList(),
)

/** "2:21" / "1:02:03" - הזמן הראשון שבמחרוזת, בשניות. */
val TIMESTAMP_PATTERN = Regex("""\d{1,3}:[0-5]\d(?::[0-5]\d)?""")

fun parseTimestampSeconds(text: String): Double? {
    val match = TIMESTAMP_PATTERN.find(text) ?: return null
    val parts = match.value.split(":").map { it.toInt() }
    return when (parts.size) {
        2 -> (parts[0] * 60 + parts[1]).toDouble()
        3 -> (parts[0] * 3600 + parts[1] * 60 + parts[2]).toDouble()
        else -> null
    }
}

/** זמן בתשובה נחשב לאותו רגע כמו ציטוט אם הם במרחק שנייה זה מזה. */
private const val SAME_MOMENT_SECONDS = 1.0

/**
 * לאיזו הקלטה שייך זמן שנכתב בגוף התשובה ("הדבר התרחש בין 2:21 ל-5:04").
 *
 * הזמן עצמו לא נושא את מקורו, ולכן מסיקים אותו: קודם מציטוט שמדבר על אותו
 * רגע, ואם אין כזה - מההקלטה היחידה שהתשובה מסתמכת עליה. כששתי הקלטות
 * מעורבות ואין התאמה מוחזר null, והמסך ישאל את המשתמש - עדיף שאלה מאשר נגן
 * שקופץ להקלטה הלא נכונה.
 */
fun ChatMessage.recordingIdForTime(seconds: Double): String? {
    val sameMoment = citations.firstOrNull {
        it.recordingId != null &&
            it.startSeconds != null &&
            kotlin.math.abs(it.startSeconds - seconds) <= SAME_MOMENT_SECONDS
    }
    if (sameMoment != null) return sameMoment.recordingId
    return citations.mapNotNull { it.recordingId }.distinct().singleOrNull()
}
