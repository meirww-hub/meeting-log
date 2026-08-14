package com.meirww.meetingscribe

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.meirww.meetingscribe.databinding.ActivityShareReceiveBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * מקבלת קובץ אודיו ששותף מאפליקציה חיצונית (למשל cally, לאחר הקלטת שיחת
 * טלפון) דרך Android Share, ומזינה אותו לאותו pipeline של הקלטות רגילות
 * (UploadWorker -> POST /recordings).
 */
class ShareReceiveActivity : AppCompatActivity() {

    private lateinit var binding: ActivityShareReceiveBinding
    private var copiedAudioFile: File? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityShareReceiveBinding.inflate(layoutInflater)
        setContentView(binding.root)

        @Suppress("DEPRECATION")
        val sourceUri = intent.takeIf { it.action == Intent.ACTION_SEND }
            ?.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)

        if (sourceUri == null) {
            finish()
            return
        }

        binding.shareTitleInput.setText(defaultTitle())
        binding.shareCancelButton.setOnClickListener {
            copiedAudioFile?.delete()
            finish()
        }
        binding.shareUploadButton.setOnClickListener { enqueueUpload() }

        lifecycleScope.launch {
            val file = withContext(Dispatchers.IO) { copySharedAudio(sourceUri) }
            if (file == null) {
                Toast.makeText(this@ShareReceiveActivity, R.string.share_copy_error, Toast.LENGTH_SHORT).show()
                finish()
                return@launch
            }
            // מדיניות האורך המזערי (ראה AudioDuration) נבדקת כאן ולא בלחיצה על
            // "שלח לעיבוד", כדי שהמשתמש יידע מיד ולא אחרי שהקליד כותרת. כאן -
            // בניגוד לייבוא האוטומטי - הסירוב גלוי, כי זו פעולה שהמשתמש יזם.
            val tooShort = withContext(Dispatchers.IO) {
                AudioDuration.isShorterThanMinimum(AudioDuration.seconds(file))
            }
            if (tooShort) {
                file.delete()
                Toast.makeText(
                    this@ShareReceiveActivity,
                    getString(R.string.share_too_short, AudioDuration.MIN_PROCESSING_MINUTES),
                    Toast.LENGTH_LONG
                ).show()
                finish()
                return@launch
            }

            copiedAudioFile = file
            binding.shareUploadButton.isEnabled = true
        }
    }

    private fun defaultTitle(): String {
        val formatted = SimpleDateFormat("dd/MM HH:mm", Locale.getDefault()).format(Date())
        return getString(R.string.share_default_title, formatted)
    }

    private fun copySharedAudio(uri: Uri): File? {
        return try {
            val outputFile = File(
                getExternalFilesDir("recordings"),
                "call_${System.currentTimeMillis()}.${extensionFor(uri)}"
            )
            outputFile.parentFile?.mkdirs()
            val copied = contentResolver.openInputStream(uri)?.use { input ->
                outputFile.outputStream().use { output -> input.copyTo(output) }
                true
            } ?: false
            if (copied) outputFile else null
        } catch (e: Exception) {
            null
        }
    }

    private fun extensionFor(uri: Uri): String {
        queryFileName(uri)?.substringAfterLast('.', "")?.takeIf { it.isNotBlank() }?.let { return it }
        contentResolver.getType(uri)
            ?.let { MimeTypeMap.getSingleton().getExtensionFromMimeType(it) }
            ?.let { return it }
        return "m4a"
    }

    private fun queryFileName(uri: Uri): String? {
        contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (idx >= 0 && cursor.moveToFirst()) return cursor.getString(idx)
        }
        return null
    }

    private fun enqueueUpload() {
        val audioFile = copiedAudioFile ?: return
        val title = binding.shareTitleInput.text?.toString().orEmpty()

        val uploadRequest = OneTimeWorkRequestBuilder<UploadWorker>()
            .setInputData(
                workDataOf(
                    UploadWorker.KEY_AUDIO_PATH to audioFile.absolutePath,
                    UploadWorker.KEY_TITLE to title,
                    // שם הקובץ נושא חותמת זמן ולכן ייחודי לכל שיתוף: ניסיון
                    // העלאה חוזר של אותו שיתוף לא ייקלט פעמיים בשרת, ושיתוף
                    // חדש של אותו קובץ עדיין מייצר הקלטה חדשה כרצון המשתמש.
                    UploadWorker.KEY_CLIENT_UPLOAD_ID to audioFile.name,
                    UploadWorker.KEY_DURATION_SECONDS to
                        (AudioDuration.seconds(audioFile) ?: 0.0),
                )
            )
            .build()

        WorkManager.getInstance(applicationContext).enqueue(uploadRequest)
        Toast.makeText(this, R.string.status_uploading, Toast.LENGTH_SHORT).show()
        finish()
    }
}
