package com.meirww.meetingscribe

import android.Manifest
import android.animation.ValueAnimator
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.meirww.meetingscribe.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {

    private companion object {
        const val AMPLITUDE_POLL_INTERVAL_MS = 80L
    }

    private lateinit var binding: ActivityMainBinding
    private var isRecording = false
    private var boundService: RecordingService? = null

    private val mainHandler = Handler(Looper.getMainLooper())
    private var pulseAnimator: ValueAnimator? = null

    private val amplitudePoller = object : Runnable {
        override fun run() {
            if (!isRecording) return
            binding.equalizerView.setAmplitude(boundService?.currentAmplitude() ?: 0)
            mainHandler.postDelayed(this, AMPLITUDE_POLL_INTERVAL_MS)
        }
    }

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            boundService = (service as? RecordingService.LocalBinder)?.service
            syncUiToServiceState()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            boundService = null
        }
    }

    /**
     * מתקבל כשההקלטה נכשלה בפועל להתחיל (MediaRecorder זרק חריגה, בדרך כלל
     * מיקרופון תפוס) - מתקן את ה-UI האופטימי שכפתור ההקלטה כבר הראה.
     */
    private val recordingFailedReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            isRecording = false
            applyIdleUi()
            Toast.makeText(
                this@MainActivity,
                "ההקלטה נכשלה להתחיל - ייתכן שהמיקרופון תפוס על ידי אפליקציה אחרת. נסה שוב.",
                Toast.LENGTH_LONG
            ).show()
        }
    }

    private val requestPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startRecording() else {
                binding.statusText.text = "נדרשת הרשאת מיקרופון כדי להקליט"
            }
        }

    private val requestPhoneStateLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val requestCallLogLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val requestNotificationsLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.recordButton.setOnClickListener {
            if (isRecording) stopRecording() else ensurePermissionAndRecord()
        }

        binding.uploadButton.setOnClickListener { uploadLastRecording() }
        binding.historyButton.setOnClickListener {
            startActivity(Intent(this, HistoryActivity::class.java))
        }
        binding.chatButton.setOnClickListener {
            startActivity(Intent(this, ChatActivity::class.java))
        }

        setUpCallAutoImport()
    }

    override fun onStart() {
        super.onStart()
        bindService(
            Intent(this, RecordingService::class.java), serviceConnection, Context.BIND_AUTO_CREATE
        )
        val filter = IntentFilter(RecordingService.ACTION_RECORDING_FAILED)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(recordingFailedReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(recordingFailedReceiver, filter)
        }
    }

    override fun onResume() {
        super.onResume()
        syncUiToServiceState()
    }

    override fun onStop() {
        super.onStop()
        unbindService(serviceConnection)
        boundService = null
        unregisterReceiver(recordingFailedReceiver)
    }

    /**
     * מיישר את ה-UI למצב האמיתי של RecordingService - נחוץ כי הקלטה יכולה
     * להיעצר גם מחוץ למסך הזה (למשל מכפתור "עצור" בהתראה).
     */
    private fun syncUiToServiceState() {
        val actuallyRecording = RecordingService.isRecording
        if (actuallyRecording == isRecording) return
        isRecording = actuallyRecording
        if (actuallyRecording) applyRecordingUi() else applyIdleUi()
    }

    /**
     * הכנת האיסוף האוטומטי של הקלטות שיחה מ-cally: הרשאת מצב טלפון (לזיהוי
     * סיום שיחה), הרשאת יומן שיחות (לשליפת שם איש הקשר לתיוג "הצד השני" -
     * ראה CallImportWorker) והרשאת Shizuku (לקריאת התיקייה החסומה של cally).
     * נקראת בכל פתיחה כי הרשאת Shizuku פוקעת כשהשירות מופעל מחדש אחרי אתחול.
     */
    private fun setUpCallAutoImport() {
        val hasPhoneState = ContextCompat.checkSelfPermission(
            this, Manifest.permission.READ_PHONE_STATE
        ) == PackageManager.PERMISSION_GRANTED
        if (!hasPhoneState) {
            requestPhoneStateLauncher.launch(Manifest.permission.READ_PHONE_STATE)
        }

        val hasCallLog = ContextCompat.checkSelfPermission(
            this, Manifest.permission.READ_CALL_LOG
        ) == PackageManager.PERMISSION_GRANTED
        if (!hasCallLog) {
            requestCallLogLauncher.launch(Manifest.permission.READ_CALL_LOG)
        }

        if (ShizukuAccess.isAvailable() && !ShizukuAccess.hasPermission()) {
            ShizukuAccess.requestPermission()
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val hasNotifications = ContextCompat.checkSelfPermission(
                this, Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED
            if (!hasNotifications) {
                requestNotificationsLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }

        // סריקה גם בפתיחת האפליקציה - קולטת שיחות שהוקלטו בזמן ש-Shizuku
        // לא הייתה פעילה (למשל מיד אחרי אתחול המכשיר).
        CallImportWorker.schedule(applicationContext, delaySeconds = 5)
    }

    private fun ensurePermissionAndRecord() {
        val hasPermission = ContextCompat.checkSelfPermission(
            this, Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED

        if (hasPermission) startRecording() else {
            requestPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun startRecording() {
        RecordingService.start(applicationContext)
        isRecording = true
        applyRecordingUi()
    }

    private fun stopRecording() {
        // עצירה מהכפתור בתוך האפליקציה לא מעלה אוטומטית - שומר על הזרימה
        // הקיימת של עריכת כותרת ולחיצה ידנית על "העלה".
        RecordingService.stop(applicationContext, autoUpload = false)
        isRecording = false
        applyIdleUi()
    }

    private fun applyRecordingUi() {
        binding.recordButton.setBackgroundResource(R.drawable.bg_record_button_active)
        binding.recordIcon.setImageResource(R.drawable.ic_stop)
        binding.statusText.text = getString(R.string.status_recording)
        binding.statusText.setTextColor(ContextCompat.getColor(this, R.color.record_active))
        binding.uploadButton.isEnabled = false

        binding.equalizerView.setRecording(true)
        mainHandler.post(amplitudePoller)
        startPulse()
    }

    private fun applyIdleUi() {
        binding.recordButton.setBackgroundResource(R.drawable.bg_record_button_idle)
        binding.recordIcon.setImageResource(R.drawable.ic_mic)
        binding.statusText.text = getString(R.string.status_idle)
        binding.statusText.setTextColor(ContextCompat.getColor(this, R.color.text_secondary))
        binding.uploadButton.isEnabled = true

        binding.equalizerView.setRecording(false)
        mainHandler.removeCallbacks(amplitudePoller)
        stopPulse()
    }

    private fun startPulse() {
        pulseAnimator?.cancel()
        pulseAnimator = ValueAnimator.ofFloat(1f, 1.08f, 1f).apply {
            duration = 900
            repeatCount = ValueAnimator.INFINITE
            addUpdateListener {
                val scale = it.animatedValue as Float
                binding.recordButtonRing.scaleX = scale
                binding.recordButtonRing.scaleY = scale
            }
            start()
        }
    }

    private fun stopPulse() {
        pulseAnimator?.cancel()
        pulseAnimator = null
        binding.recordButtonRing.scaleX = 1f
        binding.recordButtonRing.scaleY = 1f
    }

    private fun uploadLastRecording() {
        val audioFile = RecordingService.lastOutputFile ?: return
        val title = binding.titleInput.text?.toString().orEmpty()

        val uploadRequest = OneTimeWorkRequestBuilder<UploadWorker>()
            .setInputData(
                workDataOf(
                    UploadWorker.KEY_AUDIO_PATH to audioFile.absolutePath,
                    UploadWorker.KEY_TITLE to title
                )
            )
            .build()

        WorkManager.getInstance(applicationContext).enqueue(uploadRequest)
        binding.statusText.text = getString(R.string.status_uploading)
        binding.statusText.setTextColor(ContextCompat.getColor(this, R.color.accent_cyan))
        binding.uploadButton.isEnabled = false
    }
}
