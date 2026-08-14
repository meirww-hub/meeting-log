package com.meirww.meetingscribe

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import java.io.File
import java.util.Locale

/**
 * מקליט עמיד להפרעות: כותב את הפגישה כרצף קטעים בתוך תיקיית session אחת,
 * ומתאושש לבד מכל אירוע שחוטף את המיקרופון.
 *
 * למה לא MediaRecorder אחד לכל הפגישה: כשמגיעה שיחה נכנסת, מערכת הטלפוניה
 * (וגם cally שמקליטה את השיחה) מקבלות עדיפות על המיקרופון, ו-MediaRecorder של
 * אפליקציה רגילה מקבל שגיאה ומפסיק לכתוב. עד עכשיו זה הפיל את כל הפגישה:
 * הקובץ נשאר בלי טבלת אינדקס (moov) ולכן בלתי קריא, ו-stop() המאוחר זרק
 * חריגה שהפילה את השירות לפני שההקלטה בכלל נשלחה לשרת.
 *
 * עכשיו: כל אובדן מיקרופון סוגר את הקטע הנוכחי כקובץ תקין, ושומר בלולאה
 * (כל [RETRY_INTERVAL_MS]) לנסות להשיג את המיקרופון בחזרה - כלומר ברגע
 * שהשיחה מסתיימת ההקלטה מתחדשת מעצמה לקטע חדש, בלי מגע יד. הקטעים מאוחדים
 * לקובץ אחד בסיום (ראה [AudioSegmentMerger]).
 */
class RecordingManager(private val context: Context) {

    private companion object {
        const val TAG = "RecordingManager"

        /**
         * גלגול קטע יזום כל דקה. טבלת האינדקס (moov) נכתבת רק ב-stop() תקין,
         * ולכן קטע שנקטע באלימות - מיקרופון שנחטף לשיחה, או תהליך שנהרג -
         * אבוד. הגלגול חוסם את ההפסד לדקה האחרונה בלבד במקום לכל הפגישה.
         *
         * המחיר הוא רווח של כ-0.2 שניות בכל גלגול (סגירת מקליט ופתיחת חדש).
         * דקה נבחרה כאיזון: פחות מ-0.5% מזמן ההקלטה, מול חסימת הפסד לדקה.
         */
        const val SEGMENT_ROLL_MS = 60_000

        /** קצב ניסיונות חוזרים כשהמיקרופון תפוס (שיחה פעילה / cally מקליטה). */
        const val RETRY_INTERVAL_MS = 2_000L

        /**
         * כל כמה זמן מסומן שההקלטה חיה. צריך להיות קטן בהרבה מחלון החיים של
         * [RecordingSessionState], כדי שדופק אחד שהוחמץ לא ישחרר הקלטה פעילה.
         */
        const val HEARTBEAT_INTERVAL_MS = 30_000L

        /**
         * פרופיל דיבור: מונו, 16kHz, 48kbps. ההקלטה הייתה 44.1kHz ב-128kbps -
         * איכות מוזיקה שמייצרת כ-1MB לדקה, וכך פגישה מעל ~34 דקות עברה את
         * מגבלת 32MiB של Cloud Run ונדחתה ב-413 בלי שאיש ידע. 16kHz מונו הוא
         * גם מה ש-STT מצפה לו ממילא, כך שאין פגיעה בתמלול - רק פי 2.7 פחות
         * בתים.
         */
        const val SPEECH_BITRATE = 48_000
        const val SPEECH_SAMPLE_RATE = 16_000

        /** ברירת המחדל הישנה - נשמרת רק כרשת ביטחון, ראה [configure]. */
        const val LEGACY_BITRATE = 128_000
        const val LEGACY_SAMPLE_RATE = 44_100
    }

    /** עדכוני מצב לשירות, לצורך טקסט ההתראה שהמשתמש רואה. */
    interface Listener {
        /** המיקרופון נחטף (בדרך כלל שיחה נכנסת) - ההקלטה ממתינה ומנסה שוב. */
        fun onCaptureInterrupted()

        /** המיקרופון חזר וההקלטה ממשיכה לקטע חדש. */
        fun onCaptureResumed()
    }

    private val watchdogThread = HandlerThread("recording-watchdog").apply { start() }
    private val watchdogHandler = Handler(watchdogThread.looper)

    private val lock = Any()
    private var recorder: MediaRecorder? = null
    private var currentSegment: File? = null
    private var segmentIndex = 0

    /** נכתב בפתיחת ההקלטה ומשם והלאה רק מחוט הכלב השומר. */
    @Volatile
    private var lastHeartbeatMs = 0L

    @Volatile
    private var sessionDir: File? = null

    /** המשתמש ביקש להקליט - נשאר true גם בזמן הפסקה כפויה. */
    @Volatile
    private var sessionActive = false

    /** אנחנו באמת כותבים אודיו ברגע זה. */
    @Volatile
    var isCapturing = false
        private set

    private var listener: Listener? = null

    /** null = עוד לא ידוע. ראה [startSegment]. */
    @Volatile
    private var speechProfileSupported: Boolean? = null

