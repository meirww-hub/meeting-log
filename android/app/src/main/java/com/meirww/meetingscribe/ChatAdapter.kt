package com.meirww.meetingscribe

import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

class ChatAdapter(private var items: List<ChatMessage> = emptyList()) :
    RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    private companion object {
        const val TYPE_USER = 0
        const val TYPE_ASSISTANT = 1
    }

    fun submitList(newItems: List<ChatMessage>) {
        items = newItems
        notifyDataSetChanged()
    }

    override fun getItemViewType(position: Int) =
        if (items[position].isUser) TYPE_USER else TYPE_ASSISTANT

    override fun getItemCount() = items.size

    class UserViewHolder(view: android.view.View) : RecyclerView.ViewHolder(view) {
        val text: TextView = view.findViewById(R.id.messageText)
    }

    class AssistantViewHolder(view: android.view.View) : RecyclerView.ViewHolder(view) {
        val text: TextView = view.findViewById(R.id.messageText)
        val citationsContainer: LinearLayout = view.findViewById(R.id.citationsContainer)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return if (viewType == TYPE_USER) {
            UserViewHolder(inflater.inflate(R.layout.item_chat_user, parent, false))
        } else {
            AssistantViewHolder(inflater.inflate(R.layout.item_chat_assistant, parent, false))
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        val item = items[position]
        when (holder) {
            is UserViewHolder -> holder.text.text = item.text
            is AssistantViewHolder -> {
                holder.text.text = item.text
                holder.citationsContainer.removeAllViews()
                item.citations.forEach { citation ->
                    val row = TextView(holder.itemView.context).apply {
                        text = "📍 ${citation.recordingTitle} · ${citation.timestamp}"
                        setTextColor(
                            androidx.core.content.ContextCompat.getColor(
                                context, R.color.accent_cyan
                            )
                        )
                        textSize = 12f
                        setPadding(0, 6, 0, 0)
                    }
                    holder.citationsContainer.addView(row)
                }
            }
        }
    }
}
