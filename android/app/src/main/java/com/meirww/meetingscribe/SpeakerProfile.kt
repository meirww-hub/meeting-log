package com.meirww.meetingscribe

import org.json.JSONArray
import org.json.JSONObject

/**
 * דובר שזוהה חוצה-הקלטות לפי טביעת קול - כפי שמוחזר מ-GET /speaker-profiles.
 * name הוא null כל עוד לא תויג; פרופיל מתויג מוצג עם השם הקיים, וניתן
 * לתקן אותו מאותו מסך (ראה UnidentifiedSpeakersActivity).
 *
 * recordingId/channel/startSeconds/endSeconds מגדירים קטע שמע נקי של הדובר
 * הזה בלבד. הסוף נשמר ולא רק ההתחלה: בלעדיו הניגון ממשיך אל שאר ההקלטה -
 * כלומר אל שאר הדוברים - במקום להשמיע את הקול שמתייגים ולעצור.
 *
 * hasAudio הוא false כשההקלטה שממנה נלקחה הדגימה כבר נמחקה (מחיקה ידנית או
 * הניקוי האוטומטי של הקלטות קצרות). זה בדיוק המצב שבו "נגן" לא השמיע כלום
 * בלי שום הסבר, ולכן הוא מוחזר מהשרת ומוצג למשתמש.
 */
data class SpeakerProfile(
    val profileId: String,
    val name: String?,
    val recordingId: String,
    val channel: Int,
    val startSeconds: Double,
    val endSeconds: Double,
    val hasAudio: Boolean,
    val sampleCount: Int,
) {
    /** אורך הקטע במילישניות, או 0 אם הסוף לא נשמר (פרופילים ישנים). */
    val clipLengthMs: Int
        get() = ((endSeconds - startSeconds) * 1000).toInt().coerceAtLeast(0)

    companion object {
        fun fromJson(obj: JSONObject): SpeakerProfile = SpeakerProfile(
            profileId = obj.optString("profile_id"),
            name = obj.optStringOrNull("name"),
            recordingId = obj.optString("recording_id"),
            channel = obj.optInt("channel"),
            startSeconds = obj.optDouble("start_seconds", 0.0),
            endSeconds = obj.optDouble("end_seconds", 0.0),
            // ברירת מחדל true לשרת ישן שלא מחזיר את השדה - התנהגות כמו קודם.
            hasAudio = obj.optBoolean("has_audio", true),
            sampleCount = obj.optInt("sample_count", 1),
        )

        fun listFromJson(json: String): List<SpeakerProfile> {
            val array = JSONArray(json)
            return (0 until array.length()).map { fromJson(array.getJSONObject(it)) }
        }
    }
}
