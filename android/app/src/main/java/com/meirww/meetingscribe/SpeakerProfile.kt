package com.meirww.meetingscribe

import org.json.JSONArray
import org.json.JSONObject

/**
 * דובר שזוהה חוצה-הקלטות לפי טביעת קול - כפי שמוחזר מ-GET /speaker-profiles.
 * name הוא null כל עוד לא תויג; פרופיל מתויג מוצג עם השם הקיים, וניתן
 * לתקן אותו מאותו מסך (ראה UnidentifiedSpeakersActivity). recordingId/
 * channel/startSeconds מצביעים על קטע שמע ברור של הדובר הזה, להשמעה -
 * אותו מסלול הזרמה כמו נגן ההקלטות הרגיל.
 */
data class SpeakerProfile(
    val profileId: String,
    val name: String?,
    val recordingId: String,
    val channel: Int,
    val startSeconds: Double,
) {
    companion object {
        fun fromJson(obj: JSONObject): SpeakerProfile = SpeakerProfile(
            profileId = obj.optString("profile_id"),
            name = obj.optStringOrNull("name"),
            recordingId = obj.optString("recording_id"),
            channel = obj.optInt("channel"),
            startSeconds = obj.optDouble("start_seconds", 0.0),
        )

        fun listFromJson(json: String): List<SpeakerProfile> {
            val array = JSONArray(json)
            return (0 until array.length()).map { fromJson(array.getJSONObject(it)) }
        }
    }
}
