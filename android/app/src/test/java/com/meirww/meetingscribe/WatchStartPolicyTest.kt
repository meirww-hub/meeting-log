package com.meirww.meetingscribe

import com.meirww.meetingscribe.WatchStartPolicy.Decision
import com.meirww.meetingscribe.watch.FailReason
import org.junit.Assert.assertEquals
import org.junit.Test

class WatchStartPolicyTest {

    @Test
    fun launchesTheStarterWhenEverythingIsInPlace() {
        assertEquals(
            Decision.LaunchStarter,
            WatchStartPolicy.decide(isRecording = false, hasMicPermission = true, canDrawOverlays = true),
        )
    }

    @Test
    fun aWatchTapNeverStartsASecondRecording() {
        // גם אם חסרות הרשאות - הקלטה שכבר רצה מנצחת, ולא נפתח כלום.
        assertEquals(
            Decision.AlreadyRecording,
            WatchStartPolicy.decide(isRecording = true, hasMicPermission = true, canDrawOverlays = true),
        )
        assertEquals(
            Decision.AlreadyRecording,
            WatchStartPolicy.decide(isRecording = true, hasMicPermission = false, canDrawOverlays = false),
        )
    }

    @Test
    fun refusesWithoutMicPermission() {
        assertEquals(
            Decision.Refuse(FailReason.NO_MIC_PERMISSION),
            WatchStartPolicy.decide(isRecording = false, hasMicPermission = false, canDrawOverlays = true),
        )
    }

    @Test
    fun refusesWithoutOverlayPermissionInsteadOfFailingSilently() {
        assertEquals(
            Decision.Refuse(FailReason.NO_OVERLAY_PERMISSION),
            WatchStartPolicy.decide(isRecording = false, hasMicPermission = true, canDrawOverlays = false),
        )
    }

    @Test
    fun micPermissionIsReportedBeforeOverlayPermission() {
        // שתיהן חסרות: מבקשים קודם את המיקרופון, הכי בסיסי.
        assertEquals(
            Decision.Refuse(FailReason.NO_MIC_PERMISSION),
            WatchStartPolicy.decide(isRecording = false, hasMicPermission = false, canDrawOverlays = false),
        )
    }
}
