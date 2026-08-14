package com.meirww.meetingscribe

import android.content.Context
import android.util.AttributeSet
import androidx.recyclerview.widget.RecyclerView

/**
 * רשימה שגדלה לפי התוכן אבל לא עוברת חצי מגובה המסך - כך הבורר בגיליון
 * התחתון נשאר קומפקטי כשיש מעט הקלטות, ונגלל כשיש הרבה, בלי שכפתור האישור
 * ייצא מהמסך.
 */
class MaxHeightRecyclerView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : RecyclerView(context, attrs, defStyleAttr) {

    override fun onMeasure(widthSpec: Int, heightSpec: Int) {
        val cap = (resources.displayMetrics.heightPixels * 0.5f).toInt()
        super.onMeasure(widthSpec, MeasureSpec.makeMeasureSpec(cap, MeasureSpec.AT_MOST))
    }
}
