package com.meirww.meetingscribe

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.Settings
import android.util.Log
import androidx.core.content.ContextCompat
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import com.meirww.meetingscribe.WatchStartPolicy.Decision
import com.meirww.meetingscribe.watch.WatchProtocol
import com.meirww.meetingscribe.watch.WatchState
import com.meirww.meetingscribe.watch.WatchStatus

/**
 * מקבל פקודות מהשעון המצומד. ההודעות מגיעות רק דרך Play Services ורק
 * משעון של אותו משתמש עם אותה אפליקציה (אותו package ואותה חתימה); השירות
 * והמחלקה מקבלת קריאות רק מ-Play Services עצמו (הרשאת BIND_LISTENER נוסתה והחסימה את Play Services עצמו).
 *
 * "התחל" פותח מיקרופון - הטריגר היחיד מלבד כפתור ההקלטה באפליקציה, באישור
 * מפורש של המשתמש (2026-11-23). הוא לא יכול להתחיל ישירות מכאן כי מהרקע
 * אנדרואיד 14 חוסם שירות מיקרופון; ראה [WatchStartPolicy] ו-[WatchStartActivity].
 */
class WatchMessageListenerService : WearableListenerService() {

    override fun onMessageReceived(event: MessageEvent) {
        Log.i(TAG, "message ${event.path} from ${event.sourceNodeId}")
        when (event.path) {
            WatchProtocol.PATH_QUERY -> publishCurrentState()
            WatchProtocol.PATH_STOP -> handleStop()
            WatchProtocol.PATH_START -> handleStart()
            else -> Log.w(TAG, "ignored unknown path ${event.path}")
        }
    }

    private fun handleStart() {
        val decision = WatchStartPolicy.decide(
            isRecording = RecordingService.isRecording,
            hasMicPermission = ContextCompat.checkSelfPermission(
                this, Manifest.permission.RECORD_AUDIO
            ) == PackageManager.PERMISSION_GRANTED,
            canDrawOverlays = Settings.canDrawOverlays(this),
        )
        when (decision) {
            Decision.AlreadyRecording -> publishCurrentState()
            is Decision.Refuse -> WatchSync.publish(
                this, WatchStatus(WatchState.FAILED, reason = decision.reason)
            )
            Decision.LaunchStarter -> runCatching {
                startActivity(
                    Intent(this, WatchStartActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            }.onFailure { Log.e(TAG, "cannot open the starter screen", it) }
        }
    }

    private fun handleStop() {
        // עצירה לא פותחת מיקרופון, ולכן היא עוברת ישירות לשירות שכבר רץ.
        if (RecordingService.isRecording) RecordingService.stop(applicationContext)
        else publishCurrentState()
    }

    private fun publishCurrentState() {
        WatchSync.publish(this, RecordingService.currentWatchStatus(applicationContext))
    }

    private companion object {
        const val TAG = "WatchListener"
    }
}
