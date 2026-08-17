package com.meirww.meetingscribe

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.text.InputType
import android.view.View
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.lifecycle.lifecycleScope
import com.meirww.meetingscribe.databinding.ActivityHistoryBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.concurrent.TimeUnit

class HistoryActivity : AppCompatActivity() {

    private companion object {
        const val OWNER_USER_ID = "primary_user"
        const val DRIVE_ACCOUNT_EMAIL = "meirww@gmail.com"
    }

    private enum class SortMode { NEWEST, OLDEST, TITLE }

    private lateinit var binding: ActivityHistoryBinding

    // ה-Backend רץ על Cloud Run עם min-instances=0 (ראה
    // project_meetinglog_stuck_recordings_incident) - בקשה ראשונה אחרי חוסר
    // פעילות מעירה מופע קר, וברירת המחדל של OkHttp (10 שניות) קצרה מדי לזה:
    // עריכת דובר/מחיקה נכשלת בשקט בפעם הראשונה ומצליחה בשנייה כשהמופע כבר חם.
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()
    private val adapter = RecordingsAdapter(
        onOpenLink = ::openUrl,
        onMenuClick = ::showItemMenu,
    )

    private var allRecordings: List<RecordingItem> = emptyList()
    private var fromDate: String? = null
    private var toDate: String? = null
    private var sortMode: SortMode = SortMode.NEWEST
    private var pendingAttachTarget: RecordingItem? = null

    private val filePickerLauncher =
        registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
            handlePickedFiles(uris)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityHistoryBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.recordingsList.layoutManager = LinearLayoutManager(this)
        binding.recordingsList.adapter = adapter

        binding.backButton.setOnClickListener { finish() }
        binding.swipeRefresh.setOnRefreshListener { loadRecordings() }

        updateThemeToggleIcon()
        binding.themeToggleButton.setOnClickListener {
            ThemePrefs.setNightMode(this, !ThemePrefs.isNightMode(this))
            recreate()
        }

        binding.searchInput.addTextChangedListener(afterTextChanged = { query ->
            binding.clearSearchButton.visibility = if (query.isNotEmpty()) View.VISIBLE else View.GONE
            applyFilters()
        })
        binding.clearSearchButton.setOnClickListener { binding.searchInput.setText("") }
        binding.dateRangeButton.setOnClickListener { pickDateRange() }
        binding.sortButton.setOnClickListener { cycleSort() }
        binding.clearFiltersButton.setOnClickListener { clearFilters() }