    /**
     * פותח session חדש ומתחיל להקליט. מחזיר את תיקיית ה-session, או null רק
     * אם אי אפשר בכלל ליצור אותה (אין אחסון) - מיקרופון תפוס *אינו* כישלון:
     * ה-session נפתח, [Listener.onCaptureInterrupted] מדווח שממתינים, והכלב
     * השומר ימשיך לנסות עד שהמיקרופון יתפנה.
     */
    fun startRecording(listener: Listener? = null, resumeFrom: File? = null): File? {
        this.listener = listener

        val dir = resumeFrom?.takeIf { it.isDirectory } ?: File(
            context.getExternalFilesDir(RecordingRecovery.RECORDINGS_DIR),
            "${RecordingRecovery.SESSION_PREFIX}${System.currentTimeMillis()}"
        )
        if (!dir.mkdirs() && !dir.isDirectory) {
            Log.e(TAG, "startRecording: cannot create session dir ${dir.absolutePath}")
            return null
        }
        // לפני פתיחת המיקרופון, כדי שסריקת ההשלמה לא תראה תיקייה "נטושה".
        RecordingRecovery.markActive(dir)

        synchronized(lock) {
            sessionDir = dir
            // המשך לתוך session קיים (התהליך נהרג באמצע פגישה) חייב להמשיך את
            // המספור: אחרת הקטעים החדשים היו דורסים את מה שהוקלט לפני ההריגה.
            segmentIndex = lastSegmentIndex(dir)
        }
        sessionActive = true
        lastHeartbeatMs = System.currentTimeMillis()

        isCapturing = startSegment()
        if (!isCapturing) {
            Log.w(TAG, "startRecording: mic busy, waiting for it to free up")
            listener?.onCaptureInterrupted()
        }
        watchdogHandler.postDelayed(watchdog, RETRY_INTERVAL_MS)
        return dir
    }

    /**
     * סוגר את ההקלטה ומחזיר את תיקיית ה-session (הקטעים בתוכה, מוכנים
     * לאיחוד). לא זורק לעולם - כישלון סגירה של קטע בודד לא אמור למנוע
     * מהפגישה להישלח.
     */
    fun stopRecording(): File? {
        sessionActive = false
        isCapturing = false
        watchdogHandler.removeCallbacks(watchdog)
        finalizeSegment()
        listener = null
        return synchronized(lock) {
            val current = sessionDir
            sessionDir = null
            current
        }.also { RecordingRecovery.markActive(null) }
    }

    /** משחרר את חוט הכלב השומר. נקרא כשהשירות עצמו נהרס. */
    fun release() {
        sessionActive = false
        watchdogHandler.removeCallbacks(watchdog)
        finalizeSegment()
        watchdogThread.quitSafely()
    }

    /**
     * סוגר את הקטע הנוכחי ופותח חדש מיד. נקרא בסיום שיחה - גם אם אנדרואיד
     * לא זרק שגיאה אלא רק השתיק אותנו (isClientSilenced), פתיחה מחדש של
     * המיקרופון מבטיחה שממשיכים להקליט אודיו אמיתי ולא דממה.
     */
    fun rollSegment() {
        if (!sessionActive) return
        watchdogHandler.post {
            if (!sessionActive) return@post
            finalizeSegment()
            isCapturing = startSegment()
            if (!isCapturing) listener?.onCaptureInterrupted()
        }
    }

    /** עוצמת הקול הנוכחית (0..32767 בערך) - להזנת ויזואליזציית האקולייזר. */
    fun currentAmplitude(): Int = try {
        synchronized(lock) { recorder?.maxAmplitude } ?: 0
    } catch (e: Exception) {
        0
    }

    /**
     * רץ כל [RETRY_INTERVAL_MS] כל עוד ההקלטה פעילה. כשהמיקרופון תפוס
     * (שיחה) הניסיון נכשל שוב ושוב בשקט, וברגע שהשיחה נגמרת הוא מצליח
     * וההקלטה מתחדשת מעצמה.
     */
    private val watchdog = object : Runnable {
        override fun run() {
            if (!sessionActive) return
            if (!isCapturing) {
                if (startSegment()) {
                    isCapturing = true
                    Log.i(TAG, "watchdog: microphone reacquired, recording resumed")
                    listener?.onCaptureResumed()
                }
            }
            beat()
            watchdogHandler.postDelayed(this, RETRY_INTERVAL_MS)
        }
    }

    /**
     * דופק ל-[RecordingSessionState]: "ההקלטה הזו עדיין חיה". פועם גם כשהמיקרופון
     * תפוס בשיחה - הפגישה עצמה נמשכת, רק הכתיבה מושהית - וזה בדיוק מה שמאפשר
     * לשיחה ארוכה לא לשחרר את התיקייה לסריקה באמצע הפגישה.
     */
    private fun beat() {
        val now = System.currentTimeMillis()
        if (now - lastHeartbeatMs < HEARTBEAT_INTERVAL_MS) return
        lastHeartbeatMs = now
        RecordingSessionState.heartbeat(context)
    }

