package com.meirww.meetingscribe

import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.meirww.meetingscribe.databinding.ActivityUnidentifiedSpeakersBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * מסך פרופילי הדוברים: כל קול שזוהה חוצה-הקלטות לפי טביעת קול (ראה
 * pipeline/speaker_id.py בשרת) - מתויגים ולא-מתויגים כאחד. כל שורה היא
 * קול אחד שנצבר על פני הקלטות - לא הקלטה בודדת - כך שאותו דובר לא-מזוהה
 * שחוזר בכמה הקלטות לא יופיע כמה פעמים (הוחלט 2026-08-15: "מיקבוץ אחד").
 * פרופיל מתויג מוצג עם השם הקיים, וניתן לתקן אותו כאן.
 *
 * תיוג/תיקון שם כאן חל רק קדימה: לא סורק ומתקן הקלטות שכבר נשמרו, רק
 * קובע את השם שינוצל בפעם הבאה שהקול הזה יזוהה (ראה PATCH /speaker-profiles).
 * אותו דבר נכון למחיקה - היא מוציאה את הפרופיל מהתחרות על התאמות בהקלטות
 * הבאות, ולא נוגעת במה שכבר נשמר.
 *
 * מנגן ערוץ אחד בלבד (לא כל הערוצים כמו RecordingPlayer ב-ChatActivity) -
 * בכוונה: קטע נקי של הדובר הזה בלבד, בלי דליפה מקולות אחרים באותה הקלטה,
 * הוא כל הנקודה כאן. ומאותה סיבה הניגון גם **נעצר** בסוף הקטע ולא ממשיך
 * אל שאר ההקלטה.
 */
class UnidentifiedSpeakersActivity : AppCompatActivity() {

    private companion object {
        const val OWNER_USER_ID = "primary_user"
    }

    private lateinit var binding: ActivityUnidentifiedSpeakersBinding

    // כמו ב-HistoryActivity: Cloud Run רץ עם min-instances=0, אז בקשה אחרי
    // חוסר פעילות מעירה מופע קר ועלולה לחרוג מ-10 השניות המובנות ב-OkHttp.
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()
    private val adapter = UnidentifiedSpeakersAdapter(
        onPlayClick = ::togglePlaybackOf,
        onSaveClick = ::saveName,
        onDeleteClick = ::confirmDelete,
    )

    private val player by lazy { RecordingPlayer(this) }
    private var playbackJob: Job? = null
    private var playbackToken = 0
    private var loadedProfileId: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityUnidentifiedSpeakersBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.speakersList.layoutManager = LinearLayoutManager(this)
        binding.speakersList.adapter = adapter

        binding.backButton.setOnClickListener { finish() }
        binding.swipeRefresh.setOnRefreshListener { loadProfiles() }

        player.onPlaybackEnded = { adapter.setPlayingState(null) }
        player.onPausedByFocusLoss = { adapter.setPlayingState(null) }

