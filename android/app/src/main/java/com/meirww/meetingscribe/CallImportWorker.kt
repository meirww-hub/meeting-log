package com.meirww.meetingscribe

import android.Manifest
import android.content.Context
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.CallLog
import android.provider.ContactsContract
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import androidx.work.BackoffPolicy
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
        private const val IMPORTED_HISTORY_LIMIT = 500
        private const val UNIQUE_WORK_NAME = "cally_call_import"

        private const val RETRY_BACKOFF_SECONDS = 20L

        /** קידומת למיפוי מזהה-הקלטה-בשרת -> מפתח השיחה אצל cally. */
        private const val KEY_RECORDING_PREFIX = "recording_"

        /**
         * זוכר לאיזו שיחה אצל cally שייכת ההקלטה שנוצרה בשרת.
         *
         * זה מה שמאפשר "נסה לעבד שוב" מההיסטוריה: הקבצים המקומיים נמחקים
         * אחרי העלאה מוצלחת, אבל **cally עדיין שומרת את המקור**, ולכן די
         * למחוק את הסימון כדי שהסריקה הבאה תייבא ותעלה אותה שוב. אין צורך
         * להחזיק עותק שני על המכשיר.
         */
        fun rememberUpload(context: Context, recordingId: String, callKey: String) {
            if (recordingId.isBlank() || callKey.isBlank()) return
            context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putString("$KEY_RECORDING_PREFIX$recordingId", callKey)
                .apply()
        }

        /**
         * מבטל את סימון הייבוא של ההקלטה, כדי שהסריקה הבאה תייבא אותה מחדש
         * מ-cally ותשלח אותה שוב לעיבוד. מחזיר false אם לא ידוע לאיזו שיחה
         * ההקלטה שייכת, או שהקבצים כבר לא קיימים אצל cally.
         *
         * ההעלאה החוזרת נושאת את אותו client_upload_id, ולכן השרת נופל על
         * אותה רשומה בדיוק (ומריץ אותה מחדש, כי היא במצב error) - בלי
         * ליצור הקלטה כפולה. ראה main.py.
         */
        fun retryFromCally(context: Context, recordingId: String): Boolean {
            val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            val callKey = prefs.getString("$KEY_RECORDING_PREFIX$recordingId", null)
                ?: return false

            val stored = prefs.getStringSet(KEY_IMPORTED, emptySet()).orEmpty()
            prefs.edit()
                .putStringSet(
                    KEY_IMPORTED,
                    CallImportScan.importedKeys(stored) - callKey,
                )
                .commit()
            schedule(context, delaySeconds = 1)
            return true
        }

        /**
         * cally סוגרת את קובץ ה-m4a כמה שניות אחרי ניתוק השיחה, ולכן הסריקה
         * מושהית. הרצה יחידה (REPLACE) מונעת ערימת סריקות כשמתקבלות כמה
         * הודעות מצב טלפון ברצף.
         */
        fun schedule(context: Context, delaySeconds: Long = 20) {
            val request = OneTimeWorkRequestBuilder<CallImportWorker>()
                .setInitialDelay(delaySeconds, TimeUnit.SECONDS)
                // סריקה שדילגה על קובץ שעוד נכתב חוזרת מהר, במקום לחכות
                // לשיחה הבאה או לפתיחת האפליקציה.
                .setBackoffCriteria(
                    BackoffPolicy.LINEAR, RETRY_BACKOFF_SECONDS, TimeUnit.SECONDS
                )
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

        val listing = readCallyDir() ?: return@withContext Result.retry()
        if (listing.files.isEmpty()) return@withContext Result.success()

        val prefs = applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val stored = prefs.getStringSet(KEY_IMPORTED, emptySet()).orEmpty()

        // מייבאים רק שיחה שקבציה כבר לא משתנים; שיחה שעדיין נכתבת ממתינה
        // לסריקה הבאה. ראה CallImportScan.
        val (ready, stillWriting) = CallImportScan.plan(
            listing, CallImportScan.importedKeys(stored)
        )
        if (ready.isEmpty()) {
            return@withContext if (stillWriting.isEmpty()) Result.success() else Result.retry()
        }

        // שם איש הקשר משויך רק כשיש בדיוק שיחה חדשה אחת בהרצה הזו - אם
        // כמה שיחות מיובאות יחד (למשל אחרי שהמכשיר היה כבוי זמן-מה), אין
        // דרך פשוטה לשייך איזו רשומת יומן שיחות שייכת לאיזה קובץ, ולכן
        // מוותרים על השם (נופל חזרה לתווית הגנרית "הצד השני" - לא רגרסיה,
        // זה בדיוק מה שהיה קורה עד היום).
        val contactName = if (ready.size == 1) mostRecentCallerName() else null

        val destDir = File(applicationContext.getExternalFilesDir("callimport"), "")
        destDir.mkdirs()

        for ((callKey, files) in ready) {
            // הסריקה הזו בוטלה (REPLACE ע"י סריקה חדשה) - עוצרים כאן במקום
            // להמשיך לייבא במקביל לסריקה שהחליפה אותה. בלי הבדיקה הזו שתי
            // הסריקות רצות יחד על אותם קבצים, ושתיהן מייבאות אותם.
            if (isStopped) return@withContext Result.retry()

            val uplink = files.firstOrNull { it.name.contains("__uplink") }
            val downlink = files.firstOrNull { it.name.contains("__downlink") }
            val primary = uplink ?: files.firstOrNull() ?: continue

            val primaryFile = File(destDir, primary.name)
            if (!copyFromCally(primary.name, primaryFile)) continue

            var downlinkFile: File? = null
            if (uplink != null && downlink != null) {
                val candidate = File(destDir, downlink.name)
                if (copyFromCally(downlink.name, candidate)) downlinkFile = candidate
            }

            // שיחה קצרה מהמינימום לא נשלחת לעיבוד (ראה AudioDuration) - בשקט
            // ובלי התראה, כי שיחות קצרות הן שגרה יומיומית ולא אירוע. שני
            // הערוצים מתנגנים במקביל לאורך אותה שיחה, ולכן נמדד הארוך שבהם
            // ולא הסכום.
            val callSeconds =
                AudioDuration.longestSeconds(listOfNotNull(primaryFile, downlinkFile))
            if (AudioDuration.isShorterThanMinimum(callSeconds)) {
                primaryFile.delete()
                downlinkFile?.delete()
                markImported(prefs, callKey)
                continue
            }

            // עבודה ייחודית לפי מפתח השיחה, ו-KEEP: גם אם אותה שיחה מגיעה
            // לכאן פעמיים (שתי סריקות שרצו יחד), ההעלאה השנייה לא נכנסת לתור.
            WorkManager.getInstance(applicationContext).enqueueUniqueWork(
                "call_upload_$callKey",
                ExistingWorkPolicy.KEEP,
                OneTimeWorkRequestBuilder<UploadWorker>()
                    .setInputData(
                        workDataOf(
                            UploadWorker.KEY_AUDIO_PATH to primaryFile.absolutePath,
                            UploadWorker.KEY_AUDIO_PATH_DOWNLINK to
                                (downlinkFile?.absolutePath ?: ""),
                            // בלי כותרת - השרת מייצר כותרת לפי תוכן השיחה.
                            UploadWorker.KEY_TITLE to "",
                            UploadWorker.KEY_CONTACT_NAME to (contactName ?: ""),
                            UploadWorker.KEY_CLIENT_UPLOAD_ID to callKey,
                            // האורך שכבר נמדד למעלה - כך תג האורך והניקוי
                            // האוטומטי מסתמכים על השיחה עצמה ולא על סוף הדיבור.
                            UploadWorker.KEY_DURATION_SECONDS to (callSeconds ?: 0.0),
                        )
                    )
                    .build(),
            )
            markImported(prefs, callKey)
        }

        if (stillWriting.isEmpty()) Result.success() else Result.retry()
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

    /**
     * קורא את תיקיית cally: זמן השינוי האחרון של כל קובץ, יחד עם שעון המכשיר
     * באותו רגע.
     *
     * `date` רץ באותה פקודה כדי שההשוואה "כמה זמן לא נגעו בקובץ" תיעשה מול
     * אותו שעון שנתן את חותמות הזמן. `ls -la` לא מספיק כאן: הוא מדווח דקות
     * בלבד, בלי שניות.
     */
    private fun readCallyDir(): CallImportScan.CallyListing? =
        ShizukuAccess.runCommand(
            "date +%s; stat -c '%Y %n' '$CALLY_RECORDINGS_DIR'/* 2>/dev/null"
        )?.let { CallImportScan.parseListing(it) }

    /**
     * מסמן שיחה כמיובאת - מיד, ובכתיבה סינכרונית (commit ולא apply).
     *
     * הסימון בסוף הסריקה, כפי שהיה, השאיר את כל השיחות שיובאו בהרצה בלי סימון
     * על הדיסק לאורך כל ההעתקה; סריקה שנייה שהתחילה באותן שניות (סיום שיחה
     * ופתיחת האפליקציה מתרחשים יחד) ראתה אותן כחדשות והעלתה אותן בשנית.
     */
    private fun markImported(prefs: SharedPreferences, callKey: String) {
        val stored = prefs.getStringSet(KEY_IMPORTED, emptySet()).orEmpty()
        prefs.edit()
            .putStringSet(
                KEY_IMPORTED,
                CallImportScan.watermarkWith(stored, callKey, IMPORTED_HISTORY_LIMIT),
            )
            .commit()
    }

    /** chmod נדרש כי הקובץ נוצר בבעלות תהליך ה-shell, לא בבעלות האפליקציה. */
    private fun copyFromCally(sourceName: String, destination: File): Boolean {
        val source = "$CALLY_RECORDINGS_DIR/$sourceName"
        val target = destination.absolutePath
        ShizukuAccess.runCommand("cp '$source' '$target' && chmod 666 '$target'")
        return destination.exists() && destination.length() > 0
    }

    /**
     * שם הדובר שבצד השני, כפי שהוא שמור **ברשימת אנשי הקשר של הטלפון**.
     *
     * מקור האמת הוא ContactsContract ולא CACHED_NAME שביומן השיחות:
     * CACHED_NAME הוא צילום מטמוני שנכתב פעם אחת, בזמן השיחה, ולא מתעדכן
     * אחר כך - איש קשר ששמו תוקן או שנשמר רק אחרי השיחה נשאר שם ריק או
     * מיושן, וזה בדיוק השם שהיה נדבק לתמלול ולסיכום. PhoneLookup מצליב את
     * המספר מול אנשי הקשר החיים ומחזיר את השם הנוכחי בדיוק כפי שהוא כתוב
     * שם - בלי לחתוך, בלי לנרמל וכולל שם משפחה.
     *
     * CACHED_NAME נשאר כגיבוי, למקרה שהרשאת אנשי הקשר לא ניתנה.
     * מחזיר null אם אין רשומות או שהמספר לא שמור באנשי הקשר.
     */
    private fun mostRecentCallerName(): String? {
        val hasCallLog = ContextCompat.checkSelfPermission(
            applicationContext, Manifest.permission.READ_CALL_LOG
        ) == PackageManager.PERMISSION_GRANTED
        if (!hasCallLog) return null

        val call = applicationContext.contentResolver.query(
            CallLog.Calls.CONTENT_URI,
            arrayOf(CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME),
            null, null,
            "${CallLog.Calls.DATE} DESC",
        )?.use { cursor ->
            if (!cursor.moveToFirst()) return@use null
            val number = cursor.getString(cursor.getColumnIndexOrThrow(CallLog.Calls.NUMBER))
            val cached = cursor.getString(cursor.getColumnIndexOrThrow(CallLog.Calls.CACHED_NAME))
            number.orEmpty() to cached?.trim()?.takeIf { it.isNotEmpty() }
        } ?: return null

        val (number, cachedName) = call
        val raw = contactNameForNumber(number) ?: cachedName
        return ContactName.toSpeakerLabel(raw)
    }

    /**
     * שם התצוגה של המספר מתוך אנשי הקשר. PhoneLookup מבצע בעצמו את השוואת
     * המספרים לפי כללי המדינה, ולכן "+972-50-123-4567" ביומן השיחות מותאם
     * לאיש קשר ששמור כ-"050-1234567".
     */
    private fun contactNameForNumber(number: String): String? {
        if (number.isBlank()) return null
        val hasContacts = ContextCompat.checkSelfPermission(
            applicationContext, Manifest.permission.READ_CONTACTS
        ) == PackageManager.PERMISSION_GRANTED
        if (!hasContacts) return null

        val uri = Uri.withAppendedPath(
            ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number)
        )
        return try {
            applicationContext.contentResolver.query(
                uri, arrayOf(ContactsContract.PhoneLookup.DISPLAY_NAME), null, null, null
            )?.use { cursor ->
                if (cursor.moveToFirst()) {
                    cursor.getString(
                        cursor.getColumnIndexOrThrow(ContactsContract.PhoneLookup.DISPLAY_NAME)
                    )?.trim()?.takeIf { it.isNotEmpty() }
                } else null
            }
        } catch (e: Exception) {
            null
        }
    }
}
