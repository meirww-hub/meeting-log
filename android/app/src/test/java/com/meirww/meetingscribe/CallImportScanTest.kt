package com.meirww.meetingscribe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * הכלל שנשבר בפועל: אותה שיחה נכנסה למערכת פעמיים. כל מקרה כאן לקוח מהתיקייה
 * האמיתית של cally במכשיר ומהכפילויות שנמצאו בשרת.
 */
class CallImportScanTest {

    private val now = 1_786_546_800L

    private fun listing(vararg files: Pair<String, Long>) =
        CallImportScan.CallyListing(
            nowEpochSeconds = now,
            files = files.map { (name, modified) ->
                CallImportScan.CallyFile(name, modified)
            },
        )

    /** שיחה שהסתיימה ונסגרה - שני הערוצים לא נגעו כבר דקה. */
    private fun finishedCall(key: String, secondsAgo: Long = 60) = arrayOf(
        "${key}__uplink.m4a" to now - secondsAgo,
        "${key}__downlink.m4a" to now - secondsAgo,
    )

    @Test
    fun `a finished call is ready to import`() {
        val plan = CallImportScan.plan(listing(*finishedCall("2026-08-12_09-17-19__9WFSD")), emptySet())

        assertEquals(listOf("2026-08-12_09-17-19__9WFSD"), plan.ready.map { it.key })
        assertEquals(2, plan.ready.single().files.size)
        assertTrue(plan.stillWriting.isEmpty())
    }

    @Test
    fun `a call still being written is not imported yet`() {
        // cally סוגרת את הקובץ כמה שניות אחרי הניתוק. ייבוא עכשיו היה מייצר
        // הקלטה קטועה - ואת אותה שיחה שוב, שלמה, בסריקה הבאה.
        val plan = CallImportScan.plan(listing(*finishedCall("2026-08-12_15-12-33__Y6YA1", secondsAgo = 3)), emptySet())

        assertTrue(plan.ready.isEmpty())
        assertEquals(listOf("2026-08-12_15-12-33__Y6YA1"), plan.stillWriting.map { it.key })
    }

    @Test
    fun `a call whose second channel is still being written waits for both`() {
        // המקרה שיצר כפילות עם אורך שונה בשבריר שנייה: ערוץ אחד כבר נסגר
        // והשני עדיין נכתב.
        val plan = CallImportScan.plan(
            listing(
                "2026-08-12_07-33-00__ABC12__uplink.m4a" to now - 60,
                "2026-08-12_07-33-00__ABC12__downlink.m4a" to now - 2,
            ),
            emptySet(),
        )

        assertTrue(plan.ready.isEmpty())
        assertEquals(1, plan.stillWriting.size)
    }

    @Test
    fun `an already imported call is never imported again`() {
        val key = "2026-08-12_09-42-25__BAEF5"
        val plan = CallImportScan.plan(listing(*finishedCall(key)), setOf(key))

        assertTrue(plan.ready.isEmpty())
        assertTrue(plan.stillWriting.isEmpty())
    }

    @Test
    fun `a call imported before the size watermark was dropped stays imported`() {
        // ערכים שנשמרו בגרסאות קודמות נראים "מפתח::גודל". בלי חיתוך הסיומת
        // כל שיחה ישנה שהקובץ שלה עוד קיים אצל cally הייתה מיובאת מחדש.
        val key = "2026-08-12_10-22-49__DMD7S"
        val imported = CallImportScan.importedKeys(setOf("$key::4527392"))

        assertEquals(setOf(key), imported)
        assertTrue(CallImportScan.plan(listing(*finishedCall(key)), imported).ready.isEmpty())
    }

    @Test
    fun `several finished calls are imported in chronological order`() {
        val plan = CallImportScan.plan(
            listing(
                *finishedCall("2026-08-12_17-57-23__7MQH9"),
                *finishedCall("2026-08-12_10-22-49__DMD7S"),
                *finishedCall("2026-08-12_13-50-46__SH5SG"),
            ),
            emptySet(),
        )

        assertEquals(
            listOf(
                "2026-08-12_10-22-49__DMD7S",
                "2026-08-12_13-50-46__SH5SG",
                "2026-08-12_17-57-23__7MQH9",
            ),
            plan.ready.map { it.key },
        )
    }

    @Test
    fun `a finished call is imported even while another call is still recording`() {
        val plan = CallImportScan.plan(
            listing(
                *finishedCall("2026-08-12_10-22-49__DMD7S"),
                *finishedCall("2026-08-12_17-57-23__7MQH9", secondsAgo = 1),
            ),
            emptySet(),
        )

        assertEquals(listOf("2026-08-12_10-22-49__DMD7S"), plan.ready.map { it.key })
        assertEquals(listOf("2026-08-12_17-57-23__7MQH9"), plan.stillWriting.map { it.key })
    }

    @Test
    fun `listing output from the device is parsed into files and clock`() {
        val output = """
            1786546800
            1786546707 /storage/emulated/0/Android/data/dev.lyo.callrec/files/recordings/2026-08-12_17-57-23__7MQH9__uplink.m4a
            1786546707 /storage/emulated/0/Android/data/dev.lyo.callrec/files/recordings/2026-08-12_17-57-23__7MQH9__downlink.m4a
        """.trimIndent()

        val listing = CallImportScan.parseListing(output)!!

        assertEquals(now, listing.nowEpochSeconds)
        assertEquals(
            listOf(
                "2026-08-12_17-57-23__7MQH9__uplink.m4a",
                "2026-08-12_17-57-23__7MQH9__downlink.m4a",
            ),
            listing.files.map { it.name },
        )
    }

    @Test
    fun `non audio entries and an empty directory are ignored`() {
        val listing = CallImportScan.parseListing("1786546800\n1786546707 /some/dir/notes.txt")!!
        assertTrue(listing.files.isEmpty())

        // תיקייה ריקה: הגלוב לא התרחב, יש רק שורת שעון.
        assertTrue(CallImportScan.parseListing("1786546800")!!.files.isEmpty())
    }

    @Test
    fun `output without a clock line is rejected rather than guessed`() {
        // בלי שעון אי אפשר לדעת מה "נכתב עכשיו", ועדיף לא לייבא כלום.
        assertNull(CallImportScan.parseListing(""))
        assertNull(CallImportScan.parseListing("stat: cannot read"))
    }

    @Test
    fun `the watermark keeps the newest keys when it overflows`() {
        val existing = (1..5).map { "2026-08-0${it}_10-00-00__OLD$it" }.toSet()

        val updated = CallImportScan.watermarkWith(existing, "2026-08-12_10-00-00__NEW", limit = 3)

        assertEquals(
            setOf(
                "2026-08-05_10-00-00__OLD5",
                "2026-08-12_10-00-00__NEW",
                "2026-08-04_10-00-00__OLD4",
            ),
            updated,
        )
    }

    @Test
    fun `marking a call that is already marked changes nothing`() {
        val key = "2026-08-12_10-00-00__ONCE"
        val once = CallImportScan.watermarkWith(emptySet(), key, limit = 500)

        assertEquals(once, CallImportScan.watermarkWith(once, key, limit = 500))
    }
}
