package com.meirww.meetingscribe

/**
 * ההחלטה מה לייבא מתיקיית ההקלטות של cally - חלץ מ-[CallImportWorker] כדי
 * שאפשר יהיה לבדוק אותה בלי מכשיר: כאן נמצא הכלל שמונע ייבוא כפול של אותה
 * שיחה, וזה בדיוק הכלל שנשבר בפועל.
 */
object CallImportScan {

    /**
     * כמה שניות קובץ חייב להישאר ללא שינוי לפני שמייבאים אותו.
     *
     * cally סוגרת את קובץ ה-m4a כמה שניות אחרי ניתוק השיחה, וממשיכה לכתוב בזמן
     * הזה. ייבוא באמצע הכתיבה ייצר הקלטה חלקית, וסריקה מאוחרת יותר - שראתה
     * שהקובץ גדל - ייבאה את אותה שיחה **פעם שנייה**, שלמה. כך נוצרו במערכת שתי
     * הקלטות של אותה שיחה, שנבדלות בשבריר שנייה.
     */
    const val QUIET_SECONDS = 15L

    /** מפריד ישן בערך ה-watermark שנשמר בגרסאות קודמות ("מפתח::גודל"). */
    private const val SIZE_SEPARATOR = "::"

    /** שם קובץ + זמן השינוי האחרון שלו (שניות epoch) בתיקיית cally. */
    data class CallyFile(val name: String, val modifiedEpochSeconds: Long)

    /** תמונת מצב של התיקייה: שעון המכשיר בזמן הקריאה + הקבצים שנמצאו. */
    data class CallyListing(val nowEpochSeconds: Long, val files: List<CallyFile>)

    /** שיחה אחת: מפתח השיחה והקבצים שלה (uplink/downlink). */
    data class CallGroup(val key: String, val files: List<CallyFile>)

    /**
     * [ready] - שיחות חדשות שקבציהן כבר לא משתנים, מוכנות לייבוא.
     * [stillWriting] - שיחות חדשות שעדיין נכתבות; יש לסרוק שוב בהמשך במקום
     * לייבא אותן עכשיו חלקית.
     */
    data class Plan(val ready: List<CallGroup>, val stillWriting: List<CallGroup>)

    /**
     * מפרש את פלט הפקודה שרצה בתיקיית cally: שורת `date +%s` ואחריה שורת
     * `stat -c '%Y %n'` לכל קובץ.
     *
     * `stat` ולא `ls -la`: זה האחרון מדווח דקות בלבד, ובלי שניות אי אפשר לדעת
     * אם קובץ נכתב ממש עכשיו.
     */
    fun parseListing(output: String): CallyListing? {
        val lines = output.lines().map { it.trim() }.filter { it.isNotEmpty() }
        val now = lines.firstOrNull()?.toLongOrNull() ?: return null
        return CallyListing(now, lines.drop(1).mapNotNull { parseStatLine(it) })
    }

    /** שורת `stat -c '%Y %n'`: "<זמן שינוי> <נתיב מלא>", לקובצי m4a/wav בלבד. */
    private fun parseStatLine(line: String): CallyFile? {
        val modified = line.substringBefore(' ').toLongOrNull() ?: return null
        val name = line.substringAfter(' ', "").substringAfterLast('/')
        if (!(name.endsWith(".m4a", true) || name.endsWith(".wav", true))) return null
        return CallyFile(name, modified)
    }

    /**
     * מחלק את השיחות שבתיקייה לשלוש: כאלה שכבר יובאו (נשמטות), כאלה שמוכנות
     * לייבוא, וכאלה שעדיין נכתבות.
     */
    fun plan(listing: CallyListing, imported: Set<String>): Plan {
        val (ready, stillWriting) = listing.files
            // "<חותמת זמן>__<מזהה שיחה>__uplink.m4a" -> מפתח השיחה הוא הקידומת
            // המשותפת עד ה-"__" האחרון.
            .groupBy { it.name.substringBeforeLast("__") }
            .filterKeys { it !in imported }
            .map { (key, files) -> CallGroup(key, files) }
            .sortedBy { it.key }
            .partition { group ->
                group.files.all {
                    listing.nowEpochSeconds - it.modifiedEpochSeconds >= QUIET_SECONDS
                }
            }
        return Plan(ready, stillWriting)
    }

    /**
     * מפתחות השיחות שכבר יובאו, מתוך ערכי ה-watermark השמורים. ערכים שנשמרו
     * בגרסאות קודמות נראים "מפתח::גודל" - הגודל כבר לא משמש להחלטה ונחתך כאן.
     */
    fun importedKeys(entries: Set<String>): Set<String> =
        entries.map { it.substringBeforeLast(SIZE_SEPARATOR) }.toSet()

    /**
     * ערכי ה-watermark לשמירה אחרי שנוספה [callKey], חתוכים ל-[limit].
     *
     * החיתוך שומר את החדשות: המפתח פותח בחותמת זמן, ולכן מיון לקסיקלי הוא מיון
     * כרונולוגי. חיתוך שרירותי (כפי שהיה - `takeLast` על קבוצה לא מסודרת) היה
     * מוחק גם מפתח של שיחה שהקובץ שלה עדיין קיים אצל cally, והיא הייתה מיובאת
     * שוב - כפילות, חודש אחרי.
     */
    fun watermarkWith(existing: Set<String>, callKey: String, limit: Int): Set<String> =
        (importedKeys(existing) + callKey).sorted().takeLast(limit).toSet()
}
