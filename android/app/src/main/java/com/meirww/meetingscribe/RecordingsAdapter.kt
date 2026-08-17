package com.meirww.meetingscribe

import android.content.res.ColorStateList
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView

class RecordingsAdapter(
    private var items: List<RecordingItem> = emptyList(),
    private val onOpenLink: (String) -> Unit,
    private val onMenuClick: (View, RecordingItem) -> Unit,
) : RecyclerView.Adapter<RecordingsAdapter.ViewHolder>() {

    fun submitList(newItems: List<RecordingItem>) {
        items = newItems
        notifyDataSetChanged()
    }

    class ViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val root: View = view.findViewById(R.id.itemRoot)
        val statusDot: View = view.findViewById(R.id.statusDot)
        val title: android.widget.TextView = view.findViewById(R.id.itemTitle)
        val duration: android.widget.TextView = view.findViewById(R.id.itemDuration)
        val subtitle: android.widget.TextView = view.findViewById(R.id.itemSubtitle)
        val note: android.widget.TextView = view.findViewById(R.id.itemNote)
        val menuButton: android.widget.TextView = view.findViewById(R.id.menuButton)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_recording, parent, false)
        return ViewHolder(view)
    }

    override fun getItemCount() = items.size

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = items[position]
        val context = holder.root.context
        val isReady = item.transcriptUrl != null && item.summaryUrl != null
        val statusLabel = context.getString(
            when {
                item.failed -> R.string.history_status_failed
                isReady -> R.string.history_status_ready
                else -> R.string.history_status_processing
            }
        )

        holder.title.text = item.title
        if (item.durationSeconds > 0) {
            holder.duration.visibility = View.VISIBLE
            holder.duration.text = formatDuration(item.durationSeconds)
        } else {
            holder.duration.visibility = View.GONE
        }
        val dateLabel = item.createdAt.toDisplayTime()?.let { "${item.date.toDisplayDate()}  $it" }
            ?: item.date.toDisplayDate()
        val parts = mutableListOf(dateLabel)
        if (item.speakers.isNotEmpty()) parts.add(item.speakers.joinToString(", "))
        parts.add(statusLabel)
        if (item.attachments.isNotEmpty()) {
            val badgeRes = if (item.attachments.any { it.isFailed }) {
                R.string.attach_badge_failed
            } else {
                R.string.attach_badge
            }
            parts.add(context.getString(badgeRes, item.attachments.size))
        }
        holder.subtitle.text = parts.joinToString("  ·  ")

        holder.statusDot.backgroundTintList = ColorStateList.valueOf(
            ContextCompat.getColor(
                context,
                when {
                    item.failed -> R.color.accent_red
                    isReady -> R.color.accent_cyan
                    else -> R.color.accent_violet
                },
            )
        )

        // בהקלטה שנכשלה, שורת ההערה מציגה במקומה את סיבת הכישלון - זה המידע
        // היחיד שיש עליה, והוא מה שמאפשר להחליט אם לנסות שוב או למחוק.
        val noteText = if (item.failed) item.error?.let { "⚠️ $it" } else item.note?.let { "📝 $it" }
        if (noteText.isNullOrBlank()) {
            holder.note.visibility = View.GONE
        } else {
            holder.note.visibility = View.VISIBLE
            holder.note.text = noteText
        }

        holder.root.setOnClickListener {
            // הסיכום ולא התיקייה: מאז שהקבצים ב-Drive מסודרים לפי סוג
            // ("סיכום", "תמלול"...) ולא לפי פגישה, אין תיקייה שמכילה רק את
            // ההקלטה הזו - קישור התיקייה מוביל לספרייה כולה.
            (item.summaryUrl ?: item.folderUrl)?.let(onOpenLink)
        }

        holder.menuButton.setOnClickListener { onMenuClick(it, item) }
    }
}
