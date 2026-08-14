package com.meirww.meetingscribe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * הקשה על זמן בתוך תשובת הצ'אט צריכה להשמיע את הרגע הנכון בהקלטה הנכונה.
 *
 * שתי הטעויות שאפשריות כאן: לקפוץ לדקה הלא נכונה (פענוח הזמן), ולקפוץ
 * להקלטה הלא נכונה כשנשאלו כמה הקלטות יחד. השנייה גרועה יותר - היא משמיעה
 * שיחה אחרת לגמרי בלי שהמשתמש יבין למה.
 */
class ChatTimestampTest {

    @Test
    fun `parses minutes and seconds`() {
        assertEquals(141.0, parseTimestampSeconds("2:21")!!, 0.0)
        assertEquals(304.0, parseTimestampSeconds("5:04")!!, 0.0)
    }

    @Test
    fun `parses hours`() {
        assertEquals(3723.0, parseTimestampSeconds("1:02:03")!!, 0.0)
    }

    @Test
    fun `takes the first time in a sentence`() {
        val answer = "הדבר התרחש בין הדקות 2:21 עד 5:04"
        assertEquals(141.0, parseTimestampSeconds(answer)!!, 0.0)
    }

    @Test
    fun `text without a time`() {
        assertNull(parseTimestampSeconds("אין על כך מידע בהקלטות"))
        assertNull(parseTimestampSeconds(""))
    }

    @Test
    fun `both times in the answer are found`() {
        val answer = "הדבר התרחש בין הדקות 2:21 עד 5:04"
        val found = TIMESTAMP_PATTERN.findAll(answer).map { it.value }.toList()
        assertEquals(listOf("2:21", "5:04"), found)
    }

    private fun citation(recordingId: String?, seconds: Double?) = Citation(
        recordingTitle = "פגישה",
        timestamp = "",
        quote = "",
        recordingId = recordingId,
        startSeconds = seconds,
    )

    private fun answer(vararg citations: Citation) =
        ChatMessage(isUser = false, text = "תשובה", citations = citations.toList())

    @Test
    fun `a time matching a citation plays that citation's recording`() {
        val message = answer(citation("rec-1", 141.0), citation("rec-2", 900.0))
        assertEquals("rec-1", message.recordingIdForTime(141.0))
        assertEquals("rec-2", message.recordingIdForTime(900.0))
    }

    @Test
    fun `a time near a citation still counts as the same moment`() {
        // המודל מנסח בגוף התשובה זמן מעוגל שנבדל בשנייה מזה שבציטוט.
        val message = answer(citation("rec-1", 141.0))
        assertEquals("rec-1", message.recordingIdForTime(142.0))
    }

    @Test
    fun `a time with no matching citation falls back to the only cited recording`() {
        val message = answer(citation("rec-1", 141.0), citation("rec-1", 900.0))
        assertEquals("rec-1", message.recordingIdForTime(600.0))
    }

    @Test
    fun `an ambiguous time between two recordings is left to the user`() {
        val message = answer(citation("rec-1", 141.0), citation("rec-2", 900.0))
        assertNull(message.recordingIdForTime(600.0))
    }

    @Test
    fun `an answer without citations leaves the choice to the user`() {
        assertNull(answer().recordingIdForTime(141.0))
    }

    @Test
    fun `citations that the server could not attribute are ignored`() {
        val message = answer(citation(null, 141.0), citation("rec-2", 900.0))
        assertEquals("rec-2", message.recordingIdForTime(141.0))
    }
}
