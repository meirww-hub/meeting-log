package com.meirww.meetingscribe

import android.app.Application

class MeetingScribeApp : Application() {
    override fun onCreate() {
        super.onCreate()
        ThemePrefs.applyNightMode(ThemePrefs.isNightMode(this))
    }
}
