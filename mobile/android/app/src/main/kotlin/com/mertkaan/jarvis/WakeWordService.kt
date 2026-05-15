package com.mertkaan.jarvis

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log
import androidx.core.app.NotificationCompat
import java.nio.FloatBuffer

/**
 * Always-on foreground service that listens for "Hey JARVIS" using
 * an ONNX wake-word model (openWakeWord hey_jarvis_v0.1.onnx).
 *
 * When the wake-word is detected:
 *  1. Haptic feedback
 *  2. OverlayManager shows the bottom-sheet wake orb
 *  3. STT is started inside the overlay (handled by Flutter via MethodChannel)
 */
class WakeWordService : Service() {

    companion object {
        const val TAG = "WakeWordService"
        const val CHANNEL_ID = "jarvis_wake"
        const val NOTIF_ID = 1001

        /** Checked by MainActivity / Flutter to know if service is alive. */
        @Volatile var isRunning = false

        // Audio config — openWakeWord expects 16kHz mono 16-bit PCM
        private const val SAMPLE_RATE    = 16_000
        private const val CHUNK_SAMPLES  = 1_280        // 80 ms
        private const val THRESHOLD      = 0.5f
        private const val COOLDOWN_MS    = 3_000L       // 3s between triggers
    }

    private var audioRecord: AudioRecord? = null
    private var ortEnv:    OrtEnvironment? = null
    private var ortSession: OrtSession?   = null

    @Volatile private var running = false
    private var lastTriggerTime = 0L

    // ──────────────────────────────────────────────────────────────────────────
    // Lifecycle
    // ──────────────────────────────────────────────────────────────────────────

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        isRunning = true
        loadOnnxModel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIF_ID, buildNotification())
        if (!running) {
            running = true
            Thread(::audioLoop, "jarvis-wake-audio").start()
        }
        return START_STICKY   // Restart if killed by OS
    }

    override fun onDestroy() {
        running   = false
        isRunning = false
        audioRecord?.stop()
        audioRecord?.release()
        ortSession?.close()
        ortEnv?.close()
        OverlayManager.hide(this)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ──────────────────────────────────────────────────────────────────────────
    // ONNX model
    // ──────────────────────────────────────────────────────────────────────────

    private fun loadOnnxModel() {
        try {
            ortEnv = OrtEnvironment.getEnvironment()
            val modelBytes = assets.open("wake/hey_jarvis_v0.1.onnx").readBytes()
            ortSession = ortEnv!!.createSession(modelBytes, OrtSession.SessionOptions())
            Log.i(TAG, "ONNX wake-word model loaded (${modelBytes.size / 1024} KB)")
        } catch (e: Exception) {
            Log.e(TAG, "ONNX model load failed: ${e.message}")
            // Fall back to keyword-spotting via STT (no-op here; model required)
        }
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Audio loop (runs on background thread)
    // ──────────────────────────────────────────────────────────────────────────

    private fun audioLoop() {
        val bufferSize = maxOf(
            AudioRecord.getMinBufferSize(SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT),
            CHUNK_SAMPLES * 2
        )

        try {
            @Suppress("DEPRECATION")
            audioRecord = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize
            )
            audioRecord!!.startRecording()
        } catch (e: SecurityException) {
            Log.e(TAG, "RECORD_AUDIO permission denied")
            stopSelf()
            return
        }

        val pcmBuf = ShortArray(CHUNK_SAMPLES)

        while (running) {
            val read = audioRecord!!.read(pcmBuf, 0, CHUNK_SAMPLES)
            if (read <= 0) continue

            val score = infer(pcmBuf, read)
            if (score >= THRESHOLD) {
                val now = System.currentTimeMillis()
                if (now - lastTriggerTime > COOLDOWN_MS) {
                    lastTriggerTime = now
                    onWakeWordDetected()
                }
            }
        }

        audioRecord?.stop()
    }

    // ──────────────────────────────────────────────────────────────────────────
    // ONNX inference
    // ──────────────────────────────────────────────────────────────────────────

    private fun infer(pcm: ShortArray, length: Int): Float {
        val session = ortSession ?: return 0f
        val env     = ortEnv     ?: return 0f

        return try {
            // Normalise: 16-bit PCM → float [-1, 1]
            val floatBuf = FloatBuffer.allocate(length)
            for (i in 0 until length) floatBuf.put(pcm[i] / 32768f)
            floatBuf.flip()

            val inputName = session.inputNames.iterator().next()
            val inputTensor = OnnxTensor.createTensor(
                env,
                floatBuf,
                longArrayOf(1, length.toLong())
            )
            val output = session.run(mapOf(inputName to inputTensor))
            val scores = output[0].value as? Array<FloatArray>
            val score  = scores?.firstOrNull()?.firstOrNull() ?: 0f
            output.close()
            inputTensor.close()
            score
        } catch (e: Exception) {
            Log.w(TAG, "Inference error: ${e.message}")
            0f
        }
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Wake-word trigger
    // ──────────────────────────────────────────────────────────────────────────

    private fun onWakeWordDetected() {
        Log.i(TAG, "Wake-word detected!")
        vibrate()
        OverlayManager.show(this)
    }

    private fun vibrate() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val vm = getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
                vm.defaultVibrator.vibrate(
                    VibrationEffect.createOneShot(60, VibrationEffect.DEFAULT_AMPLITUDE)
                )
            } else {
                @Suppress("DEPRECATION")
                val v = getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    v.vibrate(VibrationEffect.createOneShot(60, VibrationEffect.DEFAULT_AMPLITUDE))
                } else {
                    @Suppress("DEPRECATION")
                    v.vibrate(60)
                }
            }
        } catch (_: Exception) {}
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Notification
    // ──────────────────────────────────────────────────────────────────────────

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "JARVIS Wake-Word",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Always-on wake-word detection"
            setShowBadge(false)
        }
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        // Tap notification → open app
        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val openPi = PendingIntent.getActivity(
            this, 0, openIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        // "Stop" action
        val stopIntent = Intent(this, WakeWordService::class.java).apply {
            action = "STOP"
        }
        val stopPi = PendingIntent.getService(
            this, 1, stopIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle(getString(R.string.wake_notification_title))
            .setContentText(getString(R.string.wake_notification_text))
            .setContentIntent(openPi)
            .addAction(android.R.drawable.ic_delete, "Durdur", stopPi)
            .setOngoing(true)
            .setSilent(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }
}
