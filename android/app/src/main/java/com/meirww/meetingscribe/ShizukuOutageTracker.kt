package com.meirww.meetingscribe

/**
 * מתי להודיע ש-Shizuku לא זמין, ומתי להפסיק.
 *
 * חלץ מ-[ShizukuAccess] כדי שאפשר יהיה לבדוק בלי מכשיר: הסריקה מנסה שוב כל
 * כמה שעות כל עוד Shizuku למטה (ראה CallImportWorker), ובלי הכלל הזה כל
 * ניסיון כושל היה שולח התראה חדשה - ערימה שלמה על אותה תקלה אחת.
 */
object ShizukuOutageTracker {

    /** true אם יש להודיע עכשיו: הגישה לא זמינה, ועוד לא הודענו על ההפסקה הזו. */
    fun shouldNotify(available: Boolean, alreadyNotified: Boolean): Boolean =
        !available && !alreadyNotified

    /**
     * הערך הבא שיש לשמור בדגל "כבר הודענו", לפי תוצאת בדיקת הזמינות הנוכחית.
     * זמינות חוזרת מנקה את הדגל - כדי שהפסקה הבאה תודיע מחדש.
     */
    fun nextNotifiedFlag(available: Boolean): Boolean = !available
}