        loadProfiles()
    }

    override fun onDestroy() {
        player.release()
        super.onDestroy()
    }

    private fun loadProfiles() {
        binding.swipeRefresh.isRefreshing = true
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) { fetchProfiles() }
            binding.swipeRefresh.isRefreshing = false
            if (result == null) {
                Toast.makeText(this@UnidentifiedSpeakersActivity, R.string.unidentified_speakers_load_error, Toast.LENGTH_SHORT).show()
                return@launch
            }
            adapter.submitList(result)
            binding.emptyText.visibility = if (result.isEmpty()) View.VISIBLE else View.GONE
        }
    }

    private fun fetchProfiles(): List<SpeakerProfile>? {
        val url = "${BuildConfig.BACKEND_BASE_URL}/speaker-profiles?user_id=$OWNER_USER_ID"
        val request = Request.Builder().url(url).header("X-API-Key", BuildConfig.BACKEND_API_KEY).get().build()
        return try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return null
                SpeakerProfile.listFromJson(response.body?.string().orEmpty())
            }
        } catch (e: Exception) {
            null
        }
    }

    private fun togglePlaybackOf(profile: SpeakerProfile) {
        // ההקלטה שממנה נלקחה הדגימה נמחקה - אין מה לנגן, ואומרים את זה
        // במקום להשמיע שקט.
        if (!profile.hasAudio) {
            Toast.makeText(this, R.string.speaker_profile_no_audio, Toast.LENGTH_LONG).show()
            return
        }

        if (loadedProfileId == profile.profileId && player.isLoaded) {
            if (player.isPlaying) {
                stopPlayback()
            } else if (player.play()) {
                adapter.setPlayingState(profile.profileId)
                stopAtClipEnd(profile, ++playbackToken)
            }
            return
        }

        val token = ++playbackToken
        playbackJob?.cancel()
        loadedProfileId = null
        player.release()
        adapter.setPlayingState(null)

        playbackJob = lifecycleScope.launch {
            val file = withContext(Dispatchers.IO) {
                AudioCache.ensureLocal(this@UnidentifiedSpeakersActivity, profile.recordingId, profile.channel)
            }
            if (token != playbackToken) return@launch
            if (file == null) {
                Toast.makeText(this@UnidentifiedSpeakersActivity, R.string.player_error, Toast.LENGTH_SHORT).show()
                return@launch
            }

            val startMs = (profile.startSeconds * 1000).toInt()
            val loaded = withContext(Dispatchers.IO) { player.load(listOf(file), startMs) }
            if (token != playbackToken) return@launch
            if (!loaded) {
                Toast.makeText(this@UnidentifiedSpeakersActivity, R.string.player_error, Toast.LENGTH_SHORT).show()
                return@launch
            }

            loadedProfileId = profile.profileId
            if (player.play()) {
                adapter.setPlayingState(profile.profileId)
                stopAtClipEnd(profile, token)
            } else {
                Toast.makeText(this@UnidentifiedSpeakersActivity, R.string.player_no_focus, Toast.LENGTH_SHORT).show()
            }
        }
    }

    /**
     * עוצר בסוף הקטע של הדובר הזה.
     *
     * MediaPlayer לא יודע "נגן עד שנייה X", והקטע הוא חלק מקובץ ההקלטה
     * המלא - אז בלי העצירה הזו לחיצה על "נגן" בפרופיל דובר ממשיכה אל שאר
     * הפגישה, על כל שאר הדוברים שבה. זו לא בעיה אסתטית: כל הנקודה במסך
     * הזה היא לשמוע **את הקול הזה** כדי לתת לו שם.
     *
     * פרופילים ישנים נשמרו בלי סוף הקטע (clipLengthMs == 0) - שם אין מה
     * לעצור, וההתנהגות נשארת כפי שהייתה.
     */
    private fun stopAtClipEnd(profile: SpeakerProfile, token: Int) {
        if (profile.clipLengthMs <= 0) return
        // הזמן שנותר נמדד מהמיקום בפועל ולא מאורך הקטע, כדי שגם המשך אחרי
        // השהיה באמצע ייעצר בסוף הקטע ולא כעבור אורך קטע שלם ממנו.
        val remainingMs = (profile.endSeconds * 1000).toInt() - player.positionMs
        if (remainingMs <= 0) return
        lifecycleScope.launch {
            delay(remainingMs.toLong())
            if (token == playbackToken && player.isPlaying) stopPlayback()
        }
    }

    private fun stopPlayback() {
        playbackToken++
        player.pause()
        adapter.setPlayingState(null)
    }

    private fun saveName(profile: SpeakerProfile, name: String) {
        lifecycleScope.launch {
            val success = withContext(Dispatchers.IO) { patchProfileName(profile.profileId, name) }
            if (!success) {
                Toast.makeText(this@UnidentifiedSpeakersActivity, R.string.history_edit_error, Toast.LENGTH_SHORT).show()
                return@launch
            }
            if (loadedProfileId == profile.profileId) {
                stopPlayback()
                loadedProfileId = null
            }
            Toast.makeText(this@UnidentifiedSpeakersActivity, R.string.unidentified_speaker_saved, Toast.LENGTH_SHORT).show()
            loadProfiles()
        }
    }

    private fun confirmDelete(profile: SpeakerProfile) {
        AlertDialog.Builder(this)
            .setTitle(profile.name ?: getString(R.string.speaker_profile_delete_unnamed))
            .setMessage(R.string.speaker_profile_delete_confirm)
            .setPositiveButton(R.string.attach_delete) { _, _ -> deleteProfile(profile) }
            .setNegativeButton(R.string.share_cancel, null)
            .show()
    }

    private fun deleteProfile(profile: SpeakerProfile) {
        lifecycleScope.launch {
            val success = withContext(Dispatchers.IO) { deleteProfileRequest(profile.profileId) }
            if (!success) {
                Toast.makeText(this@UnidentifiedSpeakersActivity, R.string.speaker_profile_delete_error, Toast.LENGTH_SHORT).show()
                return@launch
            }
            if (loadedProfileId == profile.profileId) {
                stopPlayback()
                player.release()
                loadedProfileId = null
            }
            Toast.makeText(this@UnidentifiedSpeakersActivity, R.string.speaker_profile_deleted, Toast.LENGTH_SHORT).show()
            loadProfiles()
        }
    }

    private fun patchProfileName(profileId: String, name: String): Boolean {
        val json = JSONObject().put("name", name)
        val body = json.toString().toRequestBody("application/json; charset=utf-8".toMediaType())
        val request = Request.Builder()
            .url("${BuildConfig.BACKEND_BASE_URL}/speaker-profiles/$profileId")
            .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
            .patch(body)
            .build()
        return try {
            client.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            false
        }
    }

    private fun deleteProfileRequest(profileId: String): Boolean {
        val request = Request.Builder()
            .url("${BuildConfig.BACKEND_BASE_URL}/speaker-profiles/$profileId")
            .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
            .delete()
            .build()
        return try {
            client.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            false
        }
    }
}
