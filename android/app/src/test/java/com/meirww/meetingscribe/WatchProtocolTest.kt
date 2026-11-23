package com.meirww.meetingscribe

import com.meirww.meetingscribe.watch.FailReason
import com.meirww.meetingscribe.watch.WatchProtocol
import com.meirww.meetingscribe.watch.WatchState
import com.meirww.meetingscribe.watch.WatchStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class WatchProtocolTest {

    @Test
    fun everyStateSurvivesARoundTrip() {
        for (state in WatchState.entries) {
            for (reason in FailReason.entries) {
                val status = WatchStatus(state, elapsedMs = 123_456L, pendingCount = 3, reason = reason)
                assertEquals(status, WatchProtocol.decode(WatchProtocol.encode(status)))
            }
        }
    }

    @Test
    fun nullAndEmptyPayloadsAreRejected() {
        assertNull(WatchProtocol.decode(null))
        assertNull(WatchProtocol.decode(ByteArray(0)))
    }

    @Test
    fun otherProtocolVersionIsRejected() {
        // גרסה אחרת של הפרוטוקול לא מפוענחת "בערך" - השעון מתעלם ממנה.
        assertNull(WatchProtocol.decode("v2|RECORDING|0|0|NONE".toByteArray()))
    }

    @Test
    fun missingOrExtraFieldsAreRejected() {
        assertNull(WatchProtocol.decode("v1|RECORDING|0|0".toByteArray()))
        assertNull(WatchProtocol.decode("v1|RECORDING|0|0|NONE|extra".toByteArray()))
    }

    @Test
    fun unknownStateOrReasonIsRejectedNotGuessed() {
        // הודעה שלא מובנת אסור שתציג "מקליט".
        assertNull(WatchProtocol.decode("v1|PARTYING|0|0|NONE".toByteArray()))
        assertNull(WatchProtocol.decode("v1|RECORDING|0|0|BECAUSE".toByteArray()))
    }

    @Test
    fun nonNumericFieldsAreRejected() {
        assertNull(WatchProtocol.decode("v1|RECORDING|abc|0|NONE".toByteArray()))
        assertNull(WatchProtocol.decode("v1|RECORDING|0|many|NONE".toByteArray()))
    }

    @Test
    fun negativeNumbersAreClampedToZero() {
        val decoded = WatchProtocol.decode("v1|RECORDING|-5|-2|NONE".toByteArray())
        assertEquals(WatchStatus(WatchState.RECORDING, 0L, 0, FailReason.NONE), decoded)
    }

    @Test
    fun pathsShareThePrefixTheManifestFiltersOn() {
        for (path in listOf(
            WatchProtocol.PATH_START,
            WatchProtocol.PATH_STOP,
            WatchProtocol.PATH_QUERY,
            WatchProtocol.PATH_STATUS,
        )) {
            assert(path.startsWith(WatchProtocol.PATH_PREFIX)) { "$path is outside the filter" }
        }
    }
}
