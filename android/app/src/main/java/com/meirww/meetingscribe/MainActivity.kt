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
import android.view.View
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
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

    /** מציג כמה זמן עובר מתחילת ההקלטה - נשען על זמן ההתחלה האמיתי מהדיסק
     * ולא על שעון פנימי, כך שאחרי סגירה ופתיחה מחדש של המסך הטיימר ממשיך
     * מהמקום הנכון ולא מתאפס. */
    private val elapsedTicker = object : Runnable {
        override fun run() {
            if (!isRecording) return
            val startedAt = RecordingSessionState.startedAtMillis(applicationContext)
            if (startedAt != null) {
                binding.timerText.text = formatElapsed(System.currentTimeMillis() - startedAt)
            }
            mainHandler.postDelayed(this, 1000L)
        }
    }

    private fun formatElapsed(millis: Long): String {
        val totalSeconds = (millis / 1000).coerceAtLeast(0)
        val hours = totalSeconds / 3600
        val minutes = (totalSeconds % 3600) / 60
        val seconds = totalSeconds % 60
        return if (hours > 0) {
            String.format("%d:%02d:%02d", hours, minutes, seconds)
        } else {
            String.format("%02d:%02d", minutes, seconds)
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
     * מתקבל כשלא ניתן היה לפתוח הקלטה בכלל (בעיית אחסון) - מתקן את ה-UI
     * האופטימי שכפתור ההקלטה כבר הראה. מיקרופון תפוס *אינו* נכלל כאן:
     * ההקלטה במקרה כזה ממתינה ומתחדשת לבד, ראה [captureStateReceiver].
     */
    private val recordingFailedReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            isRecording = false
            applyIdleUi()
            Toast.makeText(
                this@MainActivity,
                "לא ניתן להתחיל הקלטה - בדוק שיש מקום פנוי באחסון ונסה שוב.",
                Toast.LENGTH_LONG
            ).show()
        }
    }

    /**
     * המיקרופון נחטף (שיחה נכנסת) או חזר. ההקלטה ממשיכה להיות פעילה בשני
     * המצבים - רק הטקסט משתנה, כדי שהמשתמש ידע מה קורה ולא יעצור בטעות.
     */
    /** עצירה שהגיעה מבחוץ (שעון, התראה): המסך חוזר למצב "נשמר" במקום להמשיך להציג "מקליט". */
    private val recordingStoppedReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (!isRecording) return
            isRecording = false
            applyIdleUi()
            binding.statusText.text = getString(R.string.status_saved_waiting_send)
            binding.statusText.setTextColor(
                ContextCompat.getColor(this@MainActivity, R.color.accent_cyan)
            )
        }
    }

    private val captureStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (!isRecording) return
            val capturing = intent.getBooleanExtra(RecordingService.EXTRA_CAPTURING, true)
            if (capturing) {
                binding.statusText.text = getString(R.string.status_recording)
                binding.statusText.setTextColor(
                    ContextCompat.getColor(this@MainActivity, R.color.record_active)
                )
            } else {
                binding.statusText.text = getString(R.string.status_recording_waiting_mic)
                binding.statusText.setTextColor(
                    ContextCompat.getColor(this@MainActivity, R.color.text_secondary)
                )
            }
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

        updateThemeToggleIcon()
        binding.themeToggleButton.setOnClickListener {
            ThemePrefs.setNightMode(this, !ThemePrefs.isNightMode(this))
            recreate()
        }

        setUpCallAutoImport()
    }

    override fun onStart() {
        super.onStart()
        bindService(
            Intent(this, RecordingService::class.java), serviceConnection, Context.BIND_AUTO_CREATE
        )
        registerLocal(recordingFailedReceiver, RecordingService.ACTION_RECORDING_FAILED)
        registerLocal(captureStateReceiver, RecordingService.ACTION_CAPTURE_STATE)
        registerLocal(recordingStoppedReceiver, RecordingService.ACTION_RECORDING_STOPPED)
    }

    private fun registerLocal(receiver: BroadcastReceiver, action: String) {
        val filter = IntentFilter(action)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(receiver, filter)
        }
    }

    override fun onResume() {
        super.onResume()
        syncUiToServiceState()
        refreshPendingLabel()
    }

    override fun onStop() {
        super.onStop()
        unbindService(serviceConnection)
        boundService = null
        unregisterReceiver(recordingFailedReceiver)
        unregisterReceiver(captureStateReceiver)
        unregisterReceiver(recordingStoppedReceiver)
    }

    /**
     * מיישר את ה-UI למצב האמיתי של RecordingService - נחוץ כי הקלטה יכולה
     * להיעצר גם מחוץ למסך הזה (למשל מכפתור "עצור" בהתראה).
     */
    private fun syncUiToServiceState() {
        val actuallyRecording = RecordingService.isRecording
        if (actuallyRecording != isRecording) {
            isRecording = actuallyRecording
            if (actuallyRecording) applyRecordingUi() else applyIdleUi()
        }
        // הקלטה יכולה להיות פעילה אך ממתינה למיקרופון (שיחה) - חוזרים למסך
        // ורואים את המצב האמיתי ולא "מקליט..." מטעה.
        if (actuallyRecording && boundService?.isCapturing() == false) {
            binding.statusText.text = getString(R.string.status_recording_waiting_mic)
            binding.statusText.setTextColor(ContextCompat.getColor(this, R.color.text_secondary))
        }
    }

    /**
     * הכנת האיסוף האוטומטי של הקלטות שיחה מ-cally: הרשאת מצב טלפון (לזיהוי
     * סיום שיחה), הרשאת יומן שיחות (לזיהוי שיחה חדשה) והרשאת Shizuku (לקריאת
     * התיקייה החסומה של cally). נקראת בכל פתיחה כי הרשאת Shizuku פוקעת כשהשירות
     * מופעל מחדש אחרי אתחול.
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

        requestBatteryExemption()
        requestOverlayPermissionOnce()

        // סריקה גם בפתיחת האפליקציה - קולטת שיחות שהוקלטו בזמן ש-Shizuku
        // לא הייתה פעילה (למשל מיד אחרי אתחול המכשיר).
        CallImportWorker.schedule(applicationContext, delaySeconds = 5)
    }

    /**
     * בלי פטור מאופטימיזציית סוללה אנדרואיד מעביר אפליקציה שלא נפתחת הרבה
     * לדלי "RARE", ושם מכסת המשימות ברקע כמעט אפסית: סריקת ייבוא השיחות
     * וההעלאות נדחות בשעות, ושיחות מגיעות לעיבוד באיחור גדול או בכלל לא.
     * מבקשים פעם אחת (המשתמש יכול לסרב) - הדיאלוג של המערכת, לא הגדרות.
     */
    @android.annotation.SuppressLint("BatteryLife")
    private fun requestBatteryExemption() {
        val powerManager = getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
        if (powerManager.isIgnoringBatteryOptimizations(packageName)) return
        val prefs = getSharedPreferences("battery_prompt", Context.MODE_PRIVATE)
        if (prefs.getBoolean("asked", false)) return
        prefs.edit().putBoolean("asked", true).apply()
        runCatching {
            startActivity(
                Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                    .setData(android.net.Uri.parse("package:$packageName"))
            )
        }
    }

    /**
     * "הצגה מעל אפליקציות אחרות" - התנאי היחיד שמאפשר לשעון להתחיל הקלטה
     * כשהטלפון נעול או בכיס (ראה WatchStartPolicy). מבקשים פעם אחת, עם הסבר
     * למה, כי זו הרשאה שאנדרואיד מתייחס אליה בחשדנות. אם המשתמש מסרב, השעון
     * יציג בעצמו מה חסר כשינסה להתחיל.
     */
    private fun requestOverlayPermissionOnce() {
        if (android.provider.Settings.canDrawOverlays(this)) return
        val prefs = getSharedPreferences("overlay_prompt", Context.MODE_PRIVATE)
        if (prefs.getBoolean("asked", false)) return
        prefs.edit().putBoolean("asked", true).apply()
        com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
            .setTitle(R.string.overlay_prompt_title)
            .setMessage(R.string.overlay_prompt_message)
            .setPositiveButton(R.string.overlay_prompt_ok) { _, _ ->
                runCatching {
                    startActivity(
                        Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION)
                            .setData(android.net.Uri.parse("package:$packageName"))
                    )
                }
            }
            .setNegativeButton(R.string.overlay_prompt_later, null)
            .show()
    }

    private fun updateThemeToggleIcon() {
        val icon = if (ThemePrefs.isNightMode(this)) R.drawable.ic_sun else R.drawable.ic_moon
        binding.themeToggleButton.setImageResource(icon)
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

    /**
     * העצירה שומרת את ההקלטה (עם הכותרת שהוקלדה) על הדיסק, והיא נשלחת
     * לעיבוד רק בלחיצה על "שלח לעיבוד". ההמתנה בטוחה כי ההקלטה על הדיסק ולא
     * בזיכרון - היא שורדת הריגת תהליך ופתיחה מחדש של האפליקציה, ושום סריקה
     * אוטומטית לא שולחת אותה (ראה RecordingRecovery.sweep). השרת מייצר כותרת
     * לבד אם היא ריקה, והיא ניתנת לעריכה מההיסטוריה.
     */
    private fun stopRecording() {
        RecordingService.stop(applicationContext, binding.titleInput.text?.toString().orEmpty())
        binding.titleInput.setText("")
        isRecording = false
        applyIdleUi()
        binding.statusText.text = getString(R.string.status_saved_waiting_send)
        binding.statusText.setTextColor(ContextCompat.getColor(this, R.color.accent_cyan))
    }

    private fun applyRecordingUi() {
        binding.recordButton.setBackgroundResource(R.drawable.bg_record_button_active)
        binding.recordIcon.setImageResource(R.drawable.ic_stop)
        binding.statusText.text = getString(R.string.status_recording)
        binding.statusText.setTextColor(ContextCompat.getColor(this, R.color.record_active))
        binding.uploadButton.isEnabled = false

        binding.equalizerView.setRecording(true)
        mainHandler.post(amplitudePoller)
        binding.timerText.visibility = View.VISIBLE
        mainHandler.post(elapsedTicker)
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
        binding.timerText.visibility = View.GONE
        mainHandler.removeCallbacks(elapsedTicker)
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

    /** מספר הממתינות על הכפתור, כדי שיהיה ברור גם בלי ללחוץ. */
    private fun refreshPendingLabel() {
        val pending = RecordingRecovery.pendingCount(applicationContext)
        binding.uploadLabel.text =
            if (pending > 0) getString(R.string.upload_with_count, pending)
            else getString(R.string.upload_recording)
    }

    /**
     * רשימת ההקלטות שממתינות, עם בחירה מה לשלוח ומה למחוק. הכול מסומן כברירת
     * מחדל, כך שלחיצה אחת על "שלח נבחרות" שולחת הכול כמו קודם.
     */
    private fun uploadLastRecording() {
        val sessions = RecordingRecovery.pendingSessions(applicationContext)
        if (sessions.isEmpty()) {
            Toast.makeText(this, R.string.upload_nothing_pending, Toast.LENGTH_SHORT).show()
            return
        }

        val fmt = java.text.SimpleDateFormat("dd/MM HH:mm", java.util.Locale.getDefault())
        val labels = sessions.map {
            val minutes = (it.approxSeconds / 60).coerceAtLeast(0)
            val mb = it.bytes / (1024.0 * 1024.0)
            fmt.format(java.util.Date(it.recordedAtMillis)) + "  \u2022  ~" + minutes +
                " \u05d3\u05e7\u05f3  \u2022  " + String.format(java.util.Locale.US, "%.1fMB", mb)
        }.toTypedArray()
        val checked = BooleanArray(sessions.size) { true }

        fun selected() = sessions.filterIndexed { i, _ -> checked[i] }.map { it.name }.toSet()

        val dialog = com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
            .setTitle(R.string.pending_title)
            .setMultiChoiceItems(labels, checked) { _, which, isChecked -> checked[which] = isChecked }
            .setPositiveButton(R.string.pending_send, null)
            .setNegativeButton(R.string.pending_delete, null)
            .setNeutralButton(R.string.pending_close, null)
            .create()
        dialog.setOnShowListener {
            dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val names = selected()
                if (names.isEmpty()) {
                    Toast.makeText(this, R.string.pending_none_selected, Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }
                RecordingRecovery.sweep(applicationContext, userInitiated = true, onlySessions = names)
                Toast.makeText(
                    this, getString(R.string.upload_pending_sent, names.size), Toast.LENGTH_LONG
                ).show()
                binding.statusText.text = getString(R.string.status_uploading)
                binding.statusText.setTextColor(ContextCompat.getColor(this, R.color.accent_cyan))
                dialog.dismiss()
                mainHandler.postDelayed({ refreshPendingLabel() }, 3_000)
            }
            dialog.getButton(android.app.AlertDialog.BUTTON_NEGATIVE).setOnClickListener {
                val names = selected()
                if (names.isEmpty()) {
                    Toast.makeText(this, R.string.pending_none_selected, Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }
                com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
                    .setMessage(getString(R.string.pending_delete_confirm, names.size))
                    .setPositiveButton(R.string.pending_delete) { _, _ ->
                        val n = RecordingRecovery.deleteSessions(applicationContext, names)
                        Toast.makeText(
                            this, getString(R.string.pending_deleted, n), Toast.LENGTH_SHORT
                        ).show()
                        dialog.dismiss()
                        refreshPendingLabel()
                    }
                    .setNegativeButton(R.string.pending_close, null)
                    .show()
            }
        }
        dialog.show()
    }
}
