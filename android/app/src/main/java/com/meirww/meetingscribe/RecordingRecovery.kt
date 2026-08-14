package com.meirww.meetingscribe

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import java.io.File
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * מבטיח שכל הקלטת פגישה מגיעה לשרת, גם אם האפליקציה נסגרה/קרסה/נהרגה
 * באמצע.
 *
 * הדיסק הוא הרישום: כל הקלטה חיה בתיקיית `session_<חותמת זמן>` תחת
 * `recordings`, והתיקייה נמחקת רק אחרי שהשרת אישר קליטה. כל תיקייה ששרדה
 * היא בהגדרה הקלטה שלא הגיעה ליעדה, ולכן [RecordingRecoveryWorker] סורק
 * ומשלים אותה - בפתיחת האפליקציה, בסיום הקלטה, וגם כשהתהליך מתעורר לאירוע
 * אחר לגמרי (למשל סיום שיחה).
 *
 * זה מחליף את המצב הקודם, שבו הקובץ האחרון נשמר רק בשדה סטטי בזיכרון
 * (`lastOutputFile`): מספיק היה שהמערכת תהרוג את התהליך - מה שקורה בדיוק
 * כשהמשתמש עונה לשיחה ויוצא מהאפליקציה - כדי שההקלטה תישאר על הדיסק בלי שום
 * דרך להגיע אליה מהממשק.
 */
object RecordingRecovery {

    private const val TAG = "RecordingRecovery"

    const val RECORDINGS_DIR = "recordings"
    const val SESSION_PREFIX = "session_"

    /** הקבצים המאוחדים שנשלחים לשרת, בתוך תיקיית ה-session. */
    private const val MERGED_PREFIX = "meeting_"
    private const val MERGED_SUFFIX = ".m4a"

    /**
     * גג לגודל העלאה בודדת. Cloud Run דוחה גוף בקשת HTTP/1 מעל 32MiB ב-413,
     * וזו הייתה הסיבה האמיתית שפגישות ארוכות לא הגיעו למערכת: UploadWorker
     * תרגם 413 ל-Result.retry(), כך ש-WorkManager ניסה שוב לנצח בשקט מוחלט.
     * המגבלה היא של תשתית גוגל ולא של השרת שלנו - אי אפשר להרים אותה, ולכן
     * פגישה חורגת נחתכת לכמה העלאות על גבול קטע (ראה [splitBySize]).
     *
     * 30MiB ולא 32 - משאיר מרווח למעטפת ה-multipart ולשדות הטופס.
     */
    private const val MAX_UPLOAD_BYTES = 30L * 1024 * 1024

    /** כותרת שהמשתמש הקליד, נשמרת לדיסק כדי לשרוד הריגת תהליך. */
    private const val TITLE_NAME = "title.txt"

    private const val UNIQUE_WORK_NAME = "recording_recovery"

    /**
     * ה-session שמוקלט ברגע זה - סריקת ההשלמה מדלגת עליו.
     *
     * הסימון נעשה ברגע שהתיקייה נוצרת, לפני שהמיקרופון בכלל נפתח: אחרת
     * נשאר חלון של כמה עשרות מילישניות שבו התיקייה כבר על הדיסק אבל עוד לא
     * מסומנת, וסריקה שרצה בדיוק אז הייתה מאחדת ומעלה הקלטה חיה - ואז מוחקת
     * את התיקייה מתחת לרגליים של המקליט.
     */
    @Volatile
    private var activeSessionDir: File? = null

    fun sessionsRoot(context: Context): File? =
        context.getExternalFilesDir(RECORDINGS_DIR)

    internal fun markActive(sessionDir: File?) {
        activeSessionDir = sessionDir
    }

    /** שומר את הכותרת שהמשתמש הקליד לצד הקטעים, לשימוש בזמן ההעלאה. */
    fun saveTitle(sessionDir: File, title: String) {
        if (title.isBlank()) return
        runCatching { File(sessionDir, TITLE_NAME).writeText(title.trim()) }
            .onFailure { Log.w(TAG, "saveTitle failed for ${sessionDir.name}", it) }
    }

