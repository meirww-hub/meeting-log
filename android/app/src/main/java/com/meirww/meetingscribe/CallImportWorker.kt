package com.meirww.meetingscribe

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CallLog
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import java.io.File
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * סורק את תיקיית ההקלטות של cally, מעתיק הקלטות שיחה חדשות אל האפליקציה
 * ושולח אותן לעיבוד - בלי שהמשתמש יצטרך לשתף ידנית.
 *
 * cally שומרת כל שיחה כשני קבצים מבודדים, `__uplink` (הצד שלי) ו-`__downlink`
 * (הצד השני), עם קידומת משותפת של חותמת זמן ומזהה שיחה. שני הקבצים נשלחים
 * יחד לשרת, שמתמלל כל ערוץ בנפרד - וכך זיהוי הדוברים יוצא ודאי.
 */
class CallImportWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    companion object {
        private const val CALLY_RECORDINGS_DIR =
            "/storage/emulated/0/Android/data/dev.lyo.callrec/files/recordings"
        private const val PREFS_NAME = "call_import"
        private const val KEY_IMPORTED = "imported_calls"
        private const val IMPORTED_HISTORY_LIMIT = 200
        private const val UNIQUE_WORK_NAME = "cally_call_import"

        /**
         * cally סוגרת את קובץ ה-m4a כמה שניות אחרי ניתוק השיחה, ולכן הסריקה
         * מושהית. הרצה יחידה (REPLACE) מונעת ערימת סריקות כשמתקבלות כמה
         * הודעות מצב טלפון ברצף.
         */
        fun schedule(context: Context, delaySeconds: Long = 20) {
            val request = OneTimeWorkRequestBuilder<CallImportWorker>()
                .setInitialDelay(delaySeconds, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                UNIQUE_WORK_NAME, ExistingWorkPolicy.REPLACE, request
            )
        }
    }

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        if (!ShizukuAccess.isAvailable() || !ShizukuAccess.hasPermission()) {
            return@withContext Result.retry()
        }

        // הגנה מפני שידור IDLE מוקדם מדי (תנודה רגעית ברשת הסלולרית אמצע
        // שיחה, נצפה בפועל) - בלעדיה הסריקה הייתה מעתיקה קובץ חלקי ומסמנת
        // את השיחה כמיובאת, כך שההקלטה המלאה לא הייתה נסרקת שוב לעולם.
        if (isCallActive()) return@withContext Result.retry()

        val listing = ShizukuAccess.runCommand("ls -1 '$CALLY_RECORDINGS_DIR' 2>/dev/null")
            ?: return@withContext Result.retry()

        val fileNames = listing.lines()
            .map { it.trim() }
            .filter { it.endsWith(".m4a", true) || it.endsWith(".wav", true) }
        if (fileNames.isEmpty()) return@withContext Result.success()

        val prefs = applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val imported = prefs.getStringSet(KEY_IMPORTED, emptySet()).orEmpty().toMutableSet()

        // "<חותמת זמן>__<מזהה שיחה>__uplink.m4a" -> מפתח השיחה הוא הקידומת
        // המשותפת עד ה-"__" האחרון.
        val newCallGroups = fileNames.groupBy { it.substringBeforeLast("__") }
            .filterKeys { it !in imported }
        if (newCallGroups.isEmpty()) return@withContext Result.success()

        // שם איש הקשר משויך רק כשיש בדיוק שיחה חדשה אחת בהרצה הזו - אם
        // כמה שיחות מיובאות יחד (למשל אחרי שהמכשיר היה כבוי זמן-מה), אין
        // דרך פשוטה לשייך איזו רשומת יומן שיחות שייכת לאיזה קובץ, ולכן
        // מוותרים על השם (נופל חזרה לתווית הגנרית "הצד השני" - לא רגרסיה,
        // זה בדיוק מה שהיה קורה עד היום).
        val contactName = if (newCallGroups.size == 1) mostRecentCallerName() else null

        val destDir = File(applicationContext.getExternalFilesDir("callimport"), "")
        destDir.mkdirs()

        newCallGroups.forEach { (callKey, files) ->
            val uplink = files.firstOrNull { it.contains("__uplink") }
            val downlink = files.firstOrNull { it.contains("__downlink") }
            val primaryName = uplink ?: files.firstOrNull() ?: return@forEach

            val primaryFile = File(destDir, primaryName)
            if (!copyFromCally(primaryName, primaryFile)) return@forEach

            var downlinkFile: File? = null
            if (uplink != null && downlink != null) {
                val candidate = File(destDir, downlink)
                if (copyFromCally(downlink, candidate)) downlinkFile = candidate
            }

            WorkManager.getInstance(applicationContext).enqueue(
                OneTimeWorkRequestBuilder<UploadWorker>()
                    .setInputData(
                        workDataOf(
                            UploadWorker.KEY_AUDIO_PATH to primaryFile.absolutePath,
                            UploadWorker.KEY_AUDIO_PATH_DOWNLINK to
                                (downlinkFile?.absolutePath ?: ""),
                            // בלי כותרת - השרת מייצר כותרת לפי תוכן השיחה.
                            UploadWorker.KEY_TITLE to "",
                            UploadWorker.KEY_CONTACT_NAME to (contactName ?: ""),
                        )
                    )
                    .build()
            )
            imported += callKey
        }

        prefs.edit()
            .putStringSet(
                KEY_IMPORTED,
                imported.toList().takeLast(IMPORTED_HISTORY_LIMIT).toSet(),
            )
            .apply()

        Result.success()
    }

    /**
     * true אם יש כרגע שיחה פעילה (מחוברת או מצלצלת) - אם שידור ה-IDLE
     * שהפעיל את הריצה הזו היה מוקדם מדי (למשל תנודה רגעית ברשת), אנדרואיד
     * עדיין יראה מצב שיחה פעיל בפועל.
     */
    private fun isCallActive(): Boolean {
        val telephonyManager = applicationContext
            .getSystemService(Context.TELEPHONY_SERVICE) as? TelephonyManager
            ?: return false
        return telephonyManager.callState != TelephonyManager.CALL_STATE_IDLE
    }

    /** chmod נדרש כי הקובץ נוצר בבעלות תהליך ה-shell, לא בבעלות האפליקציה. */
    private fun copyFromCally(sourceName: String, destination: File): Boolean {
        val source = "$CALLY_RECORDINGS_DIR/$sourceName"
        val target = destination.absolutePath
        ShizukuAccess.runCommand("cp '$source' '$target' && chmod 666 '$target'")
        return destination.exists() && destination.length() > 0
    }

    /**
     * שם איש הקשר של רשומת השיחה האחרונה ביומן השיחות של הטלפון - אנדרואיד
     * מזהה ומשבץ אותו אוטומטית מרשימת אנשי הקשר (CACHED_NAME) בזמן כתיבת
     * הרשומה, כך שאין צורך בהרשאת אנשי קשר נפרדת או בהתאמת מספר ידנית.
     * מחזיר null אם ההרשאה חסרה, אין רשומות, או שהמספר לא שמור באנשי הקשר.
     */
    private fun mostRecentCallerName(): String? {
        val hasPermission = ContextCompat.checkSelfPermission(
            applicationContext, Manifest.permission.READ_CALL_LOG
        ) == PackageManager.PERMISSION_GRANTED
        if (!hasPermission) return null

        return applicationContext.contentResolver.query(
            CallLog.Calls.CONTENT_URI,
            arrayOf(CallLog.Calls.CACHED_NAME),
            null, null,
            "${CallLog.Calls.DATE} DESC",
        )?.use { cursor ->
            if (cursor.moveToFirst()) {
                cursor.getString(cursor.getColumnIndexOrThrow(CallLog.Calls.CACHED_NAME))
                    ?.trim()?.takeIf { it.isNotEmpty() }
            } else null
        }
    }
}
