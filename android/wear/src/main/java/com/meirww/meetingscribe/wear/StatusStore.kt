package com.meirww.meetingscribe.wear

import android.content.Context
import android.content.SharedPreferences
import android.os.SystemClock
import androidx.wear.tiles.TileService
import com.meirww.meetingscribe.watch.WatchProtocol
import com.meirww.meetingscribe.watch.WatchStatus

/**
 * המצב האחרון שהטלפון שידר + מתי השעון ביקש פעולה. נשמר ב-SharedPreferences
 * כדי לשרוד הריגת תהליך; המסך והאריח מתעדכנים מכאן.
 */
object StatusStore {

    private const val PREFS = "status"
    private const val KEY_STATUS = "status"
    private const val KEY_RECEIVED_AT = "received_at"
    private const val KEY_START_AT = "start_at"
    private const val KEY_STOP_AT = "stop_at"
    private const val KEY_NO_PHONE_AT = "no_phone_at"

    private fun prefs(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /**
     * לוקח צילום של המצב. זמנים ששמורים מהעתיד (השעון אותחל מאז, ו-
     * elapsedRealtime התאפס) מבוטלים: הם היו גורמים ל"מתחיל..." לנצח.
     */
    fun snapshot(context: Context): Snapshot {
        val p = prefs(context)
        val now = SystemClock.elapsedRealtime()
        fun stamp(key: String) = p.getLong(key, 0L).takeIf { it in 1..now } ?: 0L

        val receivedAt = stamp(KEY_RECEIVED_AT)
        val status = if (receivedAt > 0) {
            WatchProtocol.decode(p.getString(KEY_STATUS, null)?.toByteArray(Charsets.UTF_8))
        } else null

        return Snapshot(
            status = status,
            receivedAt = receivedAt,
            startRequestedAt = stamp(KEY_START_AT),
            stopRequestedAt = stamp(KEY_STOP_AT),
            noPhoneAt = stamp(KEY_NO_PHONE_AT),
        )
    }

    fun saveStatus(context: Context, status: WatchStatus) {
        prefs(context).edit()
            .putString(KEY_STATUS, String(WatchProtocol.encode(status), Charsets.UTF_8))
            .putLong(KEY_RECEIVED_AT, SystemClock.elapsedRealtime())
            .commit()
        refreshTile(context)
    }

    fun markStartRequested(context: Context) {
        prefs(context).edit().putLong(KEY_START_AT, SystemClock.elapsedRealtime()).commit()
        refreshTile(context)
    }

    fun markStopRequested(context: Context) {
        prefs(context).edit().putLong(KEY_STOP_AT, SystemClock.elapsedRealtime()).commit()
        refreshTile(context)
    }

    fun markNoPhone(context: Context) {
        prefs(context).edit().putLong(KEY_NO_PHONE_AT, SystemClock.elapsedRealtime()).commit()
        refreshTile(context)
    }

    fun addChangeListener(
        context: Context,
        listener: SharedPreferences.OnSharedPreferenceChangeListener,
    ) = prefs(context).registerOnSharedPreferenceChangeListener(listener)

    fun removeChangeListener(
        context: Context,
        listener: SharedPreferences.OnSharedPreferenceChangeListener,
    ) = prefs(context).unregisterOnSharedPreferenceChangeListener(listener)

    private fun refreshTile(context: Context) {
        runCatching {
            TileService.getUpdater(context.applicationContext)
                .requestUpdate(RecordingTileService::class.java)
        }
    }
}
