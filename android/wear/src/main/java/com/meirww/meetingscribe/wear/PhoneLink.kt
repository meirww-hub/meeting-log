package com.meirww.meetingscribe.wear

import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.Wearable
import com.meirww.meetingscribe.watch.WatchProtocol

/** שולח פקודות לטלפון המצומד, ורושם כשאין טלפון להגיע אליו. */
object PhoneLink {

    private const val TAG = "PhoneLink"

    fun requestStart(context: Context) {
        StatusStore.markStartRequested(context)
        send(context, WatchProtocol.PATH_START)
    }

    fun requestStop(context: Context) {
        StatusStore.markStopRequested(context)
        send(context, WatchProtocol.PATH_STOP)
    }

    /** "מה המצב?" - מסנכרן את המסך עם הטלפון (למשל אחרי שההקלטה התחילה מהטלפון). */
    fun requestStatus(context: Context) = send(context, WatchProtocol.PATH_QUERY)

    private fun send(context: Context, path: String) {
        val app = context.applicationContext
        Wearable.getNodeClient(app).connectedNodes
            .addOnSuccessListener { nodes ->
                if (nodes.isEmpty()) {
                    Log.w(TAG, "$path: no connected phone")
                    StatusStore.markNoPhone(app)
                    return@addOnSuccessListener
                }
                for (node in nodes) {
                    Wearable.getMessageClient(app)
                        .sendMessage(node.id, path, ByteArray(0))
                        .addOnFailureListener {
                            Log.w(TAG, "$path to ${node.displayName} failed", it)
                            StatusStore.markNoPhone(app)
                        }
                }
            }
            .addOnFailureListener {
                Log.w(TAG, "$path: node lookup failed", it)
                StatusStore.markNoPhone(app)
            }
    }
}