    /**
     * מפעיל סריקה והשלמה של כל ההקלטות התקועות. בטוח לקרוא לו בכל רגע.
     *
     * [delayMs] משמש כשצריך לחזור ולבדוק הקלטה שמוגנת כרגע כ"חיה" - ראה
     * [RecordingRecoveryWorker].
     */
    fun sweep(context: Context, delayMs: Long = 0L) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            UNIQUE_WORK_NAME,
            ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<RecordingRecoveryWorker>()
                .setInitialDelay(delayMs, TimeUnit.MILLISECONDS)
                .build(),
        )
    }

    /** כמה הקלטות ממתינות לשליחה כרגע (לא כולל זו שמוקלטת ברגע זה). */
    fun pendingCount(context: Context): Int = strandedSessions(context).size

    internal fun strandedSessions(context: Context): List<File> {
        val root = sessionsRoot(context) ?: return emptyList()
        // שתי שמירות: זו שבזיכרון מגנה על ההקלטה של התהליך הנוכחי, וזו שעל
        // הדיסק מגנה גם על הקלטה שהתהליך שלה נהרג ועומד לחזור אליה
        // (START_STICKY). בלי השנייה, הסריקה שרצה בעליית התהליך המחודשת הייתה
        // חוטפת את הפגישה באמצע ושולחת אותה לעיבוד - וההמשך שלה היה נרשם
        // כהקלטה שנייה, עם תמלול וסיכום נפרדים.
        val guarded = setOfNotNull(
            activeSessionDir?.absolutePath,
            RecordingSessionState.liveSession(context)?.absolutePath,
        )
        return root.listFiles()
            .orEmpty()
            .filter { it.isDirectory && it.name.startsWith(SESSION_PREFIX) }
            .filterNot { it.absolutePath in guarded }
            .sortedBy { it.name }
    }

    /**
     * מכין את ההקלטות התקועות לשליחה: מאחד קטעים (אם עוד לא אוחדו) ומכניס
     * כל קובץ מאוחד לתור ההעלאה. מחזיר את מספר ההעלאות שנכנסו לתור.
     */
    internal fun prepareAndEnqueue(context: Context): Int {
        var enqueued = 0

        for (sessionDir in strandedSessions(context)) {
            var mergedFiles = mergedFilesIn(sessionDir)

            if (mergedFiles.isEmpty()) {
                val parts = sessionDir.listFiles()
                    .orEmpty()
                    .filter { it.isFile && it.name.startsWith("part_") }
                    .sortedBy { numericSuffix(it.name) }

                if (parts.isEmpty()) {
                    Log.w(TAG, "prepareAndEnqueue: ${sessionDir.name} has no audio, removing")
                    sessionDir.deleteRecursively()
                    continue
                }

                splitBySize(parts).forEachIndexed { index, batch ->
                    val target = File(
                        sessionDir,
                        "$MERGED_PREFIX${String.format(Locale.US, "%02d", index + 1)}$MERGED_SUFFIX"
                    )
                    // האיחוד נכתב לשם זמני ומקבל את שמו הסופי רק בסוף. סריקה
                    // שנקטעת באמצע (המערכת הרגה את התהליך, או sweep חדש שהחליף
                    // את הישן) הייתה אחרת משאירה קובץ חתוך, שהסריקה הבאה הייתה
                    // שולחת לשרת כאילו הוא שלם.
                    val staging = File(sessionDir, "${target.name}.tmp")
                    staging.delete()
                    if (AudioSegmentMerger.merge(batch, staging)) {
                        if (!staging.renameTo(target)) staging.delete()
                    } else {
                        staging.delete()
                    }
                }

                mergedFiles = mergedFilesIn(sessionDir)
                if (mergedFiles.isEmpty()) {
                    Log.e(TAG, "prepareAndEnqueue: merge failed for ${sessionDir.name}, removing")
                    sessionDir.deleteRecursively()
                    continue
                }
                parts.forEach { it.delete() }
            }

            // הקלטה קצרה מהסף לא נשלחת לעיבוד כלל (ראה AudioDuration). המדידה
            // נעשית על הקבצים המאוחדים - כלומר בדיוק על מה שהיה נשלח - ועל
            // כולם יחד, כדי שפגישה ארוכה שהתפצלה לא תיפסל בגלל חלק אחרון קצר.
            if (AudioDuration.isShorterThanMinimum(AudioDuration.totalSeconds(mergedFiles))) {
                Log.i(TAG, "prepareAndEnqueue: ${sessionDir.name} is under the minimum length, dropped")
                sessionDir.deleteRecursively()
                NotificationHelper.notifyRecordingTooShort(context)
                continue
            }

            val baseTitle = runCatching { File(sessionDir, TITLE_NAME).readText().trim() }
                .getOrDefault("")
            val total = mergedFiles.size

            mergedFiles.forEachIndexed { index, audio ->
                // פגישה שנחתכה עולה כמה רשומות; בלי הסימון אי אפשר לדעת שהן
                // חלקים של אותה פגישה ובאיזה סדר.
                val title = when {
                    total == 1 -> baseTitle
                    baseTitle.isBlank() -> "חלק ${index + 1} מתוך $total"
                    else -> "$baseTitle (חלק ${index + 1} מתוך $total)"
                }

                // KEEP ולא REPLACE - אם ההעלאה של החלק הזה כבר רצה או ממתינה
                // ל-retry, סריקה נוספת לא תיצור העלאה כפולה.
                WorkManager.getInstance(context).enqueueUniqueWork(
                    "upload_${sessionDir.name}_${audio.name}",
                    ExistingWorkPolicy.KEEP,
                    OneTimeWorkRequestBuilder<UploadWorker>()
                        .setInputData(
                            workDataOf(
                                UploadWorker.KEY_AUDIO_PATH to audio.absolutePath,
                                UploadWorker.KEY_TITLE to title,
                                UploadWorker.KEY_SESSION_DIR to sessionDir.absolutePath,
                                // מזהה יציב של הקובץ הזה: העלאה שנשלחה שוב
                                // אחרי שהתשובה עליה לא הגיעה לא תיקלט בשרת
                                // כהקלטה נוספת. ראה UploadWorker.
                                UploadWorker.KEY_CLIENT_UPLOAD_ID to
                                    "${sessionDir.name}/${audio.name}",
                                // אורך החלק הזה, שהוא רשומה בפני עצמה בשרת.
                                // בלעדיו האורך נגזר מסוף הדיבור האחרון - כלומר
                                // פגישה שנגמרת בשתיקה נרשמת קצרה מכפי שהיא.
                                UploadWorker.KEY_DURATION_SECONDS to
                                    (AudioDuration.seconds(audio) ?: 0.0),
                            )
                        )
                        .build(),
                )
                enqueued += 1
            }
        }

        return enqueued
    }

    internal fun mergedFilesIn(sessionDir: File): List<File> =
        sessionDir.listFiles()
            .orEmpty()
            .filter {
                it.isFile && it.name.startsWith(MERGED_PREFIX) &&
                    it.name.endsWith(MERGED_SUFFIX) && it.length() > 0
            }
            .sortedBy { numericSuffix(it.name) }

    /**
     * מיון לפי המספר שבשם הקובץ ולא לפי הטקסט. מיון טקסטואלי היה שובר את סדר
     * הקטעים ברגע שהמונה עובר את רוחב הריפוד ("part_1000" מקדים טקסטואלית את
     * "part_999"), כלומר בהקלטה מעל 16 שעות. כך אין תקרת אורך בכלל.
     */
    private fun numericSuffix(name: String): Long =
        name.filter { it.isDigit() }.toLongOrNull() ?: 0L

    /**
     * מחלק קטעים לקבוצות שכל אחת נשארת מתחת ל-[MAX_UPLOAD_BYTES]. החיתוך
     * תמיד נופל על גבול קטע, כלומר על גבול שניה שלמה של אודיו - לא באמצע
     * פריים. קטע בודד שגדול מהגג (לא אמור לקרות בדקה של דיבור) נשלח לבדו,
     * כי עדיף ניסיון שנכשל על פני זריקה שקטה של האודיו.
     */
    internal fun splitBySize(parts: List<File>): List<List<File>> {
        val batches = mutableListOf<List<File>>()
        var current = mutableListOf<File>()
        var currentBytes = 0L

        for (part in parts) {
            val size = part.length()
            if (current.isNotEmpty() && currentBytes + size > MAX_UPLOAD_BYTES) {
                batches += current
                current = mutableListOf()
                currentBytes = 0L
            }
            current += part
            currentBytes += size
        }
        if (current.isNotEmpty()) batches += current
        return batches
    }
}

