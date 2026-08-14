package com.meirww.meetingscribe

import org.json.JSONArray
import org.json.JSONObject

/**
 * קובץ מצורף להקלטה - כפי שמוחזר בתוך attachments של GET /recordings.
 * status הוא "processing"/"done"/"error"; error ו-driveUrl מתמלאים בהתאם.
 */
data class Attachment(
    val attachmentId: String,
    val filename: String,
    val status: String,
    val error: String?,
    val driveUrl: String?,
) {
    val isProcessing: Boolean get() = status == "processing"
    val isFailed: Boolean get() = status == "error"

    companion object {
        fun fromJson(obj: JSONObject): Attachment = Attachment(
            attachmentId = obj.optString("attachment_id"),
            filename = obj.optString("filename"),
            status = obj.optString("status"),
            error = obj.optStringOrNull("error"),
            driveUrl = obj.optStringOrNull("drive_url"),
        )
    }
}

/** מטא-דאטה של הקלטה - כפי שמוחזר מ-GET /recordings. */
data class RecordingItem(
    val recordingId: String,
    val title: String,
    val date: String,
    val speakers: List<String>,
    val durationSeconds: Double,
    val folderUrl: String?,
    val transcriptUrl: String?,
    val summaryUrl: String?,
    val todoUrl: String?,
    val audioUrl: String?,
    /**
     * כמה קבצי אודיו יש להקלטה ב-Drive. שיחת טלפון מיובאת מגיעה בשני ערוצים
     * מבודדים (הצד שלי / הצד השני), ושמיעת השיחה דורשת את שניהם יחד - ראה
     * [RecordingPlayer]. 0 כשאין אודיו כלל.
     */
    val audioChannelCount: Int,
    val note: String?,
    /**
     * הקלטה שהעיבוד שלה נכשל. עד 2026-08-13 הרשימה כללה "done" בלבד, ולכן
     * הקלטה כזו פשוט לא הופיעה - שיחה שלמה נעלמה בלי שאיש ידע שהיא הגיעה
     * בכלל. עכשיו היא מוצגת ככישלון גלוי, עם סיבה, ואפשר לנסות אותה שוב.
     */
    val failed: Boolean = false,
    val error: String? = null,
    val attachments: List<Attachment> = emptyList(),
) {
    companion object {
        fun fromJson(obj: JSONObject): RecordingItem {
            val speakers = mutableListOf<String>()
            obj.optJSONArray("speakers")?.let { arr ->
                for (i in 0 until arr.length()) speakers.add(arr.getString(i))
            }
            val attachments = mutableListOf<Attachment>()
            obj.optJSONArray("attachments")?.let { arr ->
                for (i in 0 until arr.length()) attachments.add(Attachment.fromJson(arr.getJSONObject(i)))
            }
            val failed = obj.optString("status") == "error"
            val audioUrl = obj.optStringOrNull("drive_audio_url")
            // הקלטות ישנות נשמרו עם הקישור בלבד, בלי רשימת מזהי הקבצים -
            // להן יש ערוץ אחד, זה שהקישור מצביע עליו.
            val audioChannels = obj.optJSONArray("drive_audio_file_ids")?.length() ?: 0
            return RecordingItem(
                recordingId = obj.optString("recording_id"),
                title = obj.optString("title").ifBlank {
                    if (failed) "הקלטה שעיבודה נכשל" else "פגישה ללא כותרת"
                },
                // הקלטה שנכשלה לא הגיעה לשלב שבו נקבע התאריך, ולכן נופלים
                // על מועד ההגעה לשרת - אחרת השורה מוצגת בלי תאריך כלל.
                date = obj.optString("date").ifBlank {
                    obj.optStringOrNull("created_at")?.take(10).orEmpty()
                },
                speakers = speakers,
                durationSeconds = obj.optDouble("duration_seconds", 0.0),
                folderUrl = obj.optStringOrNull("drive_folder_url"),
                transcriptUrl = obj.optStringOrNull("drive_transcript_url"),
                summaryUrl = obj.optStringOrNull("drive_summary_url"),
                todoUrl = obj.optStringOrNull("drive_todo_url"),
                audioUrl = audioUrl,
                audioChannelCount = when {
                    audioChannels > 0 -> audioChannels
                    audioUrl != null -> 1
                    else -> 0
                },
                note = obj.optStringOrNull("note"),
                failed = failed,
                error = obj.optStringOrNull("error"),
                attachments = attachments,
            )
        }

        fun listFromJson(json: String): List<RecordingItem> {
            val array = JSONArray(json)
            return (0 until array.length()).map { fromJson(array.getJSONObject(it)) }
        }
    }
}

/**
 * שדה טקסט אופציונלי. לא optString: הוא מחזיר את המחרוזת "null" כשהערך הוא
 * null ב-JSON, ומחרוזת ריקה כשהשדה חסר.
 */
internal fun JSONObject.optStringOrNull(key: String): String? =
    if (has(key) && !isNull(key)) getString(key).takeIf { it.isNotBlank() } else null

/** שדה מספרי אופציונלי (למשל start_seconds של ציטוט בצ'אט). */
internal fun JSONObject.optDoubleOrNull(key: String): Double? =
    if (has(key) && !isNull(key)) optDouble(key).takeIf { !it.isNaN() } else null

/** ממיר תאריך ISO (yyyy-MM-dd) לתצוגה בפורמט DD/MM/YYYY. */
fun String.toDisplayDate(): String {
    val parts = split("-")
    return if (parts.size == 3) "${parts[2]}/${parts[1]}/${parts[0]}" else this
}

/** גרסה מקוצרת (DD/MM) לשימוש במקומות צרים כמו שבבי הבחירה בצ'אט. */
fun String.toShortDisplayDate(): String {
    val parts = split("-")
    return if (parts.size == 3) "${parts[2]}/${parts[1]}" else this
}

/** משך בשניות -> H:MM:SS או M:SS. */
fun formatDuration(totalSeconds: Double): String {
    val total = totalSeconds.toInt()
    val hours = total / 3600
    val minutes = (total % 3600) / 60
    val seconds = total % 60
    return if (hours > 0) {
        String.format("%d:%02d:%02d", hours, minutes, seconds)
    } else {
        String.format("%d:%02d", minutes, seconds)
    }
}
