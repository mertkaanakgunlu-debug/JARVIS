package com.mertkaan.jarvis

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.SweepGradient
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.min

/**
 * Native canvas implementation of the JARVIS wake overlay orb.
 *
 * Mirrors the Flutter WakeWidget design:
 *  • 110dp container (orb 96dp + caption text below)
 *  • Cyan arc ring that rotates continuously (9s period)
 *  • Center dot (cyanSoft)
 *  • "J.A.R.V.I.S" caption (ShareTechMono-style, uppercase)
 *  • State label: LISTENING / THINKING / RESPONDING
 */
class WakeOrbView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0
) : View(context, attrs, defStyle) {

    // ── Design colors (from JarvisColors) ──────────────────────────────────
    private val colorCyan     = Color.parseColor("#22D3EE")
    private val colorCyanSoft = Color.parseColor("#67E8F9")
    private val colorLineDim  = Color.parseColor("#4D22D3EE")   // ~0x2E opacity
    private val colorBg       = Color.parseColor("#CC000000")   // semi-black

    // ── Paints ──────────────────────────────────────────────────────────────
    private val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = colorBg
        style = Paint.Style.FILL
    }
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = colorLineDim
        style = Paint.Style.STROKE
        strokeWidth = dpToPx(1f)
    }
    private val arcPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dpToPx(1.5f)
        strokeCap  = Paint.Cap.ROUND
    }
    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = colorCyanSoft
        style = Paint.Style.FILL
        setShadowLayer(dpToPx(4f), 0f, 0f, colorCyan)
    }

    // ── Animation ───────────────────────────────────────────────────────────
    private var rotationAngle = 0f
    private val rotator = ValueAnimator.ofFloat(0f, 360f).apply {
        duration     = 9_000L
        repeatCount  = ValueAnimator.INFINITE
        repeatMode   = ValueAnimator.RESTART
        interpolator = LinearInterpolator()
        addUpdateListener {
            rotationAngle = it.animatedValue as Float
            invalidate()
        }
        start()
    }

    // ── State ────────────────────────────────────────────────────────────────
    var stateLabel: String = "LISTENING"
        set(value) { field = value; invalidate() }

    // ── Geometry ─────────────────────────────────────────────────────────────
    private val orbRect = RectF()
    private val orbRadius get() = min(width, height) / 2f * 0.42f

    // ── Drawing ───────────────────────────────────────────────────────────────
    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val r  = orbRadius

        // Background circle
        bgPaint.setShadowLayer(dpToPx(18f), 0f, 0f, Color.argb(80, 0x22, 0xD3, 0xEE))
        canvas.drawCircle(cx, cy, r + dpToPx(8f), bgPaint)

        // Dim ring border
        canvas.drawCircle(cx, cy, r, ringPaint)

        // Rotating arc (border-top-color cyan, rest transparent)
        orbRect.set(cx - r, cy - r, cx + r, cy + r)
        val shader = SweepGradient(cx, cy,
            intArrayOf(Color.TRANSPARENT, colorCyan, colorCyan, Color.TRANSPARENT),
            floatArrayOf(0f, 0.05f, 0.15f, 0.4f))
        arcPaint.shader = shader
        canvas.save()
        canvas.rotate(rotationAngle, cx, cy)
        canvas.drawArc(orbRect, -90f, 60f, false, arcPaint)
        canvas.restore()

        // Center dot
        canvas.drawCircle(cx, cy, dpToPx(4f), dotPaint)

        // State label below orb
        val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color     = Color.argb(180, 0x7B, 0xA9, 0xB3)  // inkDim
            textSize  = dpToPx(8f)
            letterSpacing = 0.3f
            textAlign = Paint.Align.CENTER
            typeface  = android.graphics.Typeface.MONOSPACE
        }
        canvas.drawText(stateLabel, cx, cy + r + dpToPx(20f), textPaint)

        // Caption "J.A.R.V.I.S"
        val captionPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color     = colorCyanSoft
            textSize  = dpToPx(9f)
            letterSpacing = 0.24f
            textAlign = Paint.Align.CENTER
            typeface  = android.graphics.Typeface.MONOSPACE
            setShadowLayer(dpToPx(6f), 0f, 0f, colorCyan)
        }
        canvas.drawText("J.A.R.V.I.S", cx, cy + r + dpToPx(34f), captionPaint)
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val desiredPx = dpToPx(120f).toInt()
        setMeasuredDimension(
            resolveSize(desiredPx, widthMeasureSpec),
            resolveSize(desiredPx, heightMeasureSpec)
        )
    }

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        rotator.cancel()
    }

    private fun dpToPx(dp: Float) = dp * resources.displayMetrics.density
}
