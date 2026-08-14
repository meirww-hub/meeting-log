package com.meirww.meetingscribe

import android.content.Context
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit

/**
 * מוריד את קובץ האודיו של הקלטה לזיכרון המטמון של האפליקציה, ומחזיר אותו
 * כקובץ מקומי לניגון.
 *
 * למה להוריד ולא להזרים ישירות לנגן: קפיצה לדקה מסוימת בתוך m4a דורשת את
 * טבלת האינדקס (moov), ש-MediaRecorder כותב **בסוף** הקובץ. נגן שמזרים
 * מהרשת היה צריך לקפוץ קדימה ואחורה בתוך שידור חי כדי למצוא אותה, וכל
 * גרירה של הסרגל הייתה בקשת רשת נוספת. קובץ מקומי הופך את הקפיצה למיידית
 * ומדויקת - וזו כל הבקשה: "הדבר התרחש בדקה 2:21" צריך להישמע בהקשה אחת.
 *
 * ההורדה מתחדשת מהנקודה שבה נקטעה (קובץ .part + כותרת Range), כי הקלטה של
 * שעה שוקלת עשרות מגה-בייט ורשת סלולרית נופלת באמצע.
 */
object AudioCache {

    private const val TAG = "AudioCache"
    private const val DIR_NAME = "recording_audio"
    private const val BUFFER_BYTES = 64 * 1024

    /** תקרה למטמון: מעליה נמחקות ההקלטות שלא נוגנו הכי מזמן. */
    private const val MAX_CACHE_BYTES = 400L * 1024 * 1024

    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        // ללא תקרת זמן לקריאה כולה: הורדה של פגישה ארוכה ברשת איטית
        // לגיטימית, ואין סיבה להפיל אותה באמצע.
        .callTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private fun dir(context: Context): File =
        File(context.cacheDir, DIR_NAME).apply { mkdirs() }

    private fun localFile(context: Context, recordingId: String, channel: Int): File =
        File(dir(context), "${recordingId}_$channel.m4a")

    /**
     * הקובץ המקומי של הערוץ, אחרי הורדה אם עוד אין כזה. חוסם - יש לקרוא
     * מ-Dispatchers.IO. מחזיר null אם ההורדה נכשלה.
     *
     * [onProgress] מקבל (בייטים שהורדו, סה"כ), כשסה"כ הוא 0 אם השרת לא
     * דיווח עליו.
     */
    fun ensureLocal(
        context: Context,
        recordingId: String,
        channel: Int,
        onProgress: (Long, Long) -> Unit = { _, _ -> },
    ): File? {
        val target = localFile(context, recordingId, channel)
        if (target.length() > 0) {
            // סימון "נוגן עכשיו", כדי שהפינוי ימחק דווקא את הישנות.
            target.setLastModified(System.currentTimeMillis())
            return target
        }

        val partial = File("${target.absolutePath}.part")
        return try {
            if (download(recordingId, channel, partial, onProgress)) {
                target.delete()
                if (!partial.renameTo(target)) {
                    Log.w(TAG, "ensureLocal: rename failed for $recordingId/$channel")
                    return null
                }
                trimCache(context, keep = target)
                target
            } else {
                null
            }
        } catch (e: Exception) {
            // הקובץ החלקי נשאר בכוונה - הניסיון הבא ימשיך ממנו.
            Log.w(TAG, "ensureLocal: download failed for $recordingId/$channel", e)
            null
        }
    }

    private fun download(
        recordingId: String,
        channel: Int,
        partial: File,
        onProgress: (Long, Long) -> Unit,
    ): Boolean {
        val alreadyHave = partial.length()
        val request = Request.Builder()
            .url("${BuildConfig.BACKEND_BASE_URL}/recordings/$recordingId/audio?channel=$channel")
            .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
            .apply { if (alreadyHave > 0) header("Range", "bytes=$alreadyHave-") }
            .get()
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                // 416 = הקובץ החלקי כבר ארוך מהקובץ עצמו (הקלטה שהוחלפה
                // בצד השרת). מוחקים אותו כדי שהניסיון הבא יתחיל נקי.
                if (response.code == 416) partial.delete()
                Log.w(TAG, "download: server returned ${response.code}")
                return false
            }

            val body = response.body ?: return false
            val total = response.header("X-Audio-Size")?.toLongOrNull() ?: 0L
            val resumed = response.code == 206
            if (!resumed) partial.delete()

            var downloaded = if (resumed) alreadyHave else 0L
            onProgress(downloaded, total)

            FileOutputStream(partial, resumed).use { output ->
                body.byteStream().use { input ->
                    val buffer = ByteArray(BUFFER_BYTES)
                    while (true) {
                        val read = input.read(buffer)
                        if (read == -1) break
                        output.write(buffer, 0, read)
                        downloaded += read
                        onProgress(downloaded, total)
                    }
                }
            }

            // קובץ אודיו קטוע הוא קובץ בלי moov - כלומר לא ניתן לניגון כלל.
            // עדיף להיכשל בגלוי ולהשאיר את החלק שהורד להמשך.
            if (total > 0 && partial.length() != total) {
                Log.w(TAG, "download: truncated (${partial.length()} of $total)")
                return false
            }
            return true
        }
    }

    /** מוחק את הקבצים שלא נוגנו הכי מזמן, עד שהמטמון חוזר מתחת לתקרה. */
    private fun trimCache(context: Context, keep: File) {
        val files = dir(context).listFiles()?.toMutableList() ?: return
        var total = files.sumOf { it.length() }
        if (total <= MAX_CACHE_BYTES) return

        files.sortBy { it.lastModified() }
        for (file in files) {
            if (total <= MAX_CACHE_BYTES) return
            if (file == keep) continue
            val size = file.length()
            if (file.delete()) total -= size
        }
    }
}