        loadRecordings()
    }

    private fun updateThemeToggleIcon() {
        val icon = if (ThemePrefs.isNightMode(this)) R.drawable.ic_sun else R.drawable.ic_moon
        binding.themeToggleButton.setImageResource(icon)
    }

    private fun loadRecordings() {
        binding.swipeRefresh.isRefreshing = true
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                cleanupExpiredRecordings()
                fetchRecordings()
            }
            binding.swipeRefresh.isRefreshing = false
            if (result == null) {
                Toast.makeText(this@HistoryActivity, "שגיאה בטעינת ההקלטות", Toast.LENGTH_SHORT).show()
                return@launch
            }
            allRecordings = result.sortedByDescending { it.date }
            applyFilters()
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

    private fun cleanupExpiredRecordings() {
        // מוחק בשרת הקלטות קצרות מ-2 דקות שלא נערכו, 48 שעות ומעלה אחרי
        // יצירתן. נקרא בכל טעינה של המסך; כשל (למשל אין רשת) לא אמור לחסום
        // את טעינת הרשימה עצמה.
        val url = "${BuildConfig.BACKEND_BASE_URL}/recordings/cleanup?user_id=$OWNER_USER_ID"
        val request = Request.Builder()
            .url(url)
            .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
            .post("".toRequestBody())
            .build()
        try {
            client.newCall(request).execute().close()
        } catch (e: Exception) {
            // לא קריטי - הרשימה עדיין תיטען כרגיל
        }
    }

    private fun pickDateRange() {
        val calendar = Calendar.getInstance()
        val dateFormat = SimpleDateFormat("yyyy-MM-dd", Locale.US)
        android.app.DatePickerDialog(
            this,
            { _, fromYear, fromMonth, fromDay ->
                val pickedFrom = Calendar.getInstance().apply { set(fromYear, fromMonth, fromDay) }
                android.app.DatePickerDialog(
                    this,
                    { _, toYear, toMonth, toDay ->
                        val pickedTo = Calendar.getInstance().apply { set(toYear, toMonth, toDay) }
                        fromDate = dateFormat.format(pickedFrom.time)
                        toDate = dateFormat.format(pickedTo.time)
                        binding.dateRangeButton.text =
                            "${fromDate?.toDisplayDate()} - ${toDate?.toDisplayDate()}"
                        applyFilters()
                    },
                    fromYear, fromMonth, fromDay,
                ).show()
            },
            calendar.get(Calendar.YEAR),
            calendar.get(Calendar.MONTH),
            calendar.get(Calendar.DAY_OF_MONTH),
        ).show()
    }

    private fun cycleSort() {
        sortMode = when (sortMode) {
            SortMode.NEWEST -> SortMode.OLDEST
            SortMode.OLDEST -> SortMode.TITLE
            SortMode.TITLE -> SortMode.NEWEST
        }
        binding.sortButton.text = getString(
            when (sortMode) {
                SortMode.NEWEST -> R.string.history_sort_newest
                SortMode.OLDEST -> R.string.history_sort_oldest
                SortMode.TITLE -> R.string.history_sort_title_az
            }
        )
        applyFilters()
    }

    private fun clearFilters() {
        binding.searchInput.setText("")
        fromDate = null
        toDate = null
        binding.dateRangeButton.text = getString(R.string.history_date_range)
        applyFilters()
    }

    private fun applyFilters() {
        val query = binding.searchInput.text?.toString()?.trim().orEmpty()

        val filtered = allRecordings.filter { item ->
            val matchesQuery = query.isBlank() ||
                item.title.contains(query, ignoreCase = true) ||
                item.speakers.any { it.contains(query, ignoreCase = true) } ||
                item.note?.contains(query, ignoreCase = true) == true
            val matchesFrom = fromDate == null || item.date >= fromDate!!
            val matchesTo = toDate == null || item.date <= toDate!!
            matchesQuery && matchesFrom && matchesTo
        }

        val sorted = when (sortMode) {
            SortMode.NEWEST -> filtered.sortedByDescending { it.date }
            SortMode.OLDEST -> filtered.sortedBy { it.date }
            SortMode.TITLE -> filtered.sortedBy { it.title }
        }

        adapter.submitList(sorted)

        val filtersActive = query.isNotBlank() || fromDate != null || toDate != null
        binding.clearFiltersButton.visibility = if (filtersActive) View.VISIBLE else View.GONE

        binding.emptyText.text = getString(
            if (allRecordings.isEmpty()) R.string.history_empty else R.string.history_empty_filtered
        )
        binding.emptyText.visibility = if (sorted.isEmpty()) View.VISIBLE else View.GONE
        binding.resultsCountText.visibility = if (sorted.isEmpty()) View.GONE else View.VISIBLE
        binding.resultsCountText.text = getString(R.string.history_results_count, sorted.size)
    }

    private fun openUrl(url: String) {
        var uri = Uri.parse(url)
        val isGoogleLink = uri.host?.endsWith("google.com") == true
        if (isGoogleLink && uri.getQueryParameter("authuser") == null) {
            uri = uri.buildUpon().appendQueryParameter("authuser", DRIVE_ACCOUNT_EMAIL).build()
        }
        val intent = Intent(Intent.ACTION_VIEW, uri)
        if (isGoogleLink) {
            // Force Chrome instead of the native Drive/Docs app: those apps ignore
            // ?authuser= and show their own account-chooser dialog every time.
            intent.setPackage("com.android.chrome")
        }
        try {
            startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            intent.setPackage(null)
            startActivity(intent)
        }
    }

    private fun showItemMenu(anchor: View, item: RecordingItem) {
        val popup = PopupMenu(this, anchor)
        val actions = mutableMapOf<Int, () -> Unit>()
        var nextId = 0

        fun addAction(title: String, action: () -> Unit) {
            val id = nextId++
            popup.menu.add(0, id, id, title)
            actions[id] = action
        }

        // בהקלטה שנכשלה אין קישורים ואין מה לערוך - הפעולה היחידה שמעניינת
        // היא לנסות שוב, והיא ראשונה ברשימה.
        if (item.failed) addAction(getString(R.string.history_retry)) { retryProcessing(item) }
        item.folderUrl?.let { url -> addAction(getString(R.string.history_open_folder)) { openUrl(url) } }
        item.transcriptUrl?.let { url -> addAction(getString(R.string.link_transcript)) { openUrl(url) } }
        item.summaryUrl?.let { url -> addAction(getString(R.string.link_summary)) { openUrl(url) } }
        item.todoUrl?.let { url -> addAction(getString(R.string.link_todo)) { openUrl(url) } }
        item.audioUrl?.let { url -> addAction(getString(R.string.link_audio)) { openUrl(url) } }
        if (!item.failed) {
            if (item.attachments.isNotEmpty()) {
                addAction(getString(R.string.attach_menu_item, item.attachments.size)) {
                    showAttachmentsDialog(item)
                }
            }
            addAction(getString(R.string.attach_file)) { onAttachFileClicked(item) }
            addAction(getString(R.string.history_edit_title)) { showEditTitleDialog(item) }
            addAction(getString(R.string.history_edit_speakers)) { showEditSpeakersDialog(item) }
            addAction(getString(R.string.history_edit_note)) { showEditNoteDialog(item) }
        }
        addAction(getString(R.string.history_delete)) { showDeleteConfirmDialog(item) }

        popup.setOnMenuItemClickListener { menuItem ->
            actions[menuItem.itemId]?.invoke()
            true
        }
        popup.show()
    }

    private fun dialogPadding(view: View): View =
        android.widget.FrameLayout(this).apply {
            val padding = (24 * resources.displayMetrics.density).toInt()
            setPadding(padding, padding, padding, 0)
            addView(view)
        }

    private fun showEditTitleDialog(item: RecordingItem) {
        val input = EditText(this).apply {
            setText(item.title)
            setSelection(text.length)
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.history_edit_title)
            .setView(dialogPadding(input))
            .setPositiveButton(R.string.history_save) { _, _ ->
                val newTitle = input.text.toString().trim()
                if (newTitle.isNotBlank() && newTitle != item.title) {
                    runPatch(item.recordingId, title = newTitle)
                }
            }
            .setNegativeButton(R.string.share_cancel, null)
            .show()
    }

    private fun showEditNoteDialog(item: RecordingItem) {
        val input = EditText(this).apply {
            setText(item.note.orEmpty())
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 3
            setSelection(text.length)
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.history_edit_note)
            .setView(dialogPadding(input))
            .setPositiveButton(R.string.history_save) { _, _ ->
                runPatch(item.recordingId, note = input.text.toString().trim())
            }
            .setNegativeButton(R.string.share_cancel, null)
            .show()
    }

    private fun showEditSpeakersDialog(item: RecordingItem) {
        if (item.speakers.isEmpty()) {
            Toast.makeText(this, R.string.history_no_speakers, Toast.LENGTH_SHORT).show()
            return
        }
        val container = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

        // הרשימה מגיעה מהשרת לפי סדר הופעת הדוברים בתמלול (ראה
        // speakers.speakers_in_order), והמשתמש ממלא אותה לפי אותו סדר -
        // לכן הסדר מוצג במפורש, וכל שדה נושא מעליו את התווית שהוא מחליף.
        container.addView(TextView(this).apply {
            setText(R.string.history_edit_speakers_hint)
            setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
            textSize = 13f
        })

        val inputsBySpeaker = item.speakers.associateWith { speaker ->
            EditText(this).apply { setText(speaker) }
        }
        inputsBySpeaker.entries.forEachIndexed { index, (speaker, input) ->
            container.addView(TextView(this).apply {
                text = getString(R.string.history_speaker_position, index + 1, speaker)
                setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
                textSize = 12f
                setPadding(0, (12 * resources.displayMetrics.density).toInt(), 0, 0)
            })
            container.addView(input)
        }

        AlertDialog.Builder(this)
            .setTitle(R.string.history_edit_speakers)
            .setView(dialogPadding(container))
            .setPositiveButton(R.string.history_save) { _, _ ->
                val renames = inputsBySpeaker.mapNotNull { (oldName, input) ->
                    val newName = input.text.toString().trim()
                    if (newName.isNotBlank() && newName != oldName) oldName to newName else null
                }.toMap()
                if (renames.isNotEmpty()) {
                    runPatch(item.recordingId, speakerRenames = renames)
                }
            }
            .setNegativeButton(R.string.share_cancel, null)
            .show()
    }

    /**
     * מייבא מחדש מ-cally את השיחה שהעיבוד שלה נכשל ושולח אותה שוב.
     *
     * העותק המקומי כבר נמחק (הוא נמחק ברגע שהשרת קלט את ההעלאה), אבל cally
     * שומרת את המקור - ולכן די לבטל את סימון הייבוא. ההעלאה החוזרת נושאת את
     * אותו client_upload_id ולכן נופלת על אותה רשומה בשרת, בלי כפילות.
     */
    private fun retryProcessing(item: RecordingItem) {
        val started = CallImportWorker.retryFromCally(this, item.recordingId)
        Toast.makeText(
            this,
            if (started) R.string.history_retry_started else R.string.history_retry_no_audio,
            Toast.LENGTH_LONG,
        ).show()
    }

    private fun showDeleteConfirmDialog(item: RecordingItem) {
        AlertDialog.Builder(this)
            .setTitle(R.string.history_delete)
            .setMessage(getString(R.string.history_delete_confirm, item.title))
            .setPositiveButton(R.string.history_delete) { _, _ -> runDelete(item.recordingId) }
            .setNegativeButton(R.string.share_cancel, null)
            .show()
    }

    private fun runPatch(
        recordingId: String,
        title: String? = null,
        speakerRenames: Map<String, String>? = null,
        note: String? = null,
    ) {
        lifecycleScope.launch {
            val success = withContext(Dispatchers.IO) {
                patchRecording(recordingId, title, speakerRenames, note)
            }
            if (success) {
                loadRecordings()
            } else {
                Toast.makeText(this@HistoryActivity, R.string.history_edit_error, Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun runDelete(recordingId: String) {
        lifecycleScope.launch {
            val success = withContext(Dispatchers.IO) { deleteRecording(recordingId) }
            if (success) {
                Toast.makeText(this@HistoryActivity, R.string.history_delete_success, Toast.LENGTH_SHORT).show()
                loadRecordings()
            } else {
                Toast.makeText(this@HistoryActivity, R.string.history_edit_error, Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun patchRecording(
        recordingId: String,
        title: String?,
        speakerRenames: Map<String, String>?,
        note: String?,
    ): Boolean {
        val json = JSONObject()
        title?.let { json.put("title", it) }
        speakerRenames?.let { renames ->
            val renamesJson = JSONObject()
            renames.forEach { (oldName, newName) -> renamesJson.put(oldName, newName) }
            json.put("speaker_renames", renamesJson)
        }
        note?.let { json.put("note", it) }

        val body = json.toString().toRequestBody("application/json; charset=utf-8".toMediaType())
        val request = Request.Builder()
            .url("${BuildConfig.BACKEND_BASE_URL}/recordings/$recordingId")
            .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
            .patch(body)
            .build()
        return try {
            client.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            false
        }
    }

    private fun deleteRecording(recordingId: String): Boolean {
        val request = Request.Builder()
            .url("${BuildConfig.BACKEND_BASE_URL}/recordings/$recordingId")
            .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
            .delete()
            .build()
        return try {
            client.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            false
        }
    }

    private fun onAttachFileClicked(item: RecordingItem) {
        pendingAttachTarget = item
        filePickerLauncher.launch(arrayOf("*/*"))
    }

    private fun handlePickedFiles(uris: List<Uri>) {
        val target = pendingAttachTarget ?: return
        if (uris.isEmpty()) return

        Toast.makeText(this, R.string.attach_uploading, Toast.LENGTH_SHORT).show()

        lifecycleScope.launch {
            val success = withContext(Dispatchers.IO) { uploadAttachments(target.recordingId, uris) }
            Toast.makeText(
                this@HistoryActivity,
                if (success) R.string.attach_success else R.string.attach_error,
                Toast.LENGTH_SHORT,
            ).show()
        }
    }

    private fun uploadAttachments(recordingId: String, uris: List<Uri>): Boolean {
        val tempFiles = mutableListOf<File>()
        return try {
            val multipartBuilder = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart("user_id", OWNER_USER_ID)

            uris.forEach { uri ->
                val name = queryFileName(uri) ?: "attachment_${System.currentTimeMillis()}"
                val mimeType = contentResolver.getType(uri) ?: "application/octet-stream"
                val tempFile = File(cacheDir, "${System.currentTimeMillis()}_$name")
                contentResolver.openInputStream(uri)?.use { input ->
                    tempFile.outputStream().use { output -> input.copyTo(output) }
                }
                tempFiles.add(tempFile)
                multipartBuilder.addFormDataPart(
                    "files", name, tempFile.asRequestBody(mimeType.toMediaType())
                )
            }

            val request = Request.Builder()
                .url("${BuildConfig.BACKEND_BASE_URL}/recordings/$recordingId/attachments")
                .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
                .post(multipartBuilder.build())
                .build()

            client.newCall(request).execute().use { response -> response.isSuccessful }
        } catch (e: Exception) {
            false
        } finally {
            tempFiles.forEach { it.delete() }
        }
    }

    private fun queryFileName(uri: Uri): String? {
        contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (idx >= 0 && cursor.moveToFirst()) return cursor.getString(idx)
        }
        return null
    }

    /**
     * רשימת המצורפים של הקלטה, עם פתיחה/ניסיון חוזר/מחיקה לכל אחד. הדיאלוג
     * נבנה לפני שהשורות מתווספות אליו כדי שלחיצה על "נסה שוב"/"מחק" תוכל
     * לסגור אותו מיד - בלי זה המשתמש היה רואה שורה "נכשל" שהוא כבר לחץ
     * "נסה שוב" עליה, עד שהוא סוגר ופותח את התפריט מחדש.
     */
    private fun showAttachmentsDialog(item: RecordingItem) {
        val container = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val dialog = AlertDialog.Builder(this)
            .setTitle(getString(R.string.attach_menu_item, item.attachments.size))
            .setView(android.widget.ScrollView(this).apply { addView(dialogPadding(container)) })
            .setNegativeButton(R.string.share_cancel, null)
            .create()

        item.attachments.forEachIndexed { index, attachment ->
            if (index > 0) container.addView(dividerView())
            container.addView(buildAttachmentRow(item.recordingId, attachment, dialog))
        }

        dialog.show()
    }

    private fun dividerView(): View = View(this).apply {
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, (1 * resources.displayMetrics.density).toInt()
        )
        setBackgroundColor(ContextCompat.getColor(context, R.color.surface_stroke))
    }

    private fun buildAttachmentRow(recordingId: String, attachment: Attachment, dialog: AlertDialog): View {
        val density = resources.displayMetrics.density
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, (10 * density).toInt(), 0, (10 * density).toInt())
        }

        column.addView(TextView(this).apply {
            text = attachment.filename
            setTextColor(ContextCompat.getColor(context, R.color.text_primary))
            textSize = 14f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })

        val statusText = when {
            attachment.isProcessing -> getString(R.string.attach_status_processing)
            attachment.isFailed -> "⚠️ ${attachment.error.orEmpty()}"
            else -> getString(R.string.attach_status_done)
        }
        column.addView(TextView(this).apply {
            text = statusText
            setTextColor(
                ContextCompat.getColor(
                    context, if (attachment.isFailed) R.color.accent_red else R.color.text_secondary
                )
            )
            textSize = 12f
            setPadding(0, (2 * density).toInt(), 0, 0)
        })

        val actionsRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, (6 * density).toInt(), 0, 0)
        }

        fun addAction(label: String, colorRes: Int, onClick: () -> Unit) {
            actionsRow.addView(TextView(this@HistoryActivity).apply {
                text = label
                setTextColor(ContextCompat.getColor(context, colorRes))
                textSize = 13f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setPadding(0, 0, (20 * density).toInt(), 0)
                setOnClickListener { onClick() }
            })
        }

        attachment.driveUrl?.let { url ->
            addAction(getString(R.string.attach_open), R.color.accent_cyan) { openUrl(url) }
        }
        if (attachment.isFailed) {
            addAction(getString(R.string.attach_retry), R.color.accent_violet) {
                dialog.dismiss()
                runAttachmentRetry(recordingId, attachment.attachmentId)
            }
        }
        addAction(getString(R.string.attach_delete), R.color.accent_red) {
            confirmDeleteAttachment(recordingId, attachment, dialog)
        }

        column.addView(actionsRow)
        return column
    }

    private fun confirmDeleteAttachment(recordingId: String, attachment: Attachment, parentDialog: AlertDialog) {
        AlertDialog.Builder(this)
            .setMessage(getString(R.string.attach_delete_confirm, attachment.filename))
            .setPositiveButton(R.string.attach_delete) { _, _ ->
                parentDialog.dismiss()
                runAttachmentDelete(recordingId, attachment.attachmentId)
            }
            .setNegativeButton(R.string.share_cancel, null)
            .show()
    }

    private fun runAttachmentRetry(recordingId: String, attachmentId: String) {
        lifecycleScope.launch {
            val success = withContext(Dispatchers.IO) { retryAttachmentRequest(recordingId, attachmentId) }
            Toast.makeText(
                this@HistoryActivity,
                if (success) R.string.attach_retry_started else R.string.attach_action_error,
                Toast.LENGTH_SHORT,
            ).show()
            if (success) loadRecordings()
        }
    }

    private fun runAttachmentDelete(recordingId: String, attachmentId: String) {
        lifecycleScope.launch {
            val success = withContext(Dispatchers.IO) { deleteAttachmentRequest(recordingId, attachmentId) }
            Toast.makeText(
                this@HistoryActivity,
                if (success) R.string.attach_delete_success else R.string.attach_action_error,
                Toast.LENGTH_SHORT,
            ).show()
            if (success) loadRecordings()
        }
    }

    private fun retryAttachmentRequest(recordingId: String, attachmentId: String): Boolean {
        val request = Request.Builder()
            .url("${BuildConfig.BACKEND_BASE_URL}/recordings/$recordingId/attachments/$attachmentId/retry")
            .header("X-API-Key", BuildConfig.BACKEND_API_KEY)
            .post("".toRequestBody())
            .build()
        return try {
            client.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            false
        }
    }

    private fun deleteAttachmentRequest(recordingId: String, attachmentId: String): Boolean {
        val request = Request.Builder()
            .url("${BuildConfig.BACKEND_BASE_URL}/recordings/$recordingId/attachments/$attachmentId")
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

private fun android.widget.EditText.addTextChangedListener(afterTextChanged: (String) -> Unit) {
    addTextChangedListener(object : android.text.TextWatcher {
        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
        override fun afterTextChanged(s: android.text.Editable?) {
            afterTextChanged(s?.toString().orEmpty())
        }
    })
}
