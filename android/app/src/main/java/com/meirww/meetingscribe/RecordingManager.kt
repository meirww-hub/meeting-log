package com.meirww.meetingscribe

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import android.util.Log
import java.io.File

/** עטיפה דקה סביב MediaRecorder - הקלטת אודיו לקובץ מקומי. */
class RecordingManager(private val context: Context) {

    private companion object {
        const val TAG = "RecordingManager"
    }

    private var recorder: MediaRecorder? = null
    var currentOutputFile: File? = null
        private set

    /**
     * מחזיר null אם ההקלטה נכשלה להתחיל (למשל המיקרופון תפוס על ידי אפליקציה
     * אחרת ברגע זה - cally, שיחה פעילה, מעבר אוזניות בלוטות') - במקום לזרוק
     * חריגה ולהפיל את השירות/האפליקציה בלי שום סימן למה.
     */
    fun startRecording(): File? {
        val outputFile = File(
            context.getExternalFilesDir("recordings"),
            "recording_${System.currentTimeMillis()}.m4a"
        )
        outputFile.parentFile?.mkdirs()

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
                setAudioEncodingBitRate(128_000)
                setAudioSamplingRate(44_100)
                setOutputFile(outputFile.absolutePath)
                setOnErrorListener { _, what, extra ->
                    Log.e(TAG, "MediaRecorder async error during recording: what=$what extra=$extra")
                }
                prepare()
                start()
            }
        } catch (e: Exception) {
            Log.e(TAG, "startRecording failed (mic likely busy elsewhere)", e)
            try {
                mediaRecorder.reset()
                mediaRecorder.release()
            } catch (releaseError: Exception) {
                Log.w(TAG, "cleanup after failed start also threw", releaseError)
            }
            outputFile.delete()
            return null
        }

        recorder = mediaRecorder
        currentOutputFile = outputFile
        return outputFile
    }

    fun stopRecording(): File? {
        recorder?.apply {
            stop()
            release()
        }
        recorder = null
        return currentOutputFile
    }

    /** עוצמת הקול הנוכחית (0..32767 בערך) - להזנת ויזואליזציית האקולייזר. */
    fun currentAmplitude(): Int = try {
        recorder?.maxAmplitude ?: 0
    } catch (e: IllegalStateException) {
        0
    }
}
