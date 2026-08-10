package com.meirww.meetingscribe

import org.json.JSONArray
import org.json.JSONObject

/** מטא-דאטה של הקלטה שהושלמה - כפי שמוחזר מ-GET /recordings. */
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
    val note: String?,
) {
    companion object {
        fun fromJson(obj: JSONObject): RecordingItem {
            val speakers = mutableListOf<String>()
            obj.optJSONArray("speakers")?.let { arr ->
                for (i in 0 until arr.length()) speakers.add(arr.getString(i))
            }
            return RecordingItem(
                recordingId = obj.optString("recording_id"),
                title = obj.optString("title").ifBlank { "פגישה ללא כותרת" },
                date = obj.optString("date"),
                speakers = speakers,
                durationSeconds = obj.optDouble("duration_seconds", 0.0),
                folderUrl = obj.optStringOrNull("drive_folder_url"),
                transcriptUrl = obj.optStringOrNull("drive_transcript_url"),
                summaryUrl = obj.optStringOrNull("drive_summary_url"),
                todoUrl = obj.optStringOrNull("drive_todo_url"),
                audioUrl = obj.optStringOrNull("drive_audio_url"),
                note = obj.optStringOrNull("note"),
            )
        }

        fun listFromJson(json: String): List<RecordingItem> {
            val array = JSONArray(json)
            return (0 until array.length()).map { fromJson(array.getJSONObject(it)) }
        }
    }
}

private fun JSONObject.optStringOrNull(key: String): String? =
    if (has(key) && !isNull(key)) getString(key) else null

/** ממיר תאריך ISO (yyyy-MM-dd) לתצוגה בפורמט DD/MM/YYYY. */
fun String.toDisplayDate(): String {
    val parts = split("-")
    return if (parts.size == 3) "${parts[2]}/${parts[1]}/${parts[0]}" else this
}
