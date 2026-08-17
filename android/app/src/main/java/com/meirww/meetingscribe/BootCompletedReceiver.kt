package com.meirww.meetingscribe

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * מפעיל סריקת ייבוא שיחות זמן קצר אחרי שהטלפון עולה, במקום לחכות לשיחה
 * הבאה כדי לגלות ש-Shizuku נפל.
 *
 * Shizuku לא שורד אתחול - גם לא עדכון מערכת אוטומטי (ראה ShizukuAccess) -
 * ובלי הבודק הזה הפער בין אתחול לגילוי שהייבוא מושבת תלוי לגמרי בשיחה
 * הבאה. בפועל זה נמשך יומיים.
 */
class BootCompletedReceiver : BroadcastReceiver() {

    private companion object {
        // מרווח קטן, כדי לתת סיכוי אמיתי ל-Shizuku (מצב TCP) להתחבר מחדש
        // לבד לפני שמדווחים על תקלה.
        const val DELAY_SECONDS = 90L
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        CallImportWorker.schedule(context.applicationContext, delaySeconds = DELAY_SECONDS)
    }
}
