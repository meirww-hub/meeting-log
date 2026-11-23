package com.meirww.meetingscribe.wear

import com.meirww.meetingscribe.watch.FailReason
import com.meirww.meetingscribe.watch.WatchState
import com.meirww.meetingscribe.watch.WatchStatus

/**
 * מה ששמור על השעון: המצב האחרון שהטלפון שידר, ומתי השעון ביקש פעולה.
 * כל הזמנים הם SystemClock.elapsedRealtime() של השעון עצמו - לא שעון קיר,
 * כדי שסטייה בין שני המכשירים לא תשבש את התצוגה.
 */
data class Snapshot(
    val status: WatchStatus? = null,
    val receivedAt: Long = 0L,
    val startRequestedAt: Long = 0L,
    val stopRequestedAt: Long = 0L,
    val noPhoneAt: Long = 0L,
)

enum class Mode {
    /** כפתור התחלה פעיל. */
    START,

    /** כפתור עצירה פעיל. */
    STOP,

    /** ממתינים לטלפון - הכפתור מושבת כדי שלא יישלחו פקודות כפולות. */
    BUSY,
}

enum class Headline {
    CONNECTING,
    STARTING,
    STOPPING,
    RECORDING,
    WAITING_MIC,
    READY,
    SAVED,
    NO_PHONE,
    NO_REPLY,
    FAIL_MIC_PERMISSION,
    FAIL_OVERLAY_PERMISSION,
    FAIL_TIMEOUT,
    FAIL_START,
}

data class UiState(
    val mode: Mode,
    val headline: Headline,
    val pendingCount: Int = 0,
    /** בסיס ל-Chronometer (elapsedRealtime של השעון); null כשאין הקלטה. */
    val timerBase: Long? = null,
)

/**
 * מתרגם את מה ששמור למה שמוצג. לוגיקה טהורה - בלי Android - כדי שאפשר
 * לבדוק אותה, וכי כל הכללים של "מתי כתוב מקליט" נמצאים במקום אחד:
 * "מקליט" מוצג **רק** אחרי שהטלפון אישר זאת, אף פעם לא בגלל שנלחץ כפתור.
 */
object StatusPresenter {

    /** כמה ממתינים לתשובת הטלפון לפני שמודיעים שהוא לא ענה. */
    const val ANSWER_TIMEOUT_MS = 12_000L

    fun present(snapshot: Snapshot, now: Long): UiState {
        val status = snapshot.status
        val actionAt = maxOf(snapshot.startRequestedAt, snapshot.stopRequestedAt)
        val recordingKnown = status?.state == WatchState.RECORDING ||
            status?.state == WatchState.WAITING_MIC
        val idleMode = if (recordingKnown) Mode.STOP else Mode.START
        val pending = status?.pendingCount ?: 0

        // הטלפון לא נמצא (אין שעון-טלפון מחוברים) - בשני המקרים: אחרי לחיצה,
        // או כשרק ביקשנו לדעת את המצב.
        if (snapshot.noPhoneAt > 0 && snapshot.noPhoneAt >= maxOf(actionAt, snapshot.receivedAt)) {
            return UiState(idleMode, Headline.NO_PHONE, pending, timerBase(snapshot))
        }

        val answered = status != null && snapshot.receivedAt >= actionAt
        if (actionAt > 0 && !answered) {
            val starting = snapshot.startRequestedAt >= snapshot.stopRequestedAt
            return if (now - actionAt < ANSWER_TIMEOUT_MS) {
                UiState(
                    Mode.BUSY,
                    if (starting) Headline.STARTING else Headline.STOPPING,
                    pending,
                    timerBase(snapshot),
                )
            } else {
                UiState(idleMode, Headline.NO_REPLY, pending, timerBase(snapshot))
            }
        }

        return when (status?.state) {
            null -> UiState(Mode.START, Headline.CONNECTING)
            WatchState.RECORDING ->
                UiState(Mode.STOP, Headline.RECORDING, pending, timerBase(snapshot))
            WatchState.WAITING_MIC ->
                UiState(Mode.STOP, Headline.WAITING_MIC, pending, timerBase(snapshot))
            WatchState.IDLE -> {
                val justSaved = snapshot.stopRequestedAt > 0 &&
                    snapshot.stopRequestedAt >= snapshot.startRequestedAt &&
                    snapshot.receivedAt >= snapshot.stopRequestedAt
                UiState(Mode.START, if (justSaved) Headline.SAVED else Headline.READY, pending)
            }
            WatchState.FAILED -> UiState(Mode.START, failHeadline(status.reason), pending)
        }
    }

    private fun failHeadline(reason: FailReason): Headline = when (reason) {
        FailReason.NO_MIC_PERMISSION -> Headline.FAIL_MIC_PERMISSION
        FailReason.NO_OVERLAY_PERMISSION -> Headline.FAIL_OVERLAY_PERMISSION
        FailReason.START_TIMEOUT -> Headline.FAIL_TIMEOUT
        FailReason.START_FAILED, FailReason.NONE -> Headline.FAIL_START
    }

    private fun timerBase(snapshot: Snapshot): Long? {
        val status = snapshot.status ?: return null
        if (status.state != WatchState.RECORDING && status.state != WatchState.WAITING_MIC) {
            return null
        }
        return snapshot.receivedAt - status.elapsedMs
    }
}
