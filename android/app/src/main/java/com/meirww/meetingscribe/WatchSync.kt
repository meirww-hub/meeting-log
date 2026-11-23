package com.meirww.meetingscribe

import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.Wearable
import com.meirww.meetingscribe.watch.WatchProtocol
import com.meirww.meetingscribe.watch.WatchStatus

/**
 * משדר לשעון המחובר את מצב ההקלטה האמיתי. הטלפון הוא מקור האמת: השעון
 * מציג רק מה ששודר מכאן, כך שהוא לא יכול להציג "מקליט" כשהמיקרופון בפועל
 * לא נפתח.
 *
 * כל כשל כאן שקט בכוונה (אין שעון מחובר, אין Play Services של Wear): ההקלטה
 * לא תלויה בשעון, והשעון הוא רק חלון אליה.
 */
object WatchSync {

    private const val TAG = "WatchSync"

    fun publish(context: Context, status: WatchStatus) {
        val app = context.applicationContext
        val payload = WatchProtocol.encode(status)
        runCatching {
            Wearable.getNodeClient(app).connectedNodes
                .addOnSuccessListener { nodes ->
                    for (node in nodes) {
                        Wearable.getMessageClient(app)
                            .sendMessage(node.id, WatchProtocol.PATH_STATUS, payload)
                            .addOnFailureListener { Log.w(TAG, "status to ${node.displayName} failed", it) }
                    }
                }
                .addOnFailureListener { Log.d(TAG, "no watch to publish to: ${it.message}") }
        }.onFailure { Log.d(TAG, "wearable API unavailable: ${it.message}") }
    }
}
