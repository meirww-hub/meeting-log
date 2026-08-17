package com.meirww.meetingscribe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * המקרה שהיה בפועל ב-2026-08-16: עדכון מערכת אוטומטי הפיל את Shizuku,
 * וייבוא שיחות נשאר מושבת יומיים בלי שום סימן - כי הסריקה מנסה שוב כל כמה
 * שעות ולא הייתה שום דרך לדעת שהיא נכשלת. הכללים כאן קובעים שההודעה יוצאת
 * פעם אחת לכל הפסקה, לא בכל ניסיון חוזר.
 */
class ShizukuOutageTrackerTest {

    @Test
    fun `first failure notifies`() {
        assertTrue(ShizukuOutageTracker.shouldNotify(available = false, alreadyNotified = false))
    }

    @Test
    fun `repeated failure does not notify again`() {
        assertFalse(ShizukuOutageTracker.shouldNotify(available = false, alreadyNotified = true))
    }

    @Test
    fun `available never notifies, regardless of past state`() {
        assertFalse(ShizukuOutageTracker.shouldNotify(available = true, alreadyNotified = false))
        assertFalse(ShizukuOutageTracker.shouldNotify(available = true, alreadyNotified = true))
    }

    @Test
    fun `recovery clears the flag so the next outage notifies again`() {
        assertEquals(false, ShizukuOutageTracker.nextNotifiedFlag(available = true))
    }

    @Test
    fun `an ongoing outage keeps the flag set`() {
        assertEquals(true, ShizukuOutageTracker.nextNotifiedFlag(available = false))
    }

    @Test
    fun `a full outage-then-recovery cycle notifies exactly once`() {
        var notified = false
        val availabilitySequence = listOf(false, false, false, true, false)
        val notifications = availabilitySequence.map { available ->
            val fired = ShizukuOutageTracker.shouldNotify(available, notified)
            notified = ShizukuOutageTracker.nextNotifiedFlag(available)
            fired
        }
        // הודעה רק בכשלון הראשון (0) ובכשלון הראשון אחרי ההתאוששות (4).
        assertEquals(listOf(true, false, false, false, true), notifications)
    }
}
