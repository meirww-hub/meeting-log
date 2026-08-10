package com.meirww.meetingscribe

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import androidx.core.content.ContextCompat
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.random.Random

/**
 * ויזואליזציה של אקולייזר שמגיבה בזמן אמת לעוצמת הקול בפועל (amplitude
 * מ-MediaRecorder), ולא אנימציה מזויפת. כשלא מקליטים - "נשימה" עדינה כדי
 * שהמסך לא יהיה סטטי לגמרי.
 */
class EqualizerView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private companion object {
        const val BAR_COUNT = 28
        const val MIN_LEVEL = 0.06f
    }

    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var gradientSet = false

    private val targetLevels = FloatArray(BAR_COUNT) { MIN_LEVEL }
    private val currentLevels = FloatArray(BAR_COUNT) { MIN_LEVEL }
    private val barSeeds = FloatArray(BAR_COUNT) { Random.nextFloat() * 1000f }

    private var normalizedAmplitude = 0f
    private var isRecording = false
    private var idlePhase = 0f

    private val animator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 16
        repeatCount = ValueAnimator.INFINITE
        addUpdateListener { tick() }
    }

    init {
        setLayerType(LAYER_TYPE_HARDWARE, null)
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        animator.start()
    }

    override fun onDetachedFromWindow() {
        animator.cancel()
        super.onDetachedFromWindow()
    }

    fun setRecording(recording: Boolean) {
        isRecording = recording
        if (!recording) normalizedAmplitude = 0f
    }

    /** amplitude גולמי מ-MediaRecorder.maxAmplitude (טווח בערך 0..32767). */
    fun setAmplitude(rawAmplitude: Int) {
        val normalized = min(1f, rawAmplitude / 12000f)
        // Attack מהיר, decay איטי יותר - נראה חי יותר מאשר קפיצות פתאומיות
        normalizedAmplitude = if (normalized > normalizedAmplitude) {
            normalized
        } else {
            normalizedAmplitude * 0.85f + normalized * 0.15f
        }
    }

    private fun tick() {
        idlePhase += 0.05f

        for (i in 0 until BAR_COUNT) {
            targetLevels[i] = if (isRecording) {
                val jitter = sin(idlePhase * 2.3f + barSeeds[i]) * 0.5f + 0.5f
                MIN_LEVEL + normalizedAmplitude * (0.4f + 0.6f * jitter)
            } else {
                val breathing = sin(idlePhase * 0.6f + barSeeds[i] * 0.3f) * 0.5f + 0.5f
                MIN_LEVEL + breathing * 0.05f
            }
            currentLevels[i] += (targetLevels[i] - currentLevels[i]) * 0.25f
        }
        invalidate()
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        barPaint.shader = LinearGradient(
            0f, height.toFloat(), 0f, 0f,
            ContextCompat.getColor(context, R.color.accent_cyan),
            ContextCompat.getColor(context, R.color.accent_violet),
            Shader.TileMode.CLAMP,
        )
        gradientSet = true
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (!gradientSet || width == 0 || height == 0) return

        val gap = width / (BAR_COUNT * 2.2f)
        val barWidth = gap * 1.2f
        val totalBarsWidth = BAR_COUNT * barWidth + (BAR_COUNT - 1) * gap
        var x = (width - totalBarsWidth) / 2f
        val centerY = height / 2f

        for (i in 0 until BAR_COUNT) {
            val level = max(MIN_LEVEL, currentLevels[i])
            val barHeight = height * level
            val radius = barWidth / 2f
            canvas.drawRoundRect(
                x, centerY - barHeight / 2f,
                x + barWidth, centerY + barHeight / 2f,
                radius, radius, barPaint,
            )
            x += barWidth + gap
        }
    }
}
