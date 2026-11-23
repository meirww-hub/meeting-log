package com.meirww.meetingscribe.wear

import android.os.VibrationEffect
import android.os.Vibrator
import android.util.Log
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import com.meirww.meetingscribe.watch.WatchProtocol
import com.meirww.meetingscribe.watch.WatchState

/**
 * מקבל מהטלפון את מצב ההקלטה האמיתי, גם כשהאפליקציה בשעון סגורה, ושומר אותו
 * לתצוגה. הרטט הוא האישור הפיזי: הוא מופעל רק כשהטלפון באמת מדווח שההקלטה
 * התחילה (או שנכשלה) - כך אפשר להניח את היד ולדעת שההקלטה רצה.
 */
class StatusListenerService : WearableListenerService() {

    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != WatchProtocol.PATH_STATUS) return
        val status = WatchProtocol.decode(event.data)
        if (status == null) {
            Log.w(TAG, "ignored an unreadable status message")
            return
        }

        val before = StatusStore.snapshot(this).status?.state
        StatusStore.saveStatus(this, status)

        // רוטטים על מעבר מצב בלבד, לא על כל עדכון - ובלי להפריע כשהכול כבר ידוע.
        if (status.state != before) when (status.state) {
            WatchState.RECORDING -> buzz(VibrationEffect.createOneShot(180, 200))
            WatchState.FAILED -> buzz(
                VibrationEffect.createWaveform(longArrayOf(0, 120, 100, 120), -1)
            )
            WatchState.IDLE -> if (before != null) buzz(VibrationEffect.createOneShot(90, 150))
            WatchState.WAITING_MIC -> Unit
        }
    }

    private fun buzz(effect: VibrationEffect) {
        runCatching { getSystemService(Vibrator::class.java)?.vibrate(effect) }
    }

    private companion object {
        const val TAG = "StatusListener"
    }
}
