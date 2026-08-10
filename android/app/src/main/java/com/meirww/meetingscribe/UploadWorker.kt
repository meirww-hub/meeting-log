package com.meirww.meetingscribe

import android.content.Context
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
        const val KEY_AUDIO_PATH = "audio_path"

        /**
         * ערוץ הצד השני, כשההקלטה הגיעה משיחת טלפון שנקלטה בשני ערוצים
         * מבודדים. ריק בהקלטה רגילה. ראה CallImportWorker.
         */
        const val KEY_AUDIO_PATH_DOWNLINK = "audio_path_downlink"
        const val KEY_TITLE = "title"
        const val KEY_RECORDING_ID = "recording_id"

        /** שם איש הקשר של השיחה (רק בהקלטת שיחת טלפון) - ראה CallImportWorker. */
        const val KEY_CONTACT_NAME = "contact_name"

        // אפליקציה חד-משתמשית (הדרייב האישי של הבעלים) - אין צורך בזיהוי
        // ריבוי-משתמשים כרגע.
        private const val OWNER_USER_ID = "primary_user"
    }

    /**
     * טיימאאוטים ארוכים מהמוגדר כברירת מחדל (10 שניות) - הקלטות שיחה
     * דו-ערוציות יכולות להגיע לכמה MB, וב-10 שניות על רשת סלולרית ה-upload
     * נכשל תמיד ונכנס ללולאת retry אינסופית של WorkManager בלי שההעלאה
     * מגיעה בכלל לשרת (נצפה בפועל - שיחות ארוכות נתקעו ב-WorkManager retry).
     */
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    override suspend fun doWork(): Result {
        val audioPath = inputData.getString(KEY_AUDIO_PATH) ?: return Result.failure()
        val title = inputData.getString(KEY_TITLE).orEmpty()
        val audioFile = File(audioPath)
        if (!audioFile.exists()) return Result.failure()

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
        val notificationLabel = title.ifBlank { contactName }

        val requestBody = bodyBuilder.build()

        val request = Request.Builder()
            .url("${BuildConfig.BACKEND_BASE_URL}/recordings")
            .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
            .post(requestBody)
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return Result.retry()
                val body = response.body?.string().orEmpty()
                val recordingId = Regex("\"recording_id\"\\s*:\\s*\"([^\"]+)\"")
                    .find(body)?.groupValues?.get(1).orEmpty()
                if (recordingId.isNotBlank()) {
                    RecordingStatusWorker.schedule(applicationContext, recordingId, notificationLabel)
                }
                Result.success(workDataOf(KEY_RECORDING_ID to recordingId))
            }
        } catch (e: Exception) {
            Result.retry()
        }
    }
}
