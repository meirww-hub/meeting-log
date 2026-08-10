package com.meirww.meetingscribe

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.RecognizerIntent
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.chip.Chip
import com.meirww.meetingscribe.databinding.ActivityChatBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

class ChatActivity : AppCompatActivity() {

    private companion object {
        const val OWNER_USER_ID = "primary_user"
    }

    private lateinit var binding: ActivityChatBinding
    private val client = OkHttpClient()
    private val adapter = ChatAdapter()
    private val messages = mutableListOf<ChatMessage>()

    private var allRecordings: List<RecordingItem> = emptyList()
    private val selectedRecordingIds = mutableSetOf<String>()

    private val speechRecognizerLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val spokenText = result.data
                ?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
                ?.firstOrNull()
            if (!spokenText.isNullOrBlank()) {
                val existing = binding.questionInput.text?.toString().orEmpty()
                val combined = if (existing.isBlank()) spokenText else "$existing $spokenText"
                binding.questionInput.setText(combined)
                binding.questionInput.setSelection(combined.length)
            }
        }

    private val requestMicPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startVoiceInput() else {
                Toast.makeText(this, R.string.chat_voice_permission_needed, Toast.LENGTH_SHORT).show()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityChatBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.chatList.layoutManager = LinearLayoutManager(this)
        binding.chatList.adapter = adapter

        binding.backButton.setOnClickListener { finish() }
        binding.sendButton.setOnClickListener { sendQuestion() }
        binding.micButton.setOnClickListener { ensureMicPermissionAndListen() }

        loadRecordings()
    }

    private fun ensureMicPermissionAndListen() {
        val hasPermission = ContextCompat.checkSelfPermission(
            this, Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED

        if (hasPermission) startVoiceInput() else {
            requestMicPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun startVoiceInput() {
        val intent = android.content.Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale("he", "IL"))
            putExtra(RecognizerIntent.EXTRA_PROMPT, getString(R.string.chat_voice_input))
        }
        try {
            speechRecognizerLauncher.launch(intent)
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, R.string.chat_voice_unavailable, Toast.LENGTH_SHORT).show()
        }
    }

    private fun loadRecordings() {
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) { fetchRecordings() }
            if (result.isNullOrEmpty()) return@launch
            allRecordings = result
            buildRecordingChips()
        }
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

    private fun buildRecordingChips() {
        binding.recordingsChipGroup.removeAllViews()
        allRecordings.sortedByDescending { it.date }.forEach { recording ->
            val chip = Chip(this).apply {
                text = recording.title
                isCheckable = true
                tag = recording.recordingId
                setOnCheckedChangeListener { _, isChecked ->
                    if (isChecked) selectedRecordingIds.add(recording.recordingId)
                    else selectedRecordingIds.remove(recording.recordingId)
                }
            }
            binding.recordingsChipGroup.addView(chip)
        }
    }

    private fun sendQuestion() {
        val question = binding.questionInput.text?.toString()?.trim().orEmpty()
        if (question.isBlank()) return

        if (selectedRecordingIds.isEmpty()) {
            Toast.makeText(this, R.string.chat_no_selection, Toast.LENGTH_SHORT).show()
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
}
