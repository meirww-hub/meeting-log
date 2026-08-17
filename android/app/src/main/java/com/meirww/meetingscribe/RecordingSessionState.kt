package com.meirww.meetingscribe

import android.content.Context
import java.io.File

/**
 * המצב שחייב לשרוד הריגה של התהליך: האם יש הקלטה שהמשתמש ביקש ועוד לא עצר,
 * ולאיזו תיקיית session היא כותבת.
 *
 * למה על הדיסק ולא בזיכרון: בדיוק כשמגיעה שיחה, מערכת ההפעלה נותנת עדיפות
 * לאפליקציית הטלפון ויכולה להרוג את התהליך שלנו. הזיכרון נמחק יחד איתו, ולכן
 * התהליך שחוזר (START_STICKY) לא ידע שהוא באמצע פגישה - הוא היה פותח session
 * חדש, בעוד הישן נשלח לעיבוד בפני עצמו. התוצאה: פגישה אחת מתפצלת לשתי הקלטות,
 * שני תמלולים ושני סיכומים. כאן נשמר המידע שמאפשר לו להמשיך *לאותה* תיקייה.
 *
 * [KEY_ALIVE_AT] הוא דופק: כל עוד השירות חי הוא מעדכן אותו. הוא מה שמונע את
 * הצד השני של הסכנה - הקלטה שהתהליך שלה מת ולא חזר הייתה נשארת "חיה" לנצח,
 * וסריקת ההשלמה הייתה מדלגת עליה לעד ולא שולחת אותה לעיבוד.
 */
object RecordingSessionState {

    private const val PREFS_NAME = "recording_state"
    private const val KEY_USER_STARTED = "user_started"
    private const val KEY_SESSION_DIR = "session_dir"
    private const val KEY_ALIVE_AT = "alive_at"
    private const val KEY_STARTED_AT = "started_at"

    /**
     * כמה זמן הקלטה נחשבת חיה בלי דופק. חידוש אחרי הריגת תהליך קורה תוך
     * שניות, ולכן שלוש דקות הן מרווח נדיב; מעבר לזה מניחים שהתהליך לא חוזר,
     * והתיקייה משוחררת לסריקה כדי שמה שהוקלט עד כה יגיע לעיבוד.
     */
    const val ALIVE_WINDOW_MS = 3 * 60_000L

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /**
     * commit ולא apply: מכאן והלאה התהליך יכול להיהרג בכל רגע, והכתיבה חייבת
     * כבר להיות על הדיסק.
     */
    fun markStarted(context: Context, sessionDir: File) {
        prefs(context).edit()
            .putBoolean(KEY_USER_STARTED, true)
            .putString(KEY_SESSION_DIR, sessionDir.absolutePath)
            .putLong(KEY_ALIVE_AT, System.currentTimeMillis())
            .commit()
    }

    /** apply ולא commit: אבדן דופק בודד לא משנה מול חלון של שלוש דקות. */
    fun heartbeat(context: Context) {
        if (!userStarted(context)) return
        prefs(context).edit().putLong(KEY_ALIVE_AT, System.currentTimeMillis()).apply()
    }

    fun clear(context: Context) {
        prefs(context).edit()
            .remove(KEY_USER_STARTED)
            .remove(KEY_SESSION_DIR)
            .remove(KEY_ALIVE_AT)
            .remove(KEY_STARTED_AT)
            .commit()
    }

    /**
     * זמן תחילת הפגישה בפועל - נכתב פעם אחת בלבד ולא זז בחידוש אחרי הריגת
     * תהליך, כדי שטיימר במסך ובהתראה ימשיך לרוץ מהזמן האמיתי ולא יתאפס.
     */
    fun recordStartTimeIfAbsent(context: Context) {
        val prefs = prefs(context)
        if (prefs.contains(KEY_STARTED_AT)) return
        prefs.edit().putLong(KEY_STARTED_AT, System.currentTimeMillis()).commit()
    }

    /** null כשאין הקלטה פעילה שכבר נרשם לה זמן התחלה. */
    fun startedAtMillis(context: Context): Long? =
        prefs(context).getLong(KEY_STARTED_AT, -1L).takeIf { it > 0 }

    fun userStarted(context: Context): Boolean =
        prefs(context).getBoolean(KEY_USER_STARTED, false)

    /**
     * תיקיית ההקלטה החיה: זו שסריקת ההשלמה חייבת לדלג עליה, וזו שיש להמשיך
     * לכתוב אליה אחרי שהמערכת הרגה את השירות. null כשאין הקלטה כזו, או
     * כשהדופק שלה נשתק - כלומר התהליך לא חזר והיא כבר לא באמת חיה.
     */
    fun liveSession(context: Context): File? {
        if (!userStarted(context)) return null
        val prefs = prefs(context)
        val path = prefs.getString(KEY_SESSION_DIR, null) ?: return null
        val age = System.currentTimeMillis() - prefs.getLong(KEY_ALIVE_AT, 0L)
        if (age !in 0..ALIVE_WINDOW_MS) return null
        return File(path).takeIf { it.isDirectory }
    }

    /**
     * משחרר הקלטה שהדופק שלה נשתק, כדי שהסריקה תשלח לעיבוד את מה שכן הוקלט.
     * מחזיר true אם שוחררה כזו.
     */
    fun expireIfStale(context: Context): Boolean {
        if (!userStarted(context)) return false
        if (liveSession(context) != null) return false
        clear(context)
        return true
    }
}
