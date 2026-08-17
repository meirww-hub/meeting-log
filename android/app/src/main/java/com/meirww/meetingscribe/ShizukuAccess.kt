package com.meirww.meetingscribe

import android.content.Context
import android.content.pm.PackageManager
import java.io.InputStream
import rikka.shizuku.Shizuku

/**
 * גישה בהרשאות shell דרך Shizuku.
 *
 * נחוץ כדי לקרוא את הקלטות השיחה של cally: מאנדרואיד 11 ואילך תיקיות
 * `Android/data/<אפליקציה>` חסומות בפני אפליקציות אחרות, וגם הרשאת
 * "גישה לכל הקבצים" לא פותחת אותן. Shizuku מריצה פקודות בהרשאות של adb,
 * שכן רשאיות לקרוא שם - וזו הדרך היחידה להעביר את ההקלטה אלינו אוטומטית
 * במקום שהמשתמש ישתף אותה ידנית.
 *
 * Shizuku לא שורד אתחול - גם לא עדכון מערכת אוטומטי (נצפה בפועל
 * ב-2026-08-16: `reboot,ota`, בלי שהמשתמש יזם דבר) - ודורש הפעלה ידנית בכל
 * פעם. עד עכשיו התקלה הזו הייתה שקטה: הסריקה פשוט מנסה שוב מאוחר יותר בלי
 * שום סימן, וזה מה שהשאיר שיחות בלתי מיובאות במשך יומיים בלי שאיש ידע.
 * ראה notifyOutageOnce.
 */
object ShizukuAccess {

    const val PERMISSION_REQUEST_CODE = 4711

    private const val PREFS_NAME = "shizuku_access"
    private const val KEY_OUTAGE_NOTIFIED = "outage_notified"

    /** האם שירות Shizuku רץ כרגע במכשיר. */
    fun isAvailable(): Boolean = try {
        Shizuku.pingBinder()
    } catch (e: Throwable) {
        false
    }

    /**
     * מודיע ש-Shizuku לא זמין - פעם אחת בלבד לכל הפסקה, לא בכל ניסיון חוזר
     * (ראה ShizukuOutageTracker). קוראים בכל פעם שהזמינות נבדקת, גם כשהיא
     * true - זה מה שמנקה את הדגל ומאפשר להודיע שוב בפעם הבאה.
     */
    fun notifyOutageOnce(context: Context, available: Boolean) {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val alreadyNotified = prefs.getBoolean(KEY_OUTAGE_NOTIFIED, false)
        if (ShizukuOutageTracker.shouldNotify(available, alreadyNotified)) {
            NotificationHelper.notifyShizukuUnavailable(context)
        }
        prefs.edit()
            .putBoolean(KEY_OUTAGE_NOTIFIED, ShizukuOutageTracker.nextNotifiedFlag(available))
            .apply()
    }

    fun hasPermission(): Boolean = try {
        Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
    } catch (e: Throwable) {
        false
    }

    fun requestPermission() {
        runCatching { Shizuku.requestPermission(PERMISSION_REQUEST_CODE) }
    }

    /**
     * מריץ פקודת מעטפת בהרשאות shell ומחזיר את הפלט, או null אם Shizuku
     * לא זמינה/לא מורשית או שההרצה נכשלה.
     *
     * `newProcess` מסומן ב-Shizuku כ-API פנימי, ולכן נקרא ברפלקציה - קריאה
     * ישירה נכשלת בבדיקות ה-lint של הבנייה.
     */
    fun runCommand(command: String): String? {
        if (!isAvailable() || !hasPermission()) return null
        return try {
            val newProcess = Shizuku::class.java.getDeclaredMethod(
                "newProcess",
                Array<String>::class.java,
                Array<String>::class.java,
                String::class.java,
            ).apply { isAccessible = true }

            val process = newProcess.invoke(
                null, arrayOf("sh", "-c", command), null, null
            ) ?: return null

            val stream = process.javaClass
                .getMethod("getInputStream")
                .invoke(process) as InputStream
            val output = stream.bufferedReader().use { it.readText() }
            process.javaClass.getMethod("waitFor").invoke(process)
            output
        } catch (e: Throwable) {
            null
        }
    }
}
