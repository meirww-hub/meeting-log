package com.meirww.meetingscribe

import android.content.res.ColorStateList
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.checkbox.MaterialCheckBox

/** רשימת ההקלטות בבורר של מסך הצ'אט, עם סימון מרובה. */
class RecordingPickerAdapter(
    private val isSelected: (RecordingItem) -> Boolean,
    private val onToggle: (RecordingItem) -> Unit,
) : RecyclerView.Adapter<RecordingPickerAdapter.ViewHolder>() {

    private var items: List<RecordingItem> = emptyList()

    fun submitList(newItems: List<RecordingItem>) {
        items = newItems
        notifyDataSetChanged()
    }

    class ViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val root: View = view.findViewById(R.id.pickRoot)
        val checkbox: MaterialCheckBox = view.findViewById(R.id.pickCheckbox)
        val statusDot: View = view.findViewById(R.id.pickStatusDot)
        val title: TextView = view.findViewById(R.id.pickTitle)
        val duration: TextView = view.findViewById(R.id.pickDuration)
        val subtitle: TextView = view.findViewById(R.id.pickSubtitle)
        val note: TextView = view.findViewById(R.id.pickNote)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_recording_pick, parent, false)
        return ViewHolder(view)
    }

    override fun getItemCount() = items.size

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = items[position]
        val context = holder.root.context
        val isReady = item.transcriptUrl != null && item.summaryUrl != null

        holder.title.text = item.title

        if (item.durationSeconds > 0) {
            holder.duration.visibility = View.VISIBLE
            holder.duration.text = formatDuration(item.durationSeconds)
        } else {
            holder.duration.visibility = View.GONE
        }

        val parts = mutableListOf(item.date.toDisplayDate())
        if (item.speakers.isNotEmpty()) parts.add(item.speakers.joinToString(", "))
        parts.add(
            context.getString(
                if (isReady) R.string.history_status_ready else R.string.history_status_processing
            )
        )
        holder.subtitle.text = parts.joinToString("  ·  ")

        holder.statusDot.backgroundTintList = ColorStateList.valueOf(
            ContextCompat.getColor(context, if (isReady) R.color.accent_cyan else R.color.accent_violet)
        )

        if (item.note.isNullOrBlank()) {
            holder.note.visibility = View.GONE
        } else {
            holder.note.visibility = View.VISIBLE
            holder.note.text = "📝 ${item.note}"
        }

        val selected = isSelected(item)
        holder.checkbox.isChecked = selected
        holder.root.isActivated = selected

        holder.root.setOnClickListener {
            onToggle(item)
            val nowSelected = isSelected(item)
            holder.checkbox.isChecked = nowSelected
            holder.root.isActivated = nowSelected
        }
    }
}
