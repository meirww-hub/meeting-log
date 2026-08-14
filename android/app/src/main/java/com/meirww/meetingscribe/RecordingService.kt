package com.meirww.meetingscribe

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.telephony.TelephonyManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import java.io.File

/**
 * שירות foreground שמחזיק את ההקלטה כדי שפגישה תמשיך לרוץ גם כשהמסך נעול
 * או שהאפליקציה לא פתוחה - כולל כשמגיעה שיחה נכנסת באמצע.
 */
class RecordingService : Service() {

    companion object {
        private const val ACTION_START = "com.meirww.meetingscribe.action.START_RECORDING"
        private const val ACTION_STOP = "com.meirww.meetingscribe.action.STOP_RECORDING"
        private const val EXTRA_TITLE = "title"
        private const val CHANNEL_ID = "active_recording"
        private const val NOTIFICATION_ID = 42
        private const val TAG = "RecordingService"
        private const val WAKE_LOCK_TAG = "MeetingLog:recording"

        /** משודר (broadcast פנים-אפליקטיבי) כשההקלטה נכשלה להתחיל בפועל. */
        const val ACTION_RECORDING_FAILED = "com.meirww.meetingscribe.action.RECORDING_FAILED"

        /**
         * משודר כשההקלטה ממתינה למיקרופון (שיחה נכנסת) או כשהיא חוזרת -
         * ה-UI מציג את זה במקום להעמיד פנים שהכול תקין. ההקלטה עצמה ממשיכה
         * להיות פעילה בשני המצבים.
         */
        const val ACTION_CAPTURE_STATE = "com.meirww.meetingscribe.action.CAPTURE_STATE"
        const val EXTRA_CAPTURING = "capturing"

        var isRecording = false
            private set

        fun start(context: Context) {
            if (isRecording) return
            val intent = Intent(context, RecordingService::class.java).setAction(ACTION_START)
            ContextCompat.startForegroundService(context, intent)
        }

        /**
         * עצירה מכל מקור (כפתור באפליקציה או כפתור ההתראה) שולחת את ההקלטה
         * לעיבוד מיד. עד היום עצירה מתוך האפליקציה רק שמרה קובץ בזיכרון
         * וחיכתה ללחיצה ידנית על "שלח לעיבוד" - וכל הקלטה שהמשתמש שכח לשלוח,
         * או שהתהליך שלה נהרג לפני כן, פשוט נעלמה.
         */
        fun stop(context: Context, title: String = "") {
            if (!isRecording) return
            val intent = Intent(context, RecordingService::class.java)
                .setAction(ACTION_STOP)
                .putExtra(EXTRA_TITLE, title)
            context.startService(intent)
        }
    }

    private lateinit var recordingManager: RecordingManager
    private val binder = LocalBinder()
    private var wakeLock: PowerManager.WakeLock? = null
    private var phoneStateReceiver: BroadcastReceiver? = null

    inner class LocalBinder : Binder() {
        val service: RecordingService get() = this@RecordingService
    }

    /**
     * מעדכן את ההתראה כשהמיקרופון נחטף וכשהוא חוזר, כדי שהמשתמש יראה שההקלטה
     * לא מתה אלא ממתינה - ובשום שלב לא עוצר אותה.
     */
    private val captureListener = object : RecordingManager.Listener {
        override fun onCaptureInterrupted() = onCaptureStateChanged(capturing = false)
        override fun onCaptureResumed() = onCaptureStateChanged(capturing = true)
    }

