package com.mertkaan.jarvis

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout

/**
 * Manages the always-on-top wake overlay window.
 *
 * Design spec: bottom 96dp, height 110dp orb halo + caption.
 * Pointer events: pass-through (behind-touch allowed).
 * Animation: fade-in 400ms slide-up.
 *
 * The overlay hosts a FrameLayout. Flutter engine rendering into this
 * view is the preferred path; fallback draws a simple native canvas view.
 */
object OverlayManager {

    private const val TAG = "OverlayManager"

    private var windowManager: WindowManager? = null
    private var overlayView: View? = null
    private val handler = Handler(Looper.getMainLooper())

    /** Call from WakeWordService when wake-word triggers. */
    fun show(context: Context) {
        handler.post {
            if (overlayView != null) return@post          // Already showing

            if (!canDrawOverlays(context)) {
                Log.w(TAG, "SYSTEM_ALERT_WINDOW permission not granted — opening settings")
                requestPermission(context)
                return@post
            }

            val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
            windowManager = wm

            val view = buildNativeOrbView(context)
            overlayView = view

            val params = buildLayoutParams()
            wm.addView(view, params)

            // Fade-in animation
            view.alpha = 0f
            view.translationY = 16f.dpToPx(context)
            view.animate()
                .alpha(1f)
                .translationY(0f)
                .setDuration(400)
                .setInterpolator(android.view.animation.DecelerateInterpolator(1.5f))
                .start()

            Log.i(TAG, "Wake overlay shown")
        }
    }

    /** Remove the overlay (called after TTS finishes + 3s delay). */
    fun hide(context: Context) {
        handler.post {
            val view = overlayView ?: return@post
            view.animate()
                .alpha(0f)
                .translationY(16f.dpToPx(context))
                .setDuration(300)
                .withEndAction {
                    runCatching { windowManager?.removeView(view) }
                    overlayView = null
                    windowManager = null
                }
                .start()
            Log.i(TAG, "Wake overlay hidden")
        }
    }

    /** Schedule auto-hide after [delayMs] (default 3 s after TTS done). */
    fun scheduleHide(context: Context, delayMs: Long = 3_000L) {
        handler.postDelayed({ hide(context) }, delayMs)
    }

    // ──────────────────────────────────────────────────────────────────────────
    // View construction
    // ──────────────────────────────────────────────────────────────────────────

    /**
     * Native canvas orb view — lightweight, no Flutter engine required.
     * Renders the wake widget using the same design as the Flutter WakeWidget:
     *  • 110dp container at bottom of screen
     *  • Animated cyan arc ring
     *  • "J.A.R.V.I.S" caption
     */
    private fun buildNativeOrbView(context: Context): View {
        val frame = FrameLayout(context)
        frame.setBackgroundColor(Color.TRANSPARENT)

        // The orb canvas
        val orbView = WakeOrbView(context)

        val lp = FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.WRAP_CONTENT,
            Gravity.CENTER
        )
        frame.addView(orbView, lp)

        // Tap on orb → launch MainActivity with wake context
        orbView.setOnClickListener {
            val intent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
                putExtra("from_wake_overlay", true)
            }
            context.startActivity(intent)
        }

        return frame
    }

    private fun buildLayoutParams(): WindowManager.LayoutParams {
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }

        return WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            180.dpToPx(null).toInt(),       // ~110dp orb + 70dp padding
            type,
            // FLAG_NOT_FOCUSABLE: touches pass through to the app behind
            // FLAG_NOT_TOUCH_MODAL: only the orb itself is tappable
            // FLAG_LAYOUT_IN_SCREEN: ignore safe area
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                    or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                    or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            y = 96.dpToPx(null).toInt()    // 96dp above screen bottom
        }
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Permission helpers
    // ──────────────────────────────────────────────────────────────────────────

    fun canDrawOverlays(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(context)

    fun requestPermission(context: Context) {
        val intent = Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.parse("package:${context.packageName}")
        ).apply { flags = Intent.FLAG_ACTIVITY_NEW_TASK }
        context.startActivity(intent)
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Helpers
    // ──────────────────────────────────────────────────────────────────────────

    private fun Float.dpToPx(context: Context?): Float {
        val density = context?.resources?.displayMetrics?.density ?: 3f
        return this * density
    }

    private fun Int.dpToPx(context: Context?): Float = toFloat().dpToPx(context)
}
