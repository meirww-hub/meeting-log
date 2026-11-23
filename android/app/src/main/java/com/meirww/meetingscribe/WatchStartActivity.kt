package com.meirww.meetingscribe

import android.app.Activity
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import com.meirww.meetingscribe.watch.FailReason
import com.meirww.meetingscribe.watch.WatchState
import com.meirww.meetingscribe.watch.WatchStatus

/**
 * מסך שקוף וקצר שמתחיל הקלטה בשם השעון.
 *
 * אנדרואיד 14 לא מאפשר לפתוח מיקרופון משירות ברקע, אבל כן מכבד אפליקציה
 * ש-Activity שלה גלויה. לכן הטלפון (שהשעון הורה לו להתחיל) פותח את המסך הזה -
 * גם כשהמכשיר נעול (showWhenLocked) וגם כשהמסך כבוי (turnScreenOn) - ומתוכו
 * מתחיל את [RecordingService] בדיוק כמו כפתור ההקלטה, ואז נסגר.
 *
 * ההקלטה עצמה והמצב ששודר לשעון הם של [RecordingService]; המסך הזה אחראי רק
 * על המקרה שבו ההתחלה לא קרתה - אז הוא מודיע לשעון, במקום להשאיר אותו מחכה.
 */
class WatchStartActivity : Activity() {

    private val handler = Handler(Looper.getMainLooper())
    private var requested = false

    private val finishWhenRecording = object : Runnable {
        override fun run() {
            when {
                RecordingService.isRecording -> finishQuietly()
                SystemClock.elapsedRealtime() - startedAt > START_TIMEOUT_MS -> {
                    Log.e(TAG, "recording did not start within ${START_TIMEOUT_MS}ms")
                    WatchSync.publish(
                        applicationContext,
                        WatchStatus(WatchState.FAILED, reason = FailReason.START_TIMEOUT),
                    )
                    finishQuietly()
                }
                else -> handler.postDelayed(this, POLL_MS)
            }
        }
    }

    private var startedAt = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
    }

    /**
     * ההתחלה ב-onResume ולא ב-onCreate: רק כשה-Activity באמת בחזית האפליקציה
     * נחשבת "גלויה", וזה התנאי שמאפשר לפתוח מיקרופון.
     */
    override fun onResume() {
        super.onResume()
        if (requested) return
        requested = true
        startedAt = SystemClock.elapsedRealtime()
        RecordingService.start(applicationContext)
        handler.post(finishWhenRecording)
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    private fun finishQuietly() {
        finish()
        @Suppress("DEPRECATION")
        overridePendingTransition(0, 0)
    }

    private companion object {
        const val TAG = "WatchStart"
        const val START_TIMEOUT_MS = 6_000L
        const val POLL_MS = 150L
    }
}
