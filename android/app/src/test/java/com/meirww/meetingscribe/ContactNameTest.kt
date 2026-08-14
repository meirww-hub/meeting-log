package com.meirww.meetingscribe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * כל המקרים כאן לקוחים מרשימת אנשי הקשר האמיתית של המשתמש במכשיר, לא
 * מומצאים - כולל המקרה שהפיל את זה בפועל ("דוד. פרוייקטים. קינג חשמלאי").
 */
class ContactNameTest {

    @Test
    fun `takes the person name before the role and company`() {
        // המקרה שנצפה בפועל: הסיכום יצא "דוד. פרוייקטים. קינג חשמלאי אישר..."
        assertEquals("דוד", ContactName.toSpeakerLabel("דוד. פרוייקטים. קינג חשמלאי"))
        assertEquals("Alex Aro", ContactName.toSpeakerLabel("Alex Aro. מתווך. אגמים"))
        assertEquals("Ehud", ContactName.toSpeakerLabel("Ehud. חברת הובלות. מנכ\"ל. מרכז"))
        assertEquals("Dmitry", ContactName.toSpeakerLabel("Dmitry. מסגר עבודות ברזל"))
    }

    @Test
    fun `keeps a plain name untouched`() {
        assertEquals("קובי", ContactName.toSpeakerLabel("קובי"))
        assertEquals("ערן זולוטוב", ContactName.toSpeakerLabel("ערן זולוטוב"))
        assertEquals("Adi Sani", ContactName.toSpeakerLabel("Adi Sani"))
    }

    @Test
    fun `a name without the separator convention is never truncated`() {
        // אין מפריד - אין ניחוש איפה נגמר השם. עדיף ארוך מאשר חתוך באמצע.
        val long = "מיכאל ברלין יועץ ומשווק נדל\"ן RE/MAX FOR ALL באשקלון"
        assertEquals(long, ContactName.toSpeakerLabel(long))
        assertEquals("ABB ערן זולוטוב", ContactName.toSpeakerLabel("ABB ערן זולוטוב"))
    }

    @Test
    fun `leading separator does not swallow the name`() {
        // רשומות שמתחילות בנקודה - הסגמנט הראשון ריק, השם הוא הבא אחריו.
        assertEquals("אופק Sahar Calizo My Vibe", ContactName.toSpeakerLabel(". אופק Sahar Calizo My Vibe"))
        assertEquals("שליחויותMaram Alkam", ContactName.toSpeakerLabel(". שליחויותMaram Alkam"))
    }

    @Test
    fun `numeric leading segment is skipped in favour of the real name`() {
        assertEquals("משה אינסטלטור", ContactName.toSpeakerLabel("050-123-4567. משה אינסטלטור"))
    }

    @Test
    fun `initials are not mistaken for a separator`() {
        // נקודה בלי רווח אחריה אינה מפריד - אחרת "א.ד גורדון 1" נחתך ל-"א".
        assertEquals("א.ד גורדון 1", ContactName.toSpeakerLabel("א.ד גורדון 1"))
        assertEquals("י.ח שרברבות", ContactName.toSpeakerLabel("י.ח שרברבות"))
    }

    @Test
    fun `stray separator punctuation is trimmed off the edges`() {
        assertEquals("shlomi assulin", ContactName.toSpeakerLabel("shlomi assulin.. נהג מונית"))
        assertEquals("יוסי", ContactName.toSpeakerLabel("יוסי."))
    }

    @Test
    fun `contact whose whole name is a phone number yields no label`() {
        // אין כאן שום מידע מעבר למה ש"הצד השני" כבר אומר.
        assertNull(ContactName.toSpeakerLabel("048-221-501"))
        assertNull(ContactName.toSpeakerLabel("+972548826909"))
        assertNull(ContactName.toSpeakerLabel("1-800-700-111"))
    }

    @Test
    fun `blank and null inputs yield no label`() {
        assertNull(ContactName.toSpeakerLabel(null))
        assertNull(ContactName.toSpeakerLabel(""))
        assertNull(ContactName.toSpeakerLabel("   "))
    }

    @Test
    fun `emoji and repeated whitespace are stripped`() {
        assertEquals("Alon מדביר Haral", ContactName.toSpeakerLabel("Alon מדביר  Haral"))
        assertEquals("יוסי", ContactName.toSpeakerLabel("יוסי 😎"))
        assertEquals("רונית", ContactName.toSpeakerLabel("😎 רונית. שיפוצים"))
    }

    @Test
    fun `the label never keeps a separator that would break a summary sentence`() {
        // זו כל הנקודה: התווית נכתבת בתוך משפט בסיכום.
        val samples = listOf(
            "דוד. פרוייקטים. קינג חשמלאי",
            "Eran. מערכות חשמל Weisman. Schamalz",
            "770 נדלן. טייסים",
        )
        for (s in samples) {
            val label = ContactName.toSpeakerLabel(s)!!
            assertEquals("נשארה נקודה בתווית '$label'", false, label.contains('.'))
        }
    }
}
