package com.meirww.meetingscribe

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.MediaPlayer
import android.util.Log
import java.io.File

/**
 * נגן ההקלטות של האפליקציה: מנגן קובץ מקומי אחד או שניים במקביל, מנקודת זמן
 * מבוקשת.
 *
 * שניים ולא אחד, כי שיחת טלפון שיובאה מ-cally מוקלטת בשני ערוצים מבודדים -
 * הצד שלי בקובץ אחד והצד השני בקובץ אחר (ראה process_call_recording בשרת).
 * ניגון של אחד מהם בלבד היה משמיע חצי שיחה: שאלה בלי תשובה. שניהם מתחילים
 * באותו רגע בזמן השיחה, ולכן ניגון מסונכרן שלהם הוא השיחה המלאה.
 *
 * כל הקבצים הם קבצים מקומיים (ראה [AudioCache]) - כך הקפיצה לדקה מבוקשת
 * מיידית, ולא תלויה ברשת.
 */
class RecordingPlayer(private val context: Context) {

    private companion object {
        const val TAG = "RecordingPlayer"
    }

    private val players = mutableListOf<MediaPlayer>()
    private var focusRequest: AudioFocusRequest? = null

    /** נקרא כשההקלטה הסתיימה, וכשמערכת ההפעלה לקחה את המיקוד הקולי. */
    var onPlaybackEnded: (() -> Unit)? = null
    var onPausedByFocusLoss: (() -> Unit)? = null

    val isLoaded: Boolean get() = players.isNotEmpty()

    val isPlaying: Boolean
        get() = runCatching { players.firstOrNull()?.isPlaying == true }.getOrDefault(false)

    val durationMs: Int
        get() = runCatching { players.firstOrNull()?.duration ?: 0 }.getOrDefault(0)

    val positionMs: Int
        get() = runCatching { players.firstOrNull()?.currentPosition ?: 0 }.getOrDefault(0)

    private val audioAttributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()

    /**
     * טוען את הערוצים ומעמיד אותם על [startMs]. חוסם (prepare) - יש לקרוא
     * מ-Dispatchers.IO. מחזיר false אם אף ערוץ לא נטען.
     */
    fun load(files: List<File>, startMs: Int): Boolean {
        release()
        for (file in files) {
            try {
                val player = MediaPlayer().apply {
                    setAudioAttributes(audioAttributes)
                    setDataSource(file.absolutePath)
                    prepare()
                }
                players.add(player)
            } catch (e: Exception) {
                // ערוץ פגום לא מפיל את הניגון: עדיף חצי שיחה מכלום.
                Log.w(TAG, "load: cannot play ${file.name}", e)
            }
        }
        if (players.isEmpty()) return false

        players.first().setOnCompletionListener {
            pause()
            onPlaybackEnded?.invoke()
        }
        seekTo(startMs)
        return true
    }

    fun seekTo(positionMs: Int) {
        val target = positionMs.coerceIn(0, durationMs)
        players.forEach {
            // SEEK_CLOSEST ולא ברירת המחדל: ברירת המחדל קופצת לפריים המפתח
            // הקרוב, וכאן מבקשים רגע מדויק שצוטט בצ'אט.
            runCatching { it.seekTo(target.toLong(), MediaPlayer.SEEK_CLOSEST) }
        }
    }

    /** מתחיל/ממשיך ניגון. מחזיר false אם לא התקבל מיקוד קולי. */
    fun play(): Boolean {
        if (players.isEmpty()) return false
        if (!requestFocus()) return false
        players.forEach { runCatching { it.start() } }
        return true
    }

    fun pause() {
        players.forEach { runCatching { if (it.isPlaying) it.pause() } }
        abandonFocus()
    }

    fun release() {
        players.forEach { runCatching { it.release() } }
        players.clear()
        abandonFocus()
    }

    private fun requestFocus(): Boolean {
        // כבר יש מיקוד (המשך אחרי השהיה) - בקשה שנייה רק הייתה מייצרת בקשה
        // שאיש כבר לא משחרר.
        if (focusRequest != null) return true

        val manager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
            .setAudioAttributes(audioAttributes)
            .setOnAudioFocusChangeListener { change ->
                if (change == AudioManager.AUDIOFOCUS_LOSS ||
                    change == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT
                ) {
                    pause()
                    onPausedByFocusLoss?.invoke()
                }
            }
            .build()
        focusRequest = request
        return manager.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
    }

    private fun abandonFocus() {
        val request = focusRequest ?: return
        focusRequest = null
        val manager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        manager.abandonAudioFocusRequest(request)
    }
}
