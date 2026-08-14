package com.meirww.meetingscribe

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

/**
 * התראה כשהקלטה סיימה להתעבד ונכנסה ללוג - עם עדיפות גבוהה (heads-up) כדי
 * שהמשתמש ישים לב גם אם הטלפון בכיס. הצליל הוא צליל ההתראה הרגיל של המכשיר
 * (לא צליל שיחה נכנסת - זה בכוונה, כדי לא להתבלבל עם שיחה אמיתית).
 *
 * שם הערוץ כולל "_v2" כי הגדרות צליל של NotificationChannel ננעלות אחרי
 * היצירה הראשונה - שינוי הקוד בלבד לא היה משנה את הצליל אצל מי שכבר קיבל
 * את הערוץ הישן עם צליל הרינגטון.
 */
object NotificationHelper {

    private const val CHANNEL_ID = "recording_ready_v2"

    /** ראה [notifyRecordingTooShort] - התראה יחידה שמתעדכנת, לא ערימה. */
    private const val TOO_SHORT_NOTIFICATION_ID = 9101

    private fun ensureChannel(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return

        val notificationSoundUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
        val audioAttributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_NOTIFICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build()

        val channel = NotificationChannel(
            CHANNEL_ID, "הקלטה מוכנה", NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "התראה כשהקלטה סיימה להתעבד ונכנסה להיסטוריה"
            enableVibration(true)
            setSound(notificationSoundUri, audioAttributes)
        }
        manager.createNotificationChannel(channel)
    }

    private fun notify(context: Context, id: Int, contentTitle: String, contentText: String) {
        ensureChannel(context)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ActivityCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) return

        val openIntent = Intent(context, HistoryActivity::class.java)
            .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val pendingIntent = PendingIntent.getActivity(
            context, id, openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle(contentTitle)
            .setContentText(contentText)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(pendingIntent)
            .build()

        NotificationManagerCompat.from(context).notify(id, notification)
    }

    fun notifyRecordingReady(context: Context, recordingId: String, title: String) {
        notify(
            context, recordingId.hashCode(),
            "ההקלטה מוכנה", title.ifBlank { "ההקלטה עובדה בהצלחה" }
        )
    }

    /**
     * הקלטה שלא נשלחה לעיבוד כי היא קצרה מהמינימום (ראה AudioDuration).
     * מודיעים כדי שהיא לא תיעלם בשקט - המשתמש לחץ "הקלט" בכוונה, ובלי הודעה
     * הוא היה מחכה לסיכום שלא יגיע לעולם. ID קבוע: כמה הקלטות קצרות ברצף
     * מחליפות זו את זו במקום לערום התראות.
     */
    fun notifyRecordingTooShort(context: Context) {
        notify(
            context, TOO_SHORT_NOTIFICATION_ID,
            "ההקלטה לא נשלחה לעיבוד",
            "הקלטות קצרות מ-${AudioDuration.MIN_PROCESSING_MINUTES} דקות אינן מעובדות"
        )
    }

    /**
     * ההפניה להיסטוריה נכונה רק מאז שהמסך מציג גם הקלטות שנכשלו: עד
     * 2026-08-13 הרשימה סיננה "done" בלבד, וההתראה שלחה את המשתמש בדיוק
     * למקום היחיד שבו ההקלטה לא הופיעה. ראה firestore_store.list_recordings.
     */
    fun notifyRecordingFailed(context: Context, recordingId: String, label: String) {
        val what = label.ifBlank { "ההקלטה" }
        notify(
            context, recordingId.hashCode(),
            "עיבוד ההקלטה נכשל",
            "$what - פתחו את ההיסטוריה כדי לנסות שוב"
        )
    }
}
