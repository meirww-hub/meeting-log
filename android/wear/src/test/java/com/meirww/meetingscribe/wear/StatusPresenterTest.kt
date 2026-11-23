package com.meirww.meetingscribe.wear

import com.meirww.meetingscribe.watch.FailReason
import com.meirww.meetingscribe.watch.WatchState
import com.meirww.meetingscribe.watch.WatchStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class StatusPresenterTest {

    private fun status(state: WatchState, elapsed: Long = 0L, pending: Int = 0, reason: FailReason = FailReason.NONE) =
        WatchStatus(state, elapsed, pending, reason)

    @Test
    fun noStatusYetMeansConnecting() {
        val ui = StatusPresenter.present(Snapshot(), now = 1_000)
        assertEquals(Headline.CONNECTING, ui.headline)
        assertEquals(Mode.START, ui.mode)
    }

    @Test
    fun pressingStartNeverShowsRecordingUntilThePhoneConfirms() {
        // נלחץ "התחל" ב-t=5000, והטלפון עוד לא ענה: מחכים, לא "מקליט".
        val snapshot = Snapshot(
            status = status(WatchState.IDLE),
            receivedAt = 1_000,
            startRequestedAt = 5_000,
        )
        val ui = StatusPresenter.present(snapshot, now = 6_000)
        assertEquals(Headline.STARTING, ui.headline)
        assertEquals(Mode.BUSY, ui.mode)
    }

    @Test
    fun phoneConfirmationTurnsIntoRecordingWithATimer() {
        val snapshot = Snapshot(
            status = status(WatchState.RECORDING, elapsed = 4_000),
            receivedAt = 8_000,
            startRequestedAt = 5_000,
        )
        val ui = StatusPresenter.present(snapshot, now = 9_000)
        assertEquals(Headline.RECORDING, ui.headline)
        assertEquals(Mode.STOP, ui.mode)
        // 4 שניות כבר עברו ברגע הקבלה: בסיס הטיימר = 8000 - 4000.
        assertEquals(4_000L, ui.timerBase)
    }

    @Test
    fun noAnswerAfterTheTimeoutIsReportedNotHiddenBehindASpinner() {
        val snapshot = Snapshot(
            status = status(WatchState.IDLE),
            receivedAt = 1_000,
            startRequestedAt = 5_000,
        )
        val ui = StatusPresenter.present(snapshot, now = 5_000 + StatusPresenter.ANSWER_TIMEOUT_MS)
        assertEquals(Headline.NO_REPLY, ui.headline)
        assertEquals(Mode.START, ui.mode) // אפשר לנסות שוב
    }

    @Test
    fun noConnectedPhoneIsReportedImmediately() {
        val snapshot = Snapshot(
            status = status(WatchState.IDLE),
            receivedAt = 1_000,
            startRequestedAt = 5_000,
            noPhoneAt = 5_100,
        )
        assertEquals(Headline.NO_PHONE, StatusPresenter.present(snapshot, now = 5_200).headline)
    }

    @Test
    fun aNewerPhoneStatusClearsTheNoPhoneMessage() {
        val snapshot = Snapshot(
            status = status(WatchState.IDLE),
            receivedAt = 9_000,
            noPhoneAt = 5_100,
        )
        assertEquals(Headline.READY, StatusPresenter.present(snapshot, now = 9_500).headline)
    }

    @Test
    fun stopIsBusyUntilThePhoneReportsIdle() {
        val snapshot = Snapshot(
            status = status(WatchState.RECORDING, elapsed = 60_000),
            receivedAt = 2_000,
            startRequestedAt = 1_000,
            stopRequestedAt = 70_000,
        )
        val ui = StatusPresenter.present(snapshot, now = 71_000)
        assertEquals(Headline.STOPPING, ui.headline)
        assertEquals(Mode.BUSY, ui.mode)
    }

    @Test
    fun savedIsShownOnlyAfterTheStopWasConfirmedWithThePendingCount() {
        val snapshot = Snapshot(
            status = status(WatchState.IDLE, pending = 2),
            receivedAt = 72_000,
            startRequestedAt = 1_000,
            stopRequestedAt = 70_000,
        )
        val ui = StatusPresenter.present(snapshot, now = 73_000)
        assertEquals(Headline.SAVED, ui.headline)
        assertEquals(2, ui.pendingCount)
        assertNull(ui.timerBase)
    }

    @Test
    fun waitingForTheMicrophoneIsNotShownAsPlainRecording() {
        val snapshot = Snapshot(
            status = status(WatchState.WAITING_MIC, elapsed = 10_000),
            receivedAt = 3_000,
        )
        val ui = StatusPresenter.present(snapshot, now = 4_000)
        assertEquals(Headline.WAITING_MIC, ui.headline)
        assertEquals(Mode.STOP, ui.mode)
    }

    @Test
    fun everyFailureReasonGetsItsOwnMessage() {
        val expected = mapOf(
            FailReason.NO_MIC_PERMISSION to Headline.FAIL_MIC_PERMISSION,
            FailReason.NO_OVERLAY_PERMISSION to Headline.FAIL_OVERLAY_PERMISSION,
            FailReason.START_TIMEOUT to Headline.FAIL_TIMEOUT,
            FailReason.START_FAILED to Headline.FAIL_START,
        )
        for ((reason, headline) in expected) {
            val snapshot = Snapshot(
                status = status(WatchState.FAILED, reason = reason),
                receivedAt = 8_000,
                startRequestedAt = 5_000,
            )
            val ui = StatusPresenter.present(snapshot, now = 9_000)
            assertEquals(headline, ui.headline)
            assertEquals(Mode.START, ui.mode) // אפשר לנסות שוב אחרי תיקון
        }
    }

    @Test
    fun aFailureStatusFromBeforeTheLatestTapDoesNotShowAsTheAnswer() {
        val snapshot = Snapshot(
            status = status(WatchState.FAILED, reason = FailReason.NO_OVERLAY_PERMISSION),
            receivedAt = 2_000,
            startRequestedAt = 5_000,
        )
        assertEquals(Headline.STARTING, StatusPresenter.present(snapshot, now = 6_000).headline)
    }
}
