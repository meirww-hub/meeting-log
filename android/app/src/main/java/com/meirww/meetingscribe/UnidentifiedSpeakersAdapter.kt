package com.meirww.meetingscribe

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

class UnidentifiedSpeakersAdapter(
    private var items: List<SpeakerProfile> = emptyList(),
    private val onPlayClick: (SpeakerProfile) -> Unit,
    private val onSaveClick: (SpeakerProfile, String) -> Unit,
) : RecyclerView.Adapter<UnidentifiedSpeakersAdapter.ViewHolder>() {

    private var playingProfileId: String? = null

    fun submitList(newItems: List<SpeakerProfile>) {
        items = newItems
        notifyDataSetChanged()
    }

    /** מציין איזו שורה מנגנת כרגע (אייקון "השהה") - שאר השורות חוזרות ל"נגן". */
    fun setPlayingState(profileId: String?) {
        playingProfileId = profileId
        notifyDataSetChanged()
    }

    class ViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val playButton: ImageView = view.findViewById(R.id.playButton)
        val nameInput: EditText = view.findViewById(R.id.nameInput)
        val saveButton: TextView = view.findViewById(R.id.saveButton)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_unidentified_speaker, parent, false)
        return ViewHolder(view)
    }

    override fun getItemCount() = items.size

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = items[position]
        holder.playButton.setImageResource(
            if (playingProfileId == item.profileId) R.drawable.ic_pause else R.drawable.ic_play
        )
        holder.playButton.setOnClickListener { onPlayClick(item) }
        holder.nameInput.setText(item.name.orEmpty())
        holder.saveButton.setOnClickListener {
            val name = holder.nameInput.text?.toString()?.trim().orEmpty()
            if (name.isNotEmpty() && name != item.name) onSaveClick(item, name)
        }
    }
}
