package com.meirww.meetingscribe

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * מעלה קובץ הקלטה לשרת העיבוד ברקע (עמיד לניתוק רשת/סגירת האפליקציה,
 * WorkManager מנהל retry אוטומטית).
 */
class UploadWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    companion object {
        private const val TAG = "UploadWorker"

        const val KEY_AUDIO_PATH = "audio_path"

        /**
         * ערוץ הצד השני, כשההקלטה הגיעה משיחת טלפון שנקלטה בשני ערוצים
         * מבודדים. ריק בהקלטה רגילה. ראה CallImportWorker.
         */
        const val KEY_AUDIO_PATH_DOWNLINK = "audio_path_downlink"
        const val KEY_TITLE = "title"
        const val KEY_RECORDING_ID = "recording_id"

        /**
         * תיקיית ה-session של הקלטת פגישה. היא נמחקת רק אחרי שהשרת אישר
         * קליטה - כל עוד היא קיימת, סריקת ההשלמה תנסה לשלוח אותה שוב.
         * ריק בהקלטת שיחה מיובאת (ראה RecordingRecovery).
         */
        const val KEY_SESSION_DIR = "session_dir"

        /** שם איש הקשר של השיחה (רק בהקלטת שיחת טלפון) - ראה CallImportWorker. */
        const val KEY_CONTACT_NAME = "contact_name"

        /**
         * מזהה יציב של מקור ההקלטה (מפתח השיחה אצל cally, או תיקיית ה-session
         * ושם הקובץ בהקלטת פגישה). השרת גוזר ממנו את מזהה ההקלטה, ולכן העלאה
         * שנייה של אותו מקור מזוהה שם ולא יוצרת רשומה כפולה.
         *
         * זה מכסה בדיוק את המקרה שאי אפשר להגן עליו מהצד הזה: השרת קלט את
         * ההעלאה אבל התשובה לא הגיעה (טיימאאוט/ניתוק), WorkManager ראה כישלון
         * וניסה שוב - וכך אותה הקלטה נקלטה פעמיים.
         */
        const val KEY_CLIENT_UPLOAD_ID = "client_upload_id"

        /**
         * אורך האודיו בשניות, כפי שנמדד כאן לפני ההעלאה.
         *
         * השרת גוזר אחרת את האורך מסוף הדיבור האחרון בתמלול, וזה קירוב שגוי
         * בכל הקלטה שמסתיימת בשתיקה: שיחה בת 4 דקות שרובה המתנה למוקד נרשמה
         * כ-125 שניות, כלומר גם הוצג אורך שגוי וגם הניקוי האוטומטי - שמוחק
         * הקלטות קצרות - ראה אותה כמועמדת למחיקה. המדידה כאן ממילא מתבצעת
         * בשביל סף האורך המזערי (ראה AudioDuration), ולכן היא בחינם.
         */
        const val KEY_DURATION_SECONDS = "duration_seconds"