    /** המספר הגבוה ביותר שכבר קיים בתיקייה, 0 כשהיא ריקה. */
    private fun lastSegmentIndex(dir: File): Int =
        dir.listFiles()
            .orEmpty()
            .filter { it.isFile && it.name.startsWith("part_") }
            .mapNotNull { it.name.filter(Char::isDigit).toIntOrNull() }
            .maxOrNull()
            ?: 0

    private fun startSegment(): Boolean {
        val dir = synchronized(lock) { sessionDir } ?: return false

        val segment = synchronized(lock) {
            segmentIndex += 1
            File(dir, String.format(Locale.US, "part_%03d.m4a", segmentIndex))
        }

        // פרופיל הדיבור לא נוסה על כל מכשיר אפשרי. אם קודק כלשהו לא יקבל
        // 16kHz מונו, נפילה לפרופיל הישן עדיפה על פני הקלטה שלא מתחילה
        // בכלל - הכלב השומר היה מפרש כישלון קבוע כ"מיקרופון תפוס" ומנסה
        // לנצח, והמשתמש היה נשאר בלי שום הקלטה.
        if (speechProfileSupported != false) {
            if (openRecorder(segment, useSpeechProfile = true)) {
                speechProfileSupported = true
                return true
            }
            Log.w(TAG, "startSegment: speech profile refused, trying legacy profile")
        }

        if (!openRecorder(segment, useSpeechProfile = false)) return false

        // הישן הצליח במקום שהחדש נכשל - כלומר זו לא תפיסת מיקרופון אלא קודק
        // שלא תומך בפרופיל. מפסיקים לנסות אותו שוב בכל קטע.
        if (speechProfileSupported == null) {
            Log.w(TAG, "startSegment: device rejects the speech profile, staying on legacy")
            speechProfileSupported = false
        }
        return true
    }

    private fun openRecorder(segment: File, useSpeechProfile: Boolean): Boolean {
        val mediaRecorder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            MediaRecorder(context)
        } else {
            @Suppress("DEPRECATION")
            MediaRecorder()
        }

        try {
            mediaRecorder.apply {
                setAudioSource(MediaRecorder.AudioSource.MIC)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setAudioChannels(1)
                if (useSpeechProfile) {
                    setAudioEncodingBitRate(SPEECH_BITRATE)
                    setAudioSamplingRate(SPEECH_SAMPLE_RATE)
                } else {
                    setAudioEncodingBitRate(LEGACY_BITRATE)
                    setAudioSamplingRate(LEGACY_SAMPLE_RATE)
                }
                setMaxDuration(SEGMENT_ROLL_MS)
                setOutputFile(segment.absolutePath)
                setOnErrorListener { _, what, extra ->
                    Log.e(TAG, "MediaRecorder error what=$what extra=$extra - recovering")
                    handleCaptureLost()
                }
                setOnInfoListener { _, what, _ ->
                    if (what == MediaRecorder.MEDIA_RECORDER_INFO_MAX_DURATION_REACHED ||
                        what == MediaRecorder.MEDIA_RECORDER_INFO_MAX_FILESIZE_REACHED
                    ) {
                        rollSegment()
                    }
                }
                prepare()
                start()
            }
        } catch (e: Exception) {
            Log.w(TAG, "openRecorder failed (mic busy?) for ${segment.name}", e)
            runCatching { mediaRecorder.reset() }
            runCatching { mediaRecorder.release() }
            segment.delete()
            return false
        }

        synchronized(lock) {
            recorder = mediaRecorder
            currentSegment = segment
        }
        return true
    }

    /**
     * המיקרופון נחטף באמצע. סוגרים את מה שנכתב עד כה כקובץ תקין ומשאירים
     * את ה-session פתוח - הכלב השומר ימשיך לנסות עד שיחזור.
     */
    private fun handleCaptureLost() {
        watchdogHandler.post {
            if (!isCapturing) return@post
            isCapturing = false
            finalizeSegment()
            listener?.onCaptureInterrupted()
        }
    }

    /**
     * סוגר את ה-MediaRecorder הנוכחי. אף פעם לא זורק.
     *
     * stop() יכול לזרוק כשהמיקרופון נחטף או כשהקטע קצר מכדי להכיל אודיו
     * תקין. במקרה כזה הקובץ *לא* נמחק אלא נשאר לשיפוט האיחוד: לפעמים
     * המערכת בכל זאת הספיקה לסגור אותו כראוי, וקטע קריא שווה יותר מהניחוש
     * שלנו. קטע שבאמת נהרס נדחה על ידי [AudioSegmentMerger].
     */
    private fun finalizeSegment() {
        val (activeRecorder, segment) = synchronized(lock) {
            val pair = recorder to currentSegment
            recorder = null
            currentSegment = null
            pair
        }
        if (activeRecorder == null) return

        try {
            activeRecorder.stop()
        } catch (e: Exception) {
            Log.w(TAG, "finalizeSegment: stop() threw, segment may be truncated", e)
        }
        runCatching { activeRecorder.release() }

        if (segment != null && segment.exists() && segment.length() == 0L) {
            segment.delete()
        }
    }
}
