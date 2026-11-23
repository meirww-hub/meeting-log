package com.meirww.meetingscribe.wear

import android.content.Context

/** מיפוי כותרות המצב למחרוזות - נפרד מ-[StatusPresenter] כדי שהוא יישאר טהור. */
object Texts {

    fun headline(headline: Headline): Int = when (headline) {
        Headline.CONNECTING -> R.string.headline_connecting
        Headline.STARTING -> R.string.headline_starting
        Headline.STOPPING -> R.string.headline_stopping
        Headline.RECORDING -> R.string.headline_recording
        Headline.WAITING_MIC -> R.string.headline_waiting_mic
        Headline.READY -> R.string.headline_ready
        Headline.SAVED -> R.string.headline_saved
        Headline.NO_PHONE -> R.string.headline_no_phone
        Headline.NO_REPLY -> R.string.headline_no_reply
        Headline.FAIL_MIC_PERMISSION -> R.string.headline_fail_mic_permission
        Headline.FAIL_OVERLAY_PERMISSION -> R.string.headline_fail_overlay_permission
        Headline.FAIL_TIMEOUT -> R.string.headline_fail_timeout
        Headline.FAIL_START -> R.string.headline_fail_start
    }

    /** "2 ממתינות לשליחה" - ריק כשאין הקלטות שממתינות. */
    fun pending(context: Context, count: Int): String =
        if (count <= 0) "" else context.getString(R.string.pending_to_send, count)
}