        // אפליקציה חד-משתמשית (הדרייב האישי של הבעלים) - אין צורך בזיהוי
        // ריבוי-משתמשים כרגע.
        private const val OWNER_USER_ID = "primary_user"
    }

    /**
     * טיימאאוטים ארוכים מהמוגדר כברירת מחדל (10 שניות) - הקלטות שיחה
     * דו-ערוציות יכולות להגיע לכמה MB, וב-10 שניות על רשת סלולרית ה-upload
     * נכשל תמיד ונכנס ללולאת retry אינסופית של WorkManager בלי שההעלאה
     * מגיעה בכלל לשרת (נצפה בפועל - שיחות ארוכות נתקעו ב-WorkManager retry).
     *
     * ה-write הועלה מ-120 שניות: חלק העלאה יכול להגיע ל-30MiB (ראה
     * RecordingRecovery.MAX_UPLOAD_BYTES), וב-120 שניות זה דרש כ-2Mbps יציב -
     * ברשת סלולרית חלשה ההעלאה נחתכה באמצע וחזרה ל-retry שוב ושוב. 7 דקות
     * מורידות את הדרישה לכ-73KB/s, ועדיין נשארות בתוך תקציב 10 הדקות
     * ש-WorkManager נותן ל-worker לפני שהמערכת עוצרת אותו.
     */
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(420, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    override suspend fun doWork(): Result {
        val audioPath = inputData.getString(KEY_AUDIO_PATH) ?: return Result.failure()
        val title = inputData.getString(KEY_TITLE).orEmpty()
        val sessionDir = inputData.getString(KEY_SESSION_DIR)
            ?.takeIf { it.isNotBlank() }
            ?.let { File(it) }
        val audioFile = File(audioPath)
        if (!audioFile.exists()) {
            // אין מה לשלוח - מנקים כדי שהסריקה לא תחזור על ה-session הזה לנצח.
            sessionDir?.deleteRecursively()
            return Result.failure()
        }

        val bodyBuilder = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart(
                "file", audioFile.name,
                audioFile.asRequestBody("audio/mp4".toMediaType())
            )
            .addFormDataPart("title", title)
            .addFormDataPart("user_id", OWNER_USER_ID)

        val downlinkFile = inputData.getString(KEY_AUDIO_PATH_DOWNLINK)
            ?.takeIf { it.isNotBlank() }
            ?.let { File(it) }
            ?.takeIf { it.exists() }
        if (downlinkFile != null) {
            bodyBuilder.addFormDataPart(
                "file_downlink", downlinkFile.name,
                downlinkFile.asRequestBody("audio/mp4".toMediaType())
            )
        }

        val contactName = inputData.getString(KEY_CONTACT_NAME).orEmpty()
        if (contactName.isNotBlank()) {
            bodyBuilder.addFormDataPart("contact_name", contactName)
        }

        val clientUploadId = inputData.getString(KEY_CLIENT_UPLOAD_ID).orEmpty()
        if (clientUploadId.isNotBlank()) {
            bodyBuilder.addFormDataPart("client_upload_id", clientUploadId)
        }

        // 0 = לא נמדד (קובץ פגום/לא קריא). השרת נופל אז לחישוב מהתמלול.
        val durationSeconds = inputData.getDouble(KEY_DURATION_SECONDS, 0.0)
        if (durationSeconds > 0) {
            bodyBuilder.addFormDataPart("duration_seconds", durationSeconds.toString())
        }
        val notificationLabel = title.ifBlank { contactName }

        val requestBody = bodyBuilder.build()

        val request = Request.Builder()
            .url("${BuildConfig.BACKEND_BASE_URL}/recordings")
            .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
            .post(requestBody)
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                // 413 = הגוף עבר את מגבלת 32MiB של Cloud Run. זה כישלון קבוע,
                // ולא היה עוזר לנסות שוב לעולם - עד היום הוא תורגם ל-retry
                // ולכן פגישות ארוכות "נעלמו" בלולאת retry אילמת של WorkManager
                // בלי שום סימן למשתמש. אמור להיות בלתי אפשרי עכשיו (ההקלטה
                // נחתכת מראש לפי גודל), ולכן מדווח בקול.
                if (response.code == 413) {
                    Log.e(TAG, "upload rejected as too large: ${audioFile.length()} bytes")
                    NotificationHelper.notifyRecordingFailed(
                        applicationContext, audioPath, notificationLabel
                    )
                    return Result.failure()
                }
                if (!response.isSuccessful) return Result.retry()
                val body = response.body?.string().orEmpty()
                val recordingId = Regex("\"recording_id\"\\s*:\\s*\"([^\"]+)\"")
                    .find(body)?.groupValues?.get(1).orEmpty()
                if (recordingId.isNotBlank()) {
                    RecordingStatusWorker.schedule(applicationContext, recordingId, notificationLabel)
                    // קושר את ההקלטה בשרת לשיחה שממנה הגיעה, כדי ש"נסה לעבד
                    // שוב" בהיסטוריה יוכל לייבא אותה מחדש מ-cally אם העיבוד
                    // ייכשל. ראה CallImportWorker.retryFromCally.
                    if (sessionDir == null) {
                        CallImportWorker.rememberUpload(
                            applicationContext, recordingId, clientUploadId
                        )
                    }
                }
                // השרת קלט - רק עכשיו מותר לשחרר את העותק המקומי. נמחק רק
                // החלק הזה; התיקייה נעלמת כשהחלק האחרון שלה הגיע ליעדו.
                audioFile.delete()
                // ערוץ הצד השני נשאר עד היום בתיקיית הייבוא לנצח - העותקים
                // הצטברו שם בלי שאיש קרא אותם שוב.
                downlinkFile?.delete()
                releaseSessionDirIfDone(sessionDir)
                Result.success(workDataOf(KEY_RECORDING_ID to recordingId))
            }
        } catch (e: Exception) {
            Result.retry()
        }
    }

    /**
     * מוחק את תיקיית ה-session רק כשלא נותר בה אודיו שממתין לשליחה - פגישה
     * ארוכה מתפצלת לכמה חלקים, ואסור שהחלק הראשון שמסתיים ימחק מתחת לרגליים
     * של החלקים שעוד לא עלו.
     */
    private fun releaseSessionDirIfDone(sessionDir: File?) {
        if (sessionDir == null || !sessionDir.isDirectory) return
        if (RecordingRecovery.mergedFilesIn(sessionDir).isNotEmpty()) return
        sessionDir.deleteRecursively()
    }
}
