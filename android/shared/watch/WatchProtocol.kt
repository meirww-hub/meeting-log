package com.meirww.meetingscribe.watch

/**
 * הפרוטוקול בין השעון לטלפון. הקובץ הזה משותף לשני המודולים (:app ו-:wear) -
 * הוא מקור האמת היחיד, כך ששני הצדדים לא יכולים להיסחף זה מזה.
 *
 * השעון הוא שלט בלבד: הוא שולח "התחל" / "עצור" / "מה המצב", והטלפון (שהוא
 * היחיד שמקליט) משדר בחזרה את המצב האמיתי. השעון מציג "מקליט" רק אחרי
 * שהטלפון אישר שהקלטה באמת רצה - לא כשנלחץ הכפתור.
 */
object WatchProtocol {

    // שעון -> טלפון
    const val PATH_START = "/meetinglog/start"
    const val PATH_STOP = "/meetinglog/stop"
    const val PATH_QUERY = "/meetinglog/query"

    // טלפון -> שעון
    const val PATH_STATUS = "/meetinglog/status"

    /** קידומת משותפת, לסינון ב-intent-filter של המאזינים. */
    const val PATH_PREFIX = "/meetinglog"

    private const val VERSION = "v1"
    private const val SEPARATOR = '|'

    fun encode(status: WatchStatus): ByteArray =
        listOf(
            VERSION,
            status.state.name,
            status.elapsedMs.toString(),
            status.pendingCount.toString(),
            status.reason.name,
        ).joinToString(SEPARATOR.toString()).toByteArray(Charsets.UTF_8)

    /**
     * null על כל מטען שאיננו מובן (גרסה אחרת, שדה חסר, ערך לא מוכר). השעון
     * מתעלם ממנו ולא מנחש - הודעה פגומה לא אמורה להציג "מקליט" בטעות.
     */
    fun decode(bytes: ByteArray?): WatchStatus? {
        if (bytes == null) return null
        val parts = String(bytes, Charsets.UTF_8).split(SEPARATOR)
        if (parts.size != 5 || parts[0] != VERSION) return null

        val state = WatchState.entries.firstOrNull { it.name == parts[1] } ?: return null
        val elapsed = parts[2].toLongOrNull() ?: return null
        val pending = parts[3].toIntOrNull() ?: return null
        val reason = FailReason.entries.firstOrNull { it.name == parts[4] } ?: return null

        return WatchStatus(
            state = state,
            elapsedMs = elapsed.coerceAtLeast(0L),
            pendingCount = pending.coerceAtLeast(0),
            reason = reason,
        )
    }
}

enum class WatchState {
    /** אין הקלטה פעילה. */
    IDLE,

    /** הקלטה פעילה והמיקרופון קולט. */
    RECORDING,

    /** הקלטה פעילה, אבל המיקרופון תפוס בשיחה - היא תתחדש לבד. */
    WAITING_MIC,

    /** בקשת ההתחלה נכשלה; [FailReason] אומר למה. */
    FAILED,
}

enum class FailReason {
    NONE,

    /** האפליקציה בטלפון עדיין לא קיבלה הרשאת מיקרופון. */
    NO_MIC_PERMISSION,

    /** חסרה הרשאת "הצגה מעל אפליקציות אחרות", שבלעדיה אי אפשר להתחיל מרחוק. */
    NO_OVERLAY_PERMISSION,

    /** הטלפון ניסה להתחיל, ולא הצליח לפתוח את ההקלטה בזמן. */
    START_TIMEOUT,

    /** ההקלטה נפתחה ונכשלה מיד (למשל אין מקום באחסון). */
    START_FAILED,
}

/**
 * [elapsedMs] הוא משך ההקלטה *ברגע השידור* ולא שעת התחלה: שעון ושעון קיר של
 * שני מכשירים יכולים לסטות, ומשך אליו מוסיפים את הזמן שחלף מאז הקבלה לא תלוי
 * בשעון של אף אחד מהם.
 */
data class WatchStatus(
    val state: WatchState,
    val elapsedMs: Long = 0L,
    val pendingCount: Int = 0,
    val reason: FailReason = FailReason.NONE,
)
