package com.meirww.meetingscribe

import android.app.Application

class MeetingScribeApp : Application() {
    override fun onCreate() {
        super.onCreate()
        ThemePrefs.applyNightMode(ThemePrefs.isNightMode(this))

        // כל הפעלה של התהליך - פתיחת האפליקציה, אבל גם התעוררות לשידור
        // כמו סיום שיחה - מנסה להשלים הקלטות שנתקעו על המכשיר. ראה
        // RecordingRecovery.
        RecordingRecovery.sweep(this)
    }
}
