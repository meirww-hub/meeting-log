package com.meirww.meetingscribe

import android.content.Context
import androidx.appcompat.app.AppCompatDelegate

/** שומר את בחירת מצב כהה/בהיר של המשתמש ומחיל אותה דרך AppCompatDelegate. */
object ThemePrefs {
    private const val PREFS_NAME = "theme_prefs"
    private const val KEY_NIGHT_MODE = "night_mode"

    fun isNightMode(context: Context): Boolean =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getBoolean(KEY_NIGHT_MODE, true)

    fun setNightMode(context: Context, isNight: Boolean) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_NIGHT_MODE, isNight)
            .apply()
        applyNightMode(isNight)
    }

    fun applyNightMode(isNight: Boolean) {
        AppCompatDelegate.setDefaultNightMode(
            if (isNight) AppCompatDelegate.MODE_NIGHT_YES else AppCompatDelegate.MODE_NIGHT_NO
        )
    }
}