    override fun onCreate() {
        super.onCreate()
        recordingManager = RecordingManager(applicationContext)
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> handleStop(intent.getStringExtra(EXTRA_TITLE).orEmpty())
            // הדרך היחידה לפתוח מיקרופון ביוזמה: לחיצה על כפתור ההקלטה.
            ACTION_START -> handleStart()
            // intent ריק = המערכת הרגה את השירות והחזירה אותו (START_STICKY).
            else -> resumeAfterKill()
        }
        return START_STICKY
    }

    /**
     * חידוש הקלטה אחרי שהמערכת הרגה את השירות - ואך ורק כזה. המיקרופון נפתח
     * מחדש רק אם המשתמש באמת לחץ "הקלט" ומעולם לא לחץ "עצור", ותמיד *לתוך
     * אותה תיקיית session*: הקטעים שלפני ההריגה ושאחריה מתאחדים בסוף לקובץ
     * אחד, כלומר תמלול אחד וסיכום אחד לפגישה שלמה.
     *
     * בלי הבדיקה הראשונה כל הפעלה של השירות עם intent ריק הייתה פותחת מיקרופון
     * בלי שאיש ביקש; בלי השנייה הפגישה הייתה מתפצלת לשתי הקלטות נפרדות.
     */
    private fun resumeAfterKill() {
        if (!RecordingSessionState.userStarted(this)) {
            Log.i(TAG, "restart ignored: no interrupted recording the user asked for")
            stopSelf()
            return
        }

        // תיקייה שכבר אוחדה נמצאת בדרך לשרת - אסור להוסיף לה קטעים שלא ייכללו
        // בקובץ שנשלח. אין תיקייה להמשיך אליה = ההקלטה נגמרה; פתיחת session
        // חדש כאן הייתה בדיוק הפיצול שאנחנו מונעים, ולכן פשוט נעצרים.
        val resumeDir = RecordingSessionState.liveSession(this)
            ?.takeIf { RecordingRecovery.mergedFilesIn(it).isEmpty() }
        if (resumeDir == null) {
            Log.w(TAG, "restart ignored: the interrupted session is no longer resumable")
            RecordingSessionState.clear(this)
            RecordingRecovery.sweep(applicationContext)
            stopSelf()
            return
        }

        Log.i(TAG, "resuming into ${resumeDir.name} - the meeting stays one recording")
        handleStart(resumeDir)
    }

    fun currentAmplitude(): Int = if (isRecording) recordingManager.currentAmplitude() else 0

    /** האם באמת נכתב אודיו ברגע זה (false בזמן שיחה שחוטפת את המיקרופון). */
    fun isCapturing(): Boolean = isRecording && recordingManager.isCapturing

    /** [resumeFrom] - תיקיית session קיימת שיש להמשיך לכתוב אליה, ראה [resumeAfterKill]. */
    private fun handleStart(resumeFrom: File? = null) {
        Log.d(TAG, "handleStart isRecording=$isRecording resume=${resumeFrom?.name}")
        if (isRecording) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            Log.w(TAG, "handleStart aborted: RECORD_AUDIO not granted")
            // אין אפשרות לבקש הרשאה מתוך שירות ברקע - זה יכול לקרות רק אם
            // המשתמש עוד לא פתח את האפליקציה פעם אחת ואישר מיקרופון.
            RecordingSessionState.clear(this)
            stopSelf()
            return
        }

        startForeground(NOTIFICATION_ID, buildNotification(capturing = true))

        val sessionDir = recordingManager.startRecording(captureListener, resumeFrom)
        if (sessionDir == null) {
            Log.e(TAG, "handleStart: cannot open a recording session (storage?)")
            RecordingSessionState.clear(this)
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            sendBroadcast(Intent(ACTION_RECORDING_FAILED).setPackage(packageName))
            return
        }

        // מכאן והלאה יש הקלטה שהמשתמש ביקש: הסימון על הדיסק הוא מה שיאפשר
        // לתהליך שיחזור אחרי הריגה להמשיך בדיוק לתיקייה הזו.
        RecordingSessionState.markStarted(this, sessionDir)
        isRecording = true
        acquireWakeLock()
        registerPhoneStateReceiver()
        if (!recordingManager.isCapturing) onCaptureStateChanged(capturing = false)
    }

    private fun handleStop(title: String) {
        // לפני כל דבר אחר: מרגע זה אין הקלטה שהמשתמש מבקש. גם אם המערכת תהרוג
        // את השירות בשנייה הבאה הוא לא יתעורר להקליט, וגם הסריקה שבהמשך
        // הפונקציה כבר רשאית לקחת את התיקייה ולשלוח אותה לעיבוד.
        RecordingSessionState.clear(this)
        if (!isRecording) {
            stopSelf()
            return
        }

        val sessionDir = recordingManager.stopRecording()
        isRecording = false
        unregisterPhoneStateReceiver()
        releaseWakeLock()

        if (sessionDir != null) {
            RecordingRecovery.saveTitle(sessionDir, title)
        }
        // האיחוד וההעלאה נעשים ב-WorkManager: הם שורדים סגירת אפליקציה,
        // וגם אם משהו ייכשל כאן ההקלטה נשארת על הדיסק ותיסרק שוב.
        RecordingRecovery.sweep(applicationContext)

        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        if (isRecording) {
            // השירות נהרס בלי בקשת עצירה (המערכת הרגה אותו). סוגרים את הקטע
            // הפתוח כקובץ תקין ומשאירים את ה-session על הדיסק - הסריקה
            // תשלים אותו, במקום שההקלטה תיעלם.
            runCatching { recordingManager.stopRecording() }
            isRecording = false
            unregisterPhoneStateReceiver()
            releaseWakeLock()
            RecordingRecovery.sweep(applicationContext)
        }
        runCatching { recordingManager.release() }
        super.onDestroy()
    }

    private fun onCaptureStateChanged(capturing: Boolean) {
        if (!isRecording) return
        getSystemService(NotificationManager::class.java)
            ?.notify(NOTIFICATION_ID, buildNotification(capturing))
        sendBroadcast(
            Intent(ACTION_CAPTURE_STATE)
                .setPackage(packageName)
                .putExtra(EXTRA_CAPTURING, capturing)
        )
    }

    /**
     * המסך כבוי בפגישה ארוכה = ה-CPU יכול לרדת ל-doze. השירות ה-foreground
     * שומר על התהליך חי אבל לא מבטיח שעון CPU רציף לכלב השומר שמחזיר את
     * המיקרופון אחרי שיחה.
     */
    private fun acquireWakeLock() {
        if (wakeLock != null) return
        val powerManager = getSystemService(PowerManager::class.java) ?: return
        wakeLock = powerManager
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, WAKE_LOCK_TAG)
            .apply {
                setReferenceCounted(false)
                runCatching { acquire() }
            }
    }

    private fun releaseWakeLock() {
        wakeLock?.let { lock -> if (lock.isHeld) runCatching { lock.release() } }
        wakeLock = null
    }

    /**
     * בסיום שיחה פותחים קטע חדש. אנדרואיד לא תמיד זורק שגיאה כשהוא נותן
     * עדיפות למיקרופון של השיחה - לפעמים הוא פשוט מזין דממה למקליט
     * (isClientSilenced), וההקלטה "ממשיכה" בלי שום קול. פתיחה מחדש של
     * המיקרופון ברגע שהשיחה נגמרת מבטיחה שממשיכים לקלוט אודיו אמיתי.
     */
    private fun registerPhoneStateReceiver() {
        if (phoneStateReceiver != null) return
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                if (intent.action != TelephonyManager.ACTION_PHONE_STATE_CHANGED) return
                val state = intent.getStringExtra(TelephonyManager.EXTRA_STATE)
                if (state == TelephonyManager.EXTRA_STATE_IDLE) {
                    Log.i(TAG, "call ended - rolling to a fresh recording segment")
                    recordingManager.rollSegment()
                }
            }
        }
        val filter = IntentFilter(TelephonyManager.ACTION_PHONE_STATE_CHANGED)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(receiver, filter)
        }
        phoneStateReceiver = receiver
    }

    private fun unregisterPhoneStateReceiver() {
        phoneStateReceiver?.let { receiver -> runCatching { unregisterReceiver(receiver) } }
        phoneStateReceiver = null
    }

    private fun buildNotification(capturing: Boolean): Notification {
        ensureChannel()

        val stopIntent = Intent(this, RecordingService::class.java)
            .setAction(ACTION_STOP)
        val stopPendingIntent = PendingIntent.getService(
            this, 0, stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val openIntent = Intent(this, MainActivity::class.java)
        val openPendingIntent = PendingIntent.getActivity(
            this, 0, openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle("מקליט פגישה")
            .setContentText(
                if (capturing) "ההקלטה פעילה ברקע"
                else "המיקרופון תפוס בשיחה - ההקלטה תתחדש אוטומטית"
            )
            .setOngoing(true)
            .setContentIntent(openPendingIntent)
            .addAction(0, "עצור", stopPendingIntent)
            .build()
    }

    private fun ensureChannel() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID, "הקלטה פעילה", NotificationManager.IMPORTANCE_LOW
        )
        manager.createNotificationChannel(channel)
    }
}
