package com.meirww.meetingscribe

import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.TextPaint
import android.text.method.LinkMovementMethod
import android.text.style.ClickableSpan
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView

/**
 * [onPlayRequest] - המשתמש ביקש לשמוע רגע מסוים: מזהה ההקלטה (null כשאי
 * אפשר להסיק אותה מהתשובה עצמה, והמסך יכריע) והזמן בשניות.
 */
class ChatAdapter(
    private val onPlayRequest: (recordingId: String?, seconds: Double) -> Unit,
) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    private companion object {
        const val TYPE_USER = 0
        const val TYPE_ASSISTANT = 1
    }

    private var items: List<ChatMessage> = emptyList()

    fun submitList(newItems: List<ChatMessage>) {
        items = newItems
        notifyDataSetChanged()
    }

    override fun getItemViewType(position: Int) =
        if (items[position].isUser) TYPE_USER else TYPE_ASSISTANT

    override fun getItemCount() = items.size

    class UserViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val text: TextView = view.findViewById(R.id.messageText)
    }

    class AssistantViewHolder(view: View) : RecyclerView.ViewHolder(view) {
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
            is AssistantViewHolder -> bindAssistant(holder, item)
        }
    }

    private fun bindAssistant(holder: AssistantViewHolder, item: ChatMessage) {
        holder.text.text = playableTimestamps(holder.text, item)
        holder.text.movementMethod = LinkMovementMethod.getInstance()

        holder.citationsContainer.removeAllViews()
        item.citations.forEach { citation ->
            holder.citationsContainer.addView(citationRow(holder.itemView, citation))
        }
    }

    /**
     * הופך כל זמן שמופיע בגוף התשובה ("בין 2:21 ל-5:04") לקישור שמנגן ממנו.
     *
     * זה המסלול הטבעי: התשובה עצמה היא שאומרת מתי הדבר קרה, ולכן הזמן שבתוכה
     * הוא מה שהעין נתקלת בו - לא רק שורת המקור שמתחתיה.
     */
    private fun playableTimestamps(view: TextView, item: ChatMessage): CharSequence {
        val linkColor = ContextCompat.getColor(view.context, R.color.accent_cyan)
        val spannable = SpannableStringBuilder(item.text)

        TIMESTAMP_PATTERN.findAll(item.text).forEach { match ->
            val seconds = parseTimestampSeconds(match.value) ?: return@forEach
            val span = object : ClickableSpan() {
                override fun onClick(widget: View) {
                    onPlayRequest(item.recordingIdForTime(seconds), seconds)
                }

                override fun updateDrawState(ds: TextPaint) {
                    ds.color = linkColor
                    ds.isUnderlineText = true
                }
            }
            spannable.setSpan(
                span,
                match.range.first,
                match.range.last + 1,
                Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
        }
        return spannable
    }

    /** שורת מקור אחת; כשיש לה מקום בציר הזמן היא גם כפתור ניגון. */
    private fun citationRow(parent: View, citation: Citation): TextView {
        val recordingId = citation.recordingId
        val startSeconds = citation.startSeconds
        val density = parent.resources.displayMetrics.density

        return TextView(parent.context).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = (6 * density).toInt() }
            setTextColor(ContextCompat.getColor(context, R.color.accent_cyan))
            textSize = 12f

            if (recordingId != null && startSeconds != null) {
                text = context.getString(
                    R.string.chat_citation_playable, citation.timestamp, citation.recordingTitle
                )
                setBackgroundResource(R.drawable.bg_citation_playable)
                val paddingX = (10 * density).toInt()
                val paddingY = (6 * density).toInt()
                setPadding(paddingX, paddingY, paddingX, paddingY)
                isClickable = true
                isFocusable = true
                setOnClickListener { onPlayRequest(recordingId, startSeconds) }
            } else {
                text = context.getString(
                    R.string.chat_citation_plain, citation.recordingTitle, citation.timestamp
                )
            }
        }
    }
}
