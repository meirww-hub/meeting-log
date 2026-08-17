package com.meirww.meetingscribe

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * מחכה שהקלטה שהועלתה תסיים להתעבד בשרת (עיבוד רץ ב-background אחרי שה-
 * upload כבר החזיר תשובה) ומציג התראה - זה מה שמאפשר למשתמש לדעת שההקלטה
 * מוכנה בלי לפתוח את האפליקציה ולבדוק ידנית.
 */
class RecordingStatusWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    companion object {
        const val KEY_RECORDING_ID = "recording_id"
        const val KEY_LABEL = "label"

        // עיבוד שיחה דו-ערוצית לוקח בדרך כלל דקה-שתיים; המכסה נדיבה כדי
        // לשרוד גם הקלטות ארוכות או retry על 429 (ר' _retry.py בשרת).
        private const val MAX_ATTEMPTS = 40
        private const val POLL_INTERVAL_SECONDS = 20L

        fun schedule(context: Context, recordingId: String, label: String) {
            val request = OneTimeWorkRequestBuilder<RecordingStatusWorker>()
                .setInitialDelay(POLL_INTERVAL_SECONDS, TimeUnit.SECONDS)
                .setBackoffCriteria(BackoffPolicy.LINEAR, POLL_INTERVAL_SECONDS, TimeUnit.SECONDS)
                .setInputData(
                    workDataOf(
                        KEY_RECORDING_ID to recordingId,
                        KEY_LABEL to label,
                    )
                )
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                "status_poll_$recordingId", ExistingWorkPolicy.REPLACE, request
            )
        }
    }

    private val client = OkHttpClient()

    override suspend fun doWork(): Result {
        val recordingId = inputData.getString(KEY_RECORDING_ID) ?: return Result.failure()
        val label = inputData.getString(KEY_LABEL).orEmpty()

        val request = Request.Builder()
            .url("${BuildConfig.BACKEND_BASE_URL}/recordings/$recordingId")
            .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
            .get()
            .build()

        val body = try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return giveUpOrRetry(recordingId, label)
                response.body?.string().orEmpty()
            }
        } catch (e: Exception) {
            return giveUpOrRetry(recordingId, label)
        }

        val status = Regex("\"status\"\\s*:\\s*\"([^\"]+)\"").find(body)?.groupValues?.get(1)

        return when (status) {
            "done" -> {
                val title = Regex("\"title\"\\s*:\\s*\"([^\"]+)\"").find(body)?.groupValues?.get(1)
                NotificationHelper.notifyRecordingReady(applicationContext, recordingId, title ?: label)
                Result.success()
            }
            "error" -> {
                NotificationHelper.notifyRecordingFailed(applicationContext, recordingId, label)
                Result.success()
            }
            else -> giveUpOrRetry(recordingId, label)
        }
    }

    /**
     * בדרך כלל השרת עצמו מזהה הקלטה תקועה תוך חצי שעה ומעביר אותה ל-"error"
     * (ראה recover_stale_recordings), וזה נכנס למסלול הרגיל למעלה. אבל
     * הבדיקה הזו רצה רק כשהאפליקציה נפתחת - אם זה לא קורה תוך זמן סביר,
     * עדיף להודיע ש"עדיין לא ידוע" מאשר לוותר אחרי MAX_ATTEMPTS בשקט גמור,
     * בדיוק כמו שקרה בפועל ב-2026-08-16.
     */
    private fun giveUpOrRetry(recordingId: String, label: String): Result {
        if (runAttemptCount < MAX_ATTEMPTS) return Result.retry()
        NotificationHelper.notifyRecordingStatusUnknown(applicationContext, recordingId, label)
        return Result.failure()
    }
}
