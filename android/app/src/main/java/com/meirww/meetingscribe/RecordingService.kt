package com.meirww.meetingscribe

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Binder
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import java.io.File

/**
 * שירות foreground שמחזיק את ה-MediaRecorder כדי שהקלטת פגישה תמשיך לרוץ גם
 * כשהמסך נעול או שהאפליקציה לא פתוחה.
 */
class RecordingService : Service() {

    companion object {
        private const val ACTION_START = "com.meirww.meetingscribe.action.START_RECORDING"
        private const val ACTION_STOP = "com.meirww.meetingscribe.action.STOP_RECORDING"
        private const val EXTRA_AUTO_UPLOAD = "auto_upload"
        private const val CHANNEL_ID = "active_recording"
        private const val NOTIFICATION_ID = 42
        private const val TAG = "RecordingService"

        /** משודר (broadcast פנים-אפליקטיבי) כשההקלטה נכשלה להתחיל בפועל - ראה MainActivity. */
        const val ACTION_RECORDING_FAILED = "com.meirww.meetingscribe.action.RECORDING_FAILED"

        var isRecording = false
            private set

        /** קובץ ההקלטה האחרונה שנעצרה - לשימוש כפתור "העלה" ב-MainActivity. */
        var lastOutputFile: File? = null
            private set

        fun start(context: Context) {
            if (isRecording) return
            val intent = Intent(context, RecordingService::class.java).setAction(ACTION_START)
            ContextCompat.startForegroundService(context, intent)
        }

        /**
         * autoUpload=true (למשל עצירה מכפתור ההתראה) מעלה מיד עם כותרת ריקה,
         * כדי שההקלטה תגיע לעיבוד בלי לפתוח את האפליקציה בכלל.
         * עצירה מכפתור ה-UI הרגיל משתמשת ב-false ושומרת על הזרימה הקיימת -
         * המשתמש עורך כותרת ולוחץ "העלה" בעצמו.
         */
        fun stop(context: Context, autoUpload: Boolean) {
            if (!isRecording) return
            val intent = Intent(context, RecordingService::class.java)
                .setAction(ACTION_STOP)
                .putExtra(EXTRA_AUTO_UPLOAD, autoUpload)
            context.startService(intent)
        }
    }

    private lateinit var recordingManager: RecordingManager
    private val binder = LocalBinder()

    inner class LocalBinder : Binder() {
        val service: RecordingService get() = this@RecordingService
    }

    override fun onCreate() {
        super.onCreate()
        recordingManager = RecordingManager(applicationContext)
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> handleStop(intent.getBooleanExtra(EXTRA_AUTO_UPLOAD, false))
            else -> handleStart()
        }
        return START_NOT_STICKY
    }

    fun currentAmplitude(): Int = if (isRecording) recordingManager.currentAmplitude() else 0

    private fun handleStart() {
        Log.d(TAG, "handleStart isRecording=$isRecording")
        if (isRecording) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            Log.w(TAG, "handleStart aborted: RECORD_AUDIO not granted")
            // אין אפשרות לבקש הרשאה מתוך שירות ברקע - זה יכול לקרות רק אם
            // המשתמש עוד לא פתח את האפליקציה פעם אחת ואישר מיקרופון.
            stopSelf()
            return
        }

        startForeground(NOTIFICATION_ID, buildNotification())
        val outputFile = recordingManager.startRecording()
        if (outputFile == null) {
            Log.e(TAG, "handleStart: MediaRecorder failed to start (mic busy?)")
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            sendBroadcast(Intent(ACTION_RECORDING_FAILED).setPackage(packageName))
            return
        }
        isRecording = true
    }

    private fun handleStop(autoUpload: Boolean) {
        if (!isRecording) {
            stopSelf()
            return
        }

        val file = recordingManager.stopRecording()
        isRecording = false
        lastOutputFile = file

        if (autoUpload && file != null) {
            val request = OneTimeWorkRequestBuilder<UploadWorker>()
                .setInputData(
                    workDataOf(
                        UploadWorker.KEY_AUDIO_PATH to file.absolutePath,
                        UploadWorker.KEY_TITLE to ""
                    )
                )
                .build()
            WorkManager.getInstance(applicationContext).enqueue(request)
        }

        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        if (isRecording) {
            try {
                lastOutputFile = recordingManager.stopRecording()
            } catch (e: Exception) {
                // המקליט כבר היה במצב לא תקין - אין מה לעשות מעבר לנקות את הדגל.
            }
            isRecording = false
        }
        super.onDestroy()
    }

    private fun buildNotification(): Notification {
        ensureChannel()

        val stopIntent = Intent(this, RecordingService::class.java)
            .setAction(ACTION_STOP)
            .putExtra(EXTRA_AUTO_UPLOAD, true)
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
            .setContentText("ההקלטה פעילה ברקע")
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
