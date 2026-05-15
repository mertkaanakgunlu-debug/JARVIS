package com.mertkaan.jarvis

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Starts the WakeWordService automatically after device reboot.
 *
 * Requires:
 *  - RECEIVE_BOOT_COMPLETED permission in AndroidManifest
 *  - User must have enabled wake-word in app settings (SharedPreferences)
 *  - App must be battery-optimization exempt (requested at onboarding)
 */
class WakeBootReceiver : BroadcastReceiver() {

    companion object {
        const val TAG = "WakeBootReceiver"
        const val PREF_WAKE_ENABLED = "wake_word_enabled"
        const val PREFS_NAME        = "FlutterSharedPreferences"   // flutter shared_preferences key
        // flutter_shared_preferences prefixes the key with "flutter."
        const val FLUTTER_KEY       = "flutter.wake_word_enabled"
    }

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != "android.intent.action.QUICKBOOT_POWERON") return

        Log.i(TAG, "Boot completed — checking wake-word preference")

        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val enabled = prefs.getBoolean(FLUTTER_KEY, false)

        if (enabled) {
            Log.i(TAG, "Wake-word enabled — starting WakeWordService")
            val serviceIntent = Intent(context, WakeWordService::class.java)
            context.startForegroundService(serviceIntent)
        } else {
            Log.i(TAG, "Wake-word disabled — skipping service start")
        }
    }
}
