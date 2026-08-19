package com.meirww.meetingscribe

import android.content.res.ColorStateList
import android.os.Bundle
import android.view.View
import android.widget.SeekBar
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.chip.Chip
import com.meirww.meetingscribe.databinding.ActivityChatBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

class ChatActivity : AppCompatActivity() {

    private companion object {
        const val OWNER_USER_ID = "primary_user"

        /** קצב רענון סרגל ההתקדמות של הנגן. */
        const val PROGRESS_TICK_MS = 400L
    }

    private lateinit var binding: ActivityChatBinding

    // כמו ב-HistoryActivity: Cloud Run רץ עם min-instances=0, אז בקשה אחרי
    // חוסר פעילות מעירה מופע קר. readTimeout ארוך יותר מהרגיל כי /chat היא
    // תשובת Gemini מלאה (לא סטרימינג) שיכולה לקחת זמן גם כשהמופע כבר חם.
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()
    private val adapter = ChatAdapter(onPlayRequest = ::requestPlayback)
    private val messages = mutableListOf<ChatMessage>()

    private var allRecordings: List<RecordingItem> = emptyList()
    private val selectedRecordingIds = mutableSetOf<String>()
    private var recordingsLoadFailed = false

    private val player by lazy { RecordingPlayer(this) }
    private var playbackJob: Job? = null
    private var progressJob: Job? = null
    private var isSeeking = false

    /**
     * מזהה בקשת הניגון הנוכחית. הורדה שכבר רצה לא נעצרת באמצע (היא נשמרת
     * למטמון בכל מקרה), ובלי המונה הזה היא הייתה ממשיכה לעדכן את הנגן אחרי
     * שהמשתמש כבר הקיש על ציטוט אחר.
     */
    private var playbackToken = 0

    /** ההקלטה שכרגע טעונה בנגן - הקשה נוספת עליה היא קפיצה, לא טעינה. */
    private var loadedRecordingId: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityChatBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.chatList.layoutManager = LinearLayoutManager(this)
        binding.chatList.adapter = adapter

        binding.backButton.setOnClickListener { finish() }
        binding.sendButton.setOnClickListener { sendQuestion() }
        binding.selectionCard.setOnClickListener { openRecordingPicker() }

        setUpPlayerControls()

