package com.meirww.meetingscribe

import com.meirww.meetingscribe.watch.FailReason

/**
 * ההחלטה מה לעשות כשהשעון מבקש "התחל הקלטה". לוגיקה טהורה, בלי Android -
 * כך אפשר לבדוק אותה ב-JVM, וכל הכללים של "מתי מותר לפתוח מיקרופון" במקום
 * אחד.
 *
 * למה צריך להעלות מסך בכלל: מאנדרואיד 14, אפליקציה שרצה ברקע לא יכולה ליצור
 * שירות foreground מסוג מיקרופון - המערכת זורקת SecurityException, ושום
 * פטור (חיבור לשעון, FCM, ביטול חיסכון סוללה) לא עוקף את זה. הפטור היחיד שזמין
 * לנו הוא לפתוח Activity: היא נחשבת "אפליקציה גלויה", ומתוכה מותר להתחיל את
 * ההקלטה. ופתיחת Activity מהרקע מותרת רק עם הרשאת "הצגה מעל אפליקציות אחרות".
 */
object WatchStartPolicy {

    sealed interface Decision {
        /** כבר מקליט - לא עושים כלום, רק משדרים לשעון את המצב. */
        data object AlreadyRecording : Decision

        /** אסור/אי אפשר להתחיל; השעון יציג את הסיבה. */
        data class Refuse(val reason: FailReason) : Decision

        /** פותחים את מסך ההתחלה, והוא יתחיל את ההקלטה. */
        data object LaunchStarter : Decision
    }

    fun decide(
        isRecording: Boolean,
        hasMicPermission: Boolean,
        canDrawOverlays: Boolean,
    ): Decision = when {
        isRecording -> Decision.AlreadyRecording
        !hasMicPermission -> Decision.Refuse(FailReason.NO_MIC_PERMISSION)
        !canDrawOverlays -> Decision.Refuse(FailReason.NO_OVERLAY_PERMISSION)
        else -> Decision.LaunchStarter
    }
}
