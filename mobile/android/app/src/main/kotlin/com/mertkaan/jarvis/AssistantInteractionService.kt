package com.mertkaan.jarvis

import android.content.Intent
import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.util.Log

/**
 * Optional VoiceInteractionService that allows JARVIS to be set as the
 * system default assistant (long-press Home on some launchers).
 *
 * Users can select JARVIS as their default assistant in:
 *   Settings → Apps → Default Apps → Digital Assistant → JARVIS
 *
 * This does NOT override "Hey Google" — it only responds to the manual
 * assist gesture. For always-on wake-word, WakeWordService handles that.
 */
class AssistantInteractionService : VoiceInteractionService() {

    companion object {
        const val TAG = "AssistantService"
    }

    override fun onReady() {
        super.onReady()
        Log.i(TAG, "VoiceInteractionService ready")
    }

    override fun onShutdown() {
        super.onShutdown()
        Log.i(TAG, "VoiceInteractionService shutdown")
    }

    /**
     * Called when the user triggers the assistant (long-press Home or squeeze).
     * We launch MainActivity in voice-triggered mode.
     */
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Launch the main activity in chat mode
        val launchIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra("from_assistant", true)
        }
        startActivity(launchIntent)
        return START_NOT_STICKY
    }
}
