package com.mertkaan.jarvis

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {

    companion object {
        const val WAKE_CHANNEL = "jarvis/wake"
        const val WOL_CHANNEL  = "jarvis/wol"
    }

    private var pendingQuery: String? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        // ── Wake-word service control channel ──────────────────────────
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, WAKE_CHANNEL)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "startService" -> {
                        val intent = Intent(this, WakeWordService::class.java)
                        startForegroundService(intent)
                        result.success(null)
                    }
                    "stopService" -> {
                        stopService(Intent(this, WakeWordService::class.java))
                        result.success(null)
                    }
                    "isRunning" -> {
                        result.success(WakeWordService.isRunning)
                    }
                    "getPendingQuery" -> {
                        result.success(pendingQuery)
                        pendingQuery = null
                    }
                    else -> result.notImplemented()
                }
            }

        // ── Wake-on-LAN UDP channel ──────────────────────────────────
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, WOL_CHANNEL)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "sendMagicPacket" -> {
                        val mac = call.argument<String>("mac") ?: ""
                        val ip  = call.argument<String>("ip")  ?: "255.255.255.255"
                        val port = call.argument<Int>("port")  ?: 9
                        Thread {
                            try {
                                WolHelper.send(mac, ip, port)
                                result.success(true)
                            } catch (e: Exception) {
                                result.error("WOL_ERROR", e.message, null)
                            }
                        }.start()
                    }
                    else -> result.notImplemented()
                }
            }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        // Wake widget tap delivers query via custom URI: jarvis://query?text=...
        val data: Uri? = intent?.data
        if (data?.scheme == "jarvis" && data.host == "query") {
            pendingQuery = data.getQueryParameter("text")
        }
        // Also handle query passed from WakeWordService via extra
        intent?.getStringExtra("wake_query")?.let { pendingQuery = it }
    }
}
