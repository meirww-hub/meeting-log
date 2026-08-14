package com.meirww.meetingscribe

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import android.util.Log
import java.io.File
import java.nio.ByteBuffer

/**
 * מאחד קטעי הקלטה (segments) לקובץ m4a רציף אחד.
 *
 * הקלטת פגישה נשמרת כרצף קטעים ולא כקובץ אחד, כי MediaRecorder כותב את טבלת
 * האינדקס (moov) רק ב-stop() - קובץ אחד ארוך שנקטע באמצע (המיקרופון נחטף
 * לשיחה נכנסת, או שהתהליך נהרג) נשאר בלי moov ולכן בלתי קריא לחלוטין. גלגול
 * קטע כל כמה דקות מגביל את הנזק לקטע האחרון בלבד.
 *
 * האיחוד עצמו הוא remux בלבד (העתקת פריימי AAC בין מכולות MP4) - אין פענוח
 * ואין קידוד מחדש, ולכן אין אובדן איכות והפעולה מהירה גם על שעה של אודיו.
 * קטע פגום (למשל הקטע האחרון לפני הריגת התהליך) פשוט מדולג - עדיף לאבד את
 * הדקות האחרונות מאשר את כל הפגישה.
 */
object AudioSegmentMerger {

    private const val TAG = "AudioSegmentMerger"
    private const val FALLBACK_BUFFER_BYTES = 512 * 1024

    /**
     * רווח מלאכותי זעיר בין קטעים. חותמות הזמן של כל קטע מתחילות מ-0, ולכן
     * הן מוזזות קדימה; בלי הרווח הפריים הראשון של קטע חדש היה נושא בדיוק את
     * חותמת הזמן של הפריים האחרון שלפניו, ומוקסרים מסוימים דוחים סדר לא עולה.
     */
    private const val SEGMENT_GAP_US = 25_000L

    /**
     * מחזיר true אם [output] נכתב בהצלחה. אם יש קטע קריא אחד בלבד הוא מועבר
     * כמו שהוא (בלי remux מיותר).
     */
    fun merge(segments: List<File>, output: File): Boolean {
        val usable = segments.filter { it.exists() && it.length() > 0 }
        if (usable.isEmpty()) {
            Log.w(TAG, "merge: no usable segments")
            return false
        }
        if (usable.size == 1) {
            val single = usable.first()
            if (single.renameTo(output)) return true
            return runCatching { single.copyTo(output, overwrite = true) }.isSuccess
        }

        var muxer: MediaMuxer? = null
        var trackIndex = -1
        var timeOffsetUs = 0L
        var wroteAnySample = false

        try {
            muxer = MediaMuxer(output.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)

            for (segment in usable) {
                val extractor = MediaExtractor()
                try {
                    extractor.setDataSource(segment.absolutePath)
                    val audioTrack = (0 until extractor.trackCount).firstOrNull { track ->
                        extractor.getTrackFormat(track)
                            .getString(MediaFormat.KEY_MIME)
                            ?.startsWith("audio/") == true
                    }
                    if (audioTrack == null) {
                        Log.w(TAG, "merge: ${segment.name} has no audio track, skipped")
                        continue
                    }

                    extractor.selectTrack(audioTrack)
                    val format = extractor.getTrackFormat(audioTrack)

                    if (trackIndex == -1) {
                        trackIndex = muxer.addTrack(format)
                        muxer.start()
                    }

                    val bufferSize = format
                        .takeIf { it.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE) }
                        ?.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE)
                        ?.takeIf { it > 0 }
                        ?: FALLBACK_BUFFER_BYTES
                    val buffer = ByteBuffer.allocate(bufferSize)
                    val bufferInfo = MediaCodec.BufferInfo()
                    var lastPresentationTimeUs = 0L

                    while (true) {
                        val sampleSize = extractor.readSampleData(buffer, 0)
                        if (sampleSize < 0) break
                        bufferInfo.offset = 0
                        bufferInfo.size = sampleSize
                        bufferInfo.presentationTimeUs = extractor.sampleTime + timeOffsetUs
                        // אודיו AAC - כל פריים הוא sync frame, אין תלות בפריים קודם.
                        bufferInfo.flags = MediaCodec.BUFFER_FLAG_KEY_FRAME
                        muxer.writeSampleData(trackIndex, buffer, bufferInfo)
                        lastPresentationTimeUs = bufferInfo.presentationTimeUs
                        wroteAnySample = true
                        extractor.advance()
                    }

                    timeOffsetUs = lastPresentationTimeUs + SEGMENT_GAP_US
                } catch (e: Exception) {
                    // קטע פגום (בדרך כלל האחרון, שנכתב בלי moov כי התהליך נהרג).
                    Log.w(TAG, "merge: skipping unreadable segment ${segment.name}", e)
                } finally {
                    runCatching { extractor.release() }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "merge failed", e)
        } finally {
            if (muxer != null) {
                if (wroteAnySample) runCatching { muxer.stop() }
                runCatching { muxer.release() }
            }
        }

        if (!wroteAnySample) {
            output.delete()
            Log.e(TAG, "merge: every segment was unreadable")
            return false
        }
        return output.exists() && output.length() > 0
    }
}
