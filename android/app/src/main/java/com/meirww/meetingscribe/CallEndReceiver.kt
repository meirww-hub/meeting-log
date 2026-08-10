package com.meirww.meetingscribe

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.TelephonyManager

/**
 * מזהה סיום שיחת טלפון ומפעיל את סריקת ההקלטות של cally.
 *
 * מצב IDLE מתקבל גם על שיחות שלא נענו, ולכן הסריקה עצמה אחראית לזהות אם
 * באמת נוספה הקלטה חדשה (ראה CallImportWorker).
 */
class CallEndReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != TelephonyManager.ACTION_PHONE_STATE_CHANGED) return
        val state = intent.getStringExtra(TelephonyManager.EXTRA_STATE) ?: return
        if (state == TelephonyManager.EXTRA_STATE_IDLE) {
            CallImportWorker.schedule(context.applicationContext)
        }
    }
}
