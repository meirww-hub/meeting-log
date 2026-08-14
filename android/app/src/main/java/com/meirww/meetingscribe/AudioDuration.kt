package com.meirww.meetingscribe

import android.media.MediaMetadataRetriever
import android.util.Log
import java.io.File

/**
 * מדיניות האורך המזערי לעיבוד, ומדידת אורך של קובץ אודיו.
 *
 * הקלטה קצרה מ-[MIN_PROCESSING_SECONDS] לא נשלחת לשרת בכלל: שיחת "אני בדרך"
 * של חצי דקה לא מייצרת סיכום או משימות בעלי ערך, אבל כן עולה תמלול, סיכום
 * ותיקייה בדרייב, ומציפה את מסך ההיסטוריה. הסינון נעשה על המכשיר ולא בשרת
 * כדי לחסוך גם את ההעלאה עצמה.
 *
 * הסף נבדק בכל שלוש הדרכים שמזינות את העיבוד - הקלטת פגישה
 * ([RecordingRecovery]), ייבוא שיחה מ-cally ([CallImportWorker]) ושיתוף קובץ
 * מבחוץ ([ShareReceiveActivity]) - ותמיד על ההקלטה בשלמותה, לא על קובץ בודד:
 * פגישה ארוכה מתפצלת לכמה קבצי העלאה (ראה RecordingRecovery.splitBySize),
 * וחלק אחרון בן 20 שניות הוא סופה של פגישה שלמה ולא הקלטה קצרה.
 */
object AudioDuration {

    private const val TAG = "AudioDuration"

    /** אורך מזערי לעיבוד. הקלטה קצרה מזה נמחקת מהמכשיר ולא נשלחת. */
    const val MIN_PROCESSING_SECONDS = 180.0

    /** אותו סף בדקות, לטקסט שהמשתמש רואה - כדי שלא ייפרד מהמספר האמיתי. */
    val MIN_PROCESSING_MINUTES: Int = (MIN_PROCESSING_SECONDS / 60).toInt()

    /** אורך הקובץ בשניות, או null אם אי אפשר לקרוא אותו (קובץ פגום/חסר). */
    fun seconds(file: File): Double? {
        if (!file.isFile || file.length() == 0L) return null

        val retriever = MediaMetadataRetriever()
        return try {
            retriever.setDataSource(file.absolutePath)
            retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toLongOrNull()
                ?.let { it / 1000.0 }
        } catch (e: Exception) {
            Log.w(TAG, "seconds: cannot read duration of ${file.name}", e)
            null
        } finally {
            runCatching { retriever.release() }
        }
    }

    /**
     * אורך כולל של קבצים שמתנגנים ברצף (חלקיה של אותה הקלטה). מחזיר null אם
     * אורכו של ולו קובץ אחד לא ניתן לקריאה - סכום חלקי היה נמוך מהאמת ועלול
     * להוביל לזריקת הקלטה ארוכה.
     */
    fun totalSeconds(files: List<File>): Double? {
        if (files.isEmpty()) return null
        var total = 0.0
        for (file in files) {
            total += seconds(file) ?: return null
        }
        return total
    }

    /**
     * אורך של קבצים שמתנגנים במקביל (שני ערוצי שיחת טלפון) - כלומר האורך של
     * הארוך שבהם, לא הסכום. מחזיר null אם אף קובץ לא ניתן לקריאה.
     */
    fun longestSeconds(files: List<File>): Double? = files.mapNotNull { seconds(it) }.maxOrNull()

    /**
     * true רק כשידוע בוודאות שההקלטה קצרה מהסף. אורך לא ידוע (null) נחשב
     * בכוונה "ארוך מספיק": עדיף עיבוד מיותר של הקלטה אחת על פני אודיו שנמחק
     * בשקט בגלל מדידה שנכשלה.
     */
    fun isShorterThanMinimum(seconds: Double?): Boolean =
        seconds != null && seconds < MIN_PROCESSING_SECONDS
}
