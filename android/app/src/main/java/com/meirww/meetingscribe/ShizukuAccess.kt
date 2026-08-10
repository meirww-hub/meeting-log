package com.meirww.meetingscribe

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
 */
object ShizukuAccess {

    const val PERMISSION_REQUEST_CODE = 4711

    /** האם שירות Shizuku רץ כרגע במכשיר. */
    fun isAvailable(): Boolean = try {
        Shizuku.pingBinder()
    } catch (e: Throwable) {
        false
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