        binding.selectionSubtitle.setText(R.string.chat_selection_loading)
        loadRecordings()
    }

    override fun onDestroy() {
        player.release()
        super.onDestroy()
    }

    private fun loadRecordings() {
        lifecycleScope.launch { refreshRecordings() }
    }

    private suspend fun refreshRecordings() {
        val result = withContext(Dispatchers.IO) { fetchRecordings() }
        recordingsLoadFailed = result == null
        allRecordings = (result ?: emptyList()).sortedByDescending { it.date }
        // בחירות שהצביעו על הקלטות שנמחקו בינתיים כבר לא רלוונטיות
        val availableIds = allRecordings.map { it.recordingId }.toSet()
        selectedRecordingIds.retainAll(availableIds)
        renderSelection()
    }

    private fun fetchRecordings(): List<RecordingItem>? {
        val url = "${BuildConfig.BACKEND_BASE_URL}/recordings?user_id=$OWNER_USER_ID"
        val request = Request.Builder().url(url).header("X-API-Key", BuildConfig.BACKEND_API_KEY).get().build()
        return try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return null
                RecordingItem.listFromJson(response.body?.string().orEmpty())
            }
        } catch (e: Exception) {
            null
        }
    }

    /** פותח את בורר ההקלטות; אם הרשימה עוד לא נטענה - מנסה לטעון קודם. */
    private fun openRecordingPicker() {
        if (allRecordings.isNotEmpty()) {
            showRecordingPicker()
            return
        }
        binding.selectionSubtitle.setText(R.string.chat_selection_loading)
        lifecycleScope.launch {
            refreshRecordings()
            if (allRecordings.isNotEmpty()) {
                showRecordingPicker()
            } else {
                Toast.makeText(
                    this@ChatActivity,
                    if (recordingsLoadFailed) R.string.chat_selection_error else R.string.chat_selection_empty,
                    Toast.LENGTH_SHORT,
                ).show()
            }
        }
    }

    private fun showRecordingPicker() {
        RecordingPickerSheet(
            context = this,
            recordings = allRecordings,
            initialSelection = selectedRecordingIds.toSet(),
        ) { newSelection ->
            selectedRecordingIds.clear()
            selectedRecordingIds.addAll(newSelection)
            renderSelection()
        }.show()
    }

    /** מעדכן את כרטיס הבחירה ואת שבבי ההקלטות שנבחרו. */
    private fun renderSelection() {
        val selectedItems = allRecordings.filter { selectedRecordingIds.contains(it.recordingId) }

        binding.selectionTitle.text = when {
            selectedItems.isEmpty() -> getString(R.string.chat_pick_recordings)
            selectedItems.size == 1 -> getString(R.string.chat_selection_count_one)
            else -> getString(R.string.chat_selection_count, selectedItems.size)
        }

        binding.selectionSubtitle.text = when {
            selectedItems.isNotEmpty() -> selectedItems.joinToString("  ·  ") { it.title }
            recordingsLoadFailed -> getString(R.string.chat_selection_error)
            allRecordings.isEmpty() -> getString(R.string.chat_selection_empty)
            allRecordings.size == 1 -> getString(R.string.chat_selection_available_one)
            else -> getString(R.string.chat_selection_available, allRecordings.size)
        }

        binding.recordingsChipGroup.removeAllViews()
        selectedItems.forEach { recording ->
            val chip = Chip(this).apply {
                text = getString(
                    R.string.chat_chip_label,
                    recording.title,
                    recording.date.toShortDisplayDate(),
                )
                // צבעים מפורשים כדי שהשבב יישאר קריא גם במצב כהה וגם בהיר
                setTextColor(ContextCompat.getColor(context, R.color.text_primary))
                chipBackgroundColor =
                    ColorStateList.valueOf(ContextCompat.getColor(context, R.color.surface))
                chipStrokeWidth = resources.displayMetrics.density
                chipStrokeColor =
                    ColorStateList.valueOf(ContextCompat.getColor(context, R.color.accent_cyan_dim))
                isCloseIconVisible = true
                closeIconTint =
                    ColorStateList.valueOf(ContextCompat.getColor(context, R.color.text_secondary))
                closeIconContentDescription = getString(R.string.chat_chip_remove)
                tag = recording.recordingId
                setOnCloseIconClickListener {
                    selectedRecordingIds.remove(recording.recordingId)
                    renderSelection()
                }
                setOnClickListener { showRecordingPicker() }
            }
            binding.recordingsChipGroup.addView(chip)
        }
        binding.recordingsScroll.visibility =
            if (selectedItems.isEmpty()) View.GONE else View.VISIBLE
    }

    private fun sendQuestion() {
        val question = binding.questionInput.text?.toString()?.trim().orEmpty()
        if (question.isBlank()) return

        if (selectedRecordingIds.isEmpty()) {
            Toast.makeText(this, R.string.chat_no_selection, Toast.LENGTH_SHORT).show()
            openRecordingPicker()
            return
        }

        messages.add(ChatMessage(isUser = true, text = question))
        adapter.submitList(messages.toList())
        binding.chatList.scrollToPosition(messages.size - 1)
        binding.questionInput.setText("")

        lifecycleScope.launch {
            val answer = withContext(Dispatchers.IO) {
                askQuestion(selectedRecordingIds.toList(), question)
            }
            val message = answer ?: ChatMessage(
                isUser = false,
                text = getString(R.string.chat_error),
            )
            messages.add(message)
            adapter.submitList(messages.toList())
            binding.chatList.scrollToPosition(messages.size - 1)
        }
    }

    private fun askQuestion(recordingIds: List<String>, question: String): ChatMessage? {
        val body = JSONObject().apply {
            put("recording_ids", JSONArray(recordingIds))
            put("question", question)
        }.toString().toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("${BuildConfig.BACKEND_BASE_URL}/chat")
            .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
            .post(body)
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return null
                val json = JSONObject(response.body?.string().orEmpty())
                val citations = mutableListOf<Citation>()
                json.optJSONArray("citations")?.let { arr ->
                    for (i in 0 until arr.length()) {
                        val c = arr.getJSONObject(i)
                        citations.add(
                            Citation(
                                recordingTitle = c.optString("recording_title"),
                                timestamp = c.optString("timestamp"),
                                quote = c.optString("quote"),
                                // שני אלה הם מה שהופך את הציטוט ללחיץ; השרת
                                // משאיר אותם ריקים כשהוא לא ודאי באיזו הקלטה
                                // או באיזה רגע מדובר (ראה pipeline/chat.py).
                                recordingId = c.optStringOrNull("recording_id"),
                                startSeconds = c.optDoubleOrNull("start_seconds"),
                            )
                        )
                    }
                }
                ChatMessage(
                    isUser = false,
                    text = json.optString("answer"),
                    citations = citations,
                )
            }
        } catch (e: Exception) {
            null
        }
    }

    // ---------- ניגון הרגע שצוטט ----------

    private fun setUpPlayerControls() {
        player.onPlaybackEnded = { updatePlayPauseIcon(playing = false) }
        player.onPausedByFocusLoss = { updatePlayPauseIcon(playing = false) }

        binding.playerBar.playPauseButton.setOnClickListener { togglePlayPause() }
        binding.playerBar.playerClose.setOnClickListener { closePlayer() }
        binding.playerBar.playerSeek.setOnSeekBarChangeListener(
            object : SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(bar: SeekBar, progress: Int, fromUser: Boolean) {
                    if (fromUser) {
                        binding.playerBar.playerPosition.text = formatDuration(progress / 1000.0)
                    }
                }

                override fun onStartTrackingTouch(bar: SeekBar) {
                    isSeeking = true
                }

                override fun onStopTrackingTouch(bar: SeekBar) {
                    isSeeking = false
                    player.seekTo(bar.progress)
                }
            }
        )
    }

    /**
     * המשתמש הקיש על זמן בתשובה או על שורת מקור. [recordingId] הוא null כשאי
     * אפשר היה להסיק מהתשובה על איזו הקלטה מדובר - אז מכריעים לפי הבחירה
     * שבראש המסך, ואם גם היא לא חד-משמעית, שואלים.
     */
    private fun requestPlayback(recordingId: String?, seconds: Double) {
        // ניגון ברמקול בזמן שהמיקרופון פתוח היה נכנס לתוך ההקלטה עצמה
        // ומזהם אותה - ההקלטה החיה קודמת להאזנה.
        if (RecordingSessionState.liveSession(this) != null) {
            Toast.makeText(this, R.string.player_blocked_while_recording, Toast.LENGTH_LONG).show()
            return
        }

        val resolved = recordingId ?: soleSelectedRecordingId()
        if (resolved == null) {
            askWhichRecording(seconds)
            return
        }
        startPlaybackOf(resolved, seconds)
    }

    private fun soleSelectedRecordingId(): String? =
        allRecordings.map { it.recordingId }
            .filter { selectedRecordingIds.contains(it) }
            .singleOrNull()

    private fun askWhichRecording(seconds: Double) {
        val options = allRecordings.filter { selectedRecordingIds.contains(it.recordingId) }
        if (options.isEmpty()) {
            Toast.makeText(this, R.string.chat_no_selection, Toast.LENGTH_SHORT).show()
            return
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.player_pick_recording)
            .setItems(options.map { it.title }.toTypedArray()) { _, which ->
                startPlaybackOf(options[which].recordingId, seconds)
            }
            .show()
    }

    private fun startPlaybackOf(recordingId: String, seconds: Double) {
        val recording = allRecordings.firstOrNull { it.recordingId == recordingId }
        if (recording == null || recording.audioChannelCount == 0) {
            Toast.makeText(this, R.string.player_missing_audio, Toast.LENGTH_SHORT).show()
            return
        }

        val startMs = (seconds * 1000).toInt()

        // אותה הקלטה כבר טעונה (הקשה על זמן שני באותה תשובה) - קפיצה מיידית,
        // בלי לפרק ולהרכיב את הנגן מחדש.
        if (loadedRecordingId == recordingId && player.isLoaded) {
            player.seekTo(startMs)
            binding.playerBar.playerStatus.text =
                getString(R.string.player_from_time, formatDuration(seconds))
            resumePlayback()
            return
        }

        val token = ++playbackToken
        playbackJob?.cancel()
        progressJob?.cancel()
        loadedRecordingId = null
        player.release()

        showPlayerBar(recording.title, getString(R.string.player_preparing))

        playbackJob = lifecycleScope.launch {
            val files = downloadChannels(recording, token)
            if (token != playbackToken) return@launch
            if (files.isEmpty()) {
                binding.playerBar.playerStatus.setText(R.string.player_error)
                return@launch
            }

            val loaded = withContext(Dispatchers.IO) { player.load(files, startMs) }
            if (token != playbackToken) return@launch
            if (!loaded) {
                binding.playerBar.playerStatus.setText(R.string.player_error)
                return@launch
            }

            loadedRecordingId = recording.recordingId
            binding.playerBar.playerSeek.isEnabled = true
            binding.playerBar.playerSeek.max = player.durationMs
            binding.playerBar.playerSeek.progress = player.positionMs
            binding.playerBar.playerPosition.text = formatDuration(player.positionMs / 1000.0)
            binding.playerBar.playerDuration.text = formatDuration(player.durationMs / 1000.0)
            binding.playerBar.playerStatus.text =
                getString(R.string.player_from_time, formatDuration(seconds))
            resumePlayback()
        }
    }

    /**
     * מוריד את כל ערוצי ההקלטה (ראה [RecordingPlayer] לגבי שיחה בשני ערוצים).
     * ערוץ נוסף שנכשל לא מבטל את הניגון - עדיף להשמיע צד אחד מכלום; ערוץ 0
     * שנכשל כן, כי הוא ההקלטה עצמה.
     */
    private suspend fun downloadChannels(recording: RecordingItem, token: Int): List<File> {
        val channels = recording.audioChannelCount
        val files = mutableListOf<File>()

        for (channel in 0 until channels) {
            var lastPercent = -1
            val file = withContext(Dispatchers.IO) {
                AudioCache.ensureLocal(
                    this@ChatActivity, recording.recordingId, channel
                ) { downloaded, size ->
                    if (size <= 0) return@ensureLocal
                    val done = (channel + downloaded.toDouble() / size) / channels
                    val percent = (done * 100).toInt()
                    if (percent != lastPercent) {
                        lastPercent = percent
                        showDownloadProgress(token, percent)
                    }
                }
            }
            if (file != null) {
                files.add(file)
            } else if (channel == 0) {
                return emptyList()
            }
        }
        return files
    }

    private fun showDownloadProgress(token: Int, percent: Int) {
        runOnUiThread {
            if (isFinishing || isDestroyed || token != playbackToken) return@runOnUiThread
            binding.playerBar.playerStatus.text = getString(R.string.player_downloading, percent)
        }
    }

    private fun togglePlayPause() {
        if (!player.isLoaded) return
        if (player.isPlaying) {
            player.pause()
            updatePlayPauseIcon(playing = false)
        } else {
            resumePlayback()
        }
    }

    private fun resumePlayback() {
        if (!player.play()) {
            Toast.makeText(this, R.string.player_no_focus, Toast.LENGTH_SHORT).show()
            return
        }
        updatePlayPauseIcon(playing = true)
        startProgressTicker()
    }

    private fun startProgressTicker() {
        progressJob?.cancel()
        progressJob = lifecycleScope.launch {
            while (isActive) {
                if (player.isLoaded && !isSeeking) {
                    binding.playerBar.playerSeek.progress = player.positionMs
                    binding.playerBar.playerPosition.text =
                        formatDuration(player.positionMs / 1000.0)
                }
                delay(PROGRESS_TICK_MS)
            }
        }
    }

    private fun showPlayerBar(title: String, status: String) {
        binding.playerBar.root.visibility = View.VISIBLE
        binding.playerBar.playerTitle.text = title
        binding.playerBar.playerStatus.text = status
        binding.playerBar.playerSeek.isEnabled = false
        binding.playerBar.playerSeek.progress = 0
        binding.playerBar.playerPosition.text = formatDuration(0.0)
        binding.playerBar.playerDuration.text = ""
        updatePlayPauseIcon(playing = false)
    }

    private fun updatePlayPauseIcon(playing: Boolean) {
        binding.playerBar.playPauseIcon.setImageResource(
            if (playing) R.drawable.ic_pause else R.drawable.ic_play
        )
        binding.playerBar.playPauseIcon.contentDescription =
            getString(if (playing) R.string.player_pause else R.string.player_play)
    }

    private fun closePlayer() {
        playbackToken++
        playbackJob?.cancel()
        progressJob?.cancel()
        loadedRecordingId = null
        player.release()
        binding.playerBar.root.visibility = View.GONE
    }
}
