package com.meirww.meetingscribe.wear

import android.app.Activity
import android.content.SharedPreferences
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Chronometer
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView

/**
 * מסך השעון: כפתור אחד גדול - התחל / עצור - וכותרת שמראה מה *הטלפון* אומר
 * שקורה. השעון לא מקליט כלום בעצמו.
 */
class MainActivity : Activity() {

    private val handler = Handler(Looper.getMainLooper())

    private lateinit var timer: Chronometer
    private lateinit var button: ImageButton
    private lateinit var headline: TextView
    private lateinit var detail: TextView
    private var lastUi: UiState? = null

    private val prefsListener = SharedPreferences.OnSharedPreferenceChangeListener { _, _ ->
        runOnUiThread { render() }
    }

    /** ההודעה "הטלפון לא ענה" צריכה להופיע גם אם אף עדכון לא הגיע - לכן בדיקה תקופתית. */
    private val ticker = object : Runnable {
        override fun run() {
            render()
            handler.postDelayed(this, 1_000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(buildLayout())
    }

    override fun onResume() {
        super.onResume()
        StatusStore.addChangeListener(this, prefsListener)
        handler.post(ticker)

        // האריח פותח את המסך עם "start" כדי להתחיל בנגיעה אחת. רק התחלה: עצירה
        // תמיד דרך הכפתור במסך, כדי שנגיעה מקרית באריח לא תסיים פגישה.
        val ui = StatusPresenter.present(StatusStore.snapshot(this), SystemClock.elapsedRealtime())
        if (intent?.getStringExtra(EXTRA_AUTO) == AUTO_START && ui.mode == Mode.START) {
            intent.removeExtra(EXTRA_AUTO)
            PhoneLink.requestStart(this)
        } else {
            PhoneLink.requestStatus(this)
        }
    }

    override fun onPause() {
        handler.removeCallbacks(ticker)
        StatusStore.removeChangeListener(this, prefsListener)
        super.onPause()
    }

    private fun render() {
        val ui = StatusPresenter.present(StatusStore.snapshot(this), SystemClock.elapsedRealtime())
        if (ui == lastUi) return
        lastUi = ui

        headline.setText(Texts.headline(ui.headline))
        val pending = Texts.pending(this, ui.pendingCount)
        detail.text = pending
        detail.visibility = if (pending.isEmpty()) View.GONE else View.VISIBLE

        val base = ui.timerBase
        if (base != null) {
            timer.base = base
            timer.visibility = View.VISIBLE
            timer.start()
        } else {
            timer.stop()
            timer.visibility = View.INVISIBLE
        }

        val (color, icon) = when (ui.mode) {
            Mode.START -> COLOR_START to R.drawable.ic_mic
            Mode.STOP -> COLOR_STOP to R.drawable.ic_stop
            Mode.BUSY -> COLOR_BUSY to R.drawable.ic_mic
        }
        (button.background as GradientDrawable).setColor(color)
        button.setImageResource(icon)
        button.isEnabled = ui.mode != Mode.BUSY
        button.alpha = if (ui.mode == Mode.BUSY) 0.6f else 1f
    }

    private fun onButton() {
        when (lastUi?.mode) {
            Mode.START -> PhoneLink.requestStart(this)
            Mode.STOP -> PhoneLink.requestStop(this)
            else -> Unit
        }
    }

    private fun buildLayout(): View {
        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }

        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            // השעון עגול: שוליים רחבים כדי שדבר לא ייחתך בקצה.
            setPadding(dp(30), dp(18), dp(30), dp(34))
        }

        timer = Chronometer(this).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 22f)
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            visibility = View.INVISIBLE
        }
        column.addView(timer, wrap())

        button = ImageButton(this).apply {
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(COLOR_START)
            }
            scaleType = android.widget.ImageView.ScaleType.CENTER_INSIDE
            setPadding(dp(22), dp(22), dp(22), dp(22))
            contentDescription = getString(R.string.button_description)
            setOnClickListener { onButton() }
        }
        column.addView(button, LinearLayout.LayoutParams(dp(88), dp(88)).apply {
            topMargin = dp(6)
            bottomMargin = dp(8)
        })

        headline = TextView(this).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            gravity = Gravity.CENTER
            maxLines = 3
        }
        column.addView(headline, wrap())

        detail = TextView(this).apply {
            setTextColor(Color.LTGRAY)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            gravity = Gravity.CENTER
            maxLines = 2
        }
        column.addView(detail, wrap())

        root.addView(
            column,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
        )
        return root
    }

    private fun wrap() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.WRAP_CONTENT,
        ViewGroup.LayoutParams.WRAP_CONTENT,
    )

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).toInt()

    companion object {
        const val EXTRA_AUTO = "auto"
        const val AUTO_START = "start"

        private val COLOR_START = Color.parseColor("#E11D48")
        private val COLOR_STOP = Color.parseColor("#374151")
        private val COLOR_BUSY = Color.parseColor("#6B7280")
    }
}