/**
 * סורק תיקיות session שנותרו על הדיסק, מאחד את הקטעים ושולח לעיבוד.
 * רץ ב-WorkManager (ולא בשירות) כדי שהאיחוד - פעולת קלט/פלט שיכולה לקחת
 * כמה שניות על פגישה ארוכה - לא ייעשה על ה-main thread, ושיישרד סגירה של
 * האפליקציה באמצע.
 */
class RecordingRecoveryWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        // הקלטה שהתהליך שלה מת ולא חזר משוחררת כאן. בלי זה היא הייתה נשארת
        // מסומנת "חיה" על הדיסק לנצח, והסריקה הייתה מדלגת עליה בכל פעם מחדש -
        // כלומר פגישה שהוקלטה ולעולם לא נשלחה.
        RecordingSessionState.expireIfStale(applicationContext)

        runCatching { RecordingRecovery.prepareAndEnqueue(applicationContext) }
            .onFailure { return@withContext Result.retry() }

        // יש הקלטה מוגנת אבל היא לא מוקלטת בתהליך הזה - כלומר השירות נהרג
        // ועדיין לא חזר. חוזרים לבדוק אחרי שחלון החיים יפוג, כדי שהיא תגיע
        // לעיבוד גם אם הוא לא יחזור בכלל, בלי לחכות לפתיחה הבאה של האפליקציה.
        if (!RecordingService.isRecording &&
            RecordingSessionState.liveSession(applicationContext) != null
        ) {
            RecordingRecovery.sweep(
                applicationContext,
                RecordingSessionState.ALIVE_WINDOW_MS + 10_000L,
            )
        }
        Result.success()
    }
}
