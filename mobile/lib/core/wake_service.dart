import 'dart:async';
import 'dart:io';
import 'package:flutter/services.dart';
import 'api_client.dart';

class WakeService {
  static const _channel = MethodChannel('jarvis/wake');

  final ApiClient _api;
  final String _pcMac;
  final int _timeoutSec;

  WakeService({
    required ApiClient api,
    required String pcMac,
    int timeoutSec = 30,
  })  : _api = api,
        _pcMac = pcMac,
        _timeoutSec = timeoutSec;

  /// Ensure the PC is awake: ping → if offline, send magic packet → retry ping.
  /// Returns true when PC responds, false on timeout.
  Future<bool> ensureAwake({void Function(String)? onStatus}) async {
    onStatus?.call('PC kontrol ediliyor…');
    if (await _api.ping()) return true;

    if (_pcMac.isEmpty) {
      onStatus?.call('MAC adresi girilmedi — otomatik uyandırma devre dışı.');
      return false;
    }

    onStatus?.call('PC uyuyor — magic packet gönderiliyor…');
    await _sendMagicPacket(_pcMac);

    final deadline = DateTime.now().add(Duration(seconds: _timeoutSec));
    while (DateTime.now().isBefore(deadline)) {
      await Future.delayed(const Duration(seconds: 3));
      onStatus?.call('PC uyanıyor…');
      if (await _api.ping()) {
        onStatus?.call('PC hazır.');
        return true;
      }
    }
    onStatus?.call('PC erişilemiyor — manuel kontrol edin.');
    return false;
  }

  Future<void> _sendMagicPacket(String mac) async {
    if (Platform.isAndroid || Platform.isIOS) {
      try {
        await _channel.invokeMethod('sendMagicPacket', {'mac': mac});
        return;
      } catch (_) {}
    }
    // Dart fallback via UDP
    try {
      final socket = await RawDatagramSocket.bind(InternetAddress.anyIPv4, 0);
      socket.broadcastEnabled = true;
      final pkt = _buildPacket(mac);
      socket.send(pkt, InternetAddress('255.255.255.255'), 9);
      socket.close();
    } catch (_) {}
  }

  static List<int> _buildPacket(String mac) {
    final hex = mac.replaceAll(RegExp(r'[:\-.]'), '');
    final macBytes = <int>[];
    for (var i = 0; i < 12; i += 2) {
      macBytes.add(int.parse(hex.substring(i, i + 2), radix: 16));
    }
    return [
      ...List.filled(6, 0xFF),
      ...List.generate(16, (_) => macBytes).expand((b) => b),
    ];
  }

  // Start / stop the Android wake-word foreground service
  static Future<void> startWakeWordService() async {
    try {
      await _channel.invokeMethod('startService');
    } catch (_) {}
  }

  static Future<void> stopWakeWordService() async {
    try {
      await _channel.invokeMethod('stopService');
    } catch (_) {}
  }

  static Future<bool> isWakeWordServiceRunning() async {
    try {
      return await _channel.invokeMethod<bool>('isRunning') ?? false;
    } catch (_) {
      return false;
    }
  }

  /// After wake-word triggers and user taps orb, app may have a
  /// pending query passed from the overlay intent.
  static Future<String?> getPendingQuery() async {
    try {
      return await _channel.invokeMethod<String>('getPendingQuery');
    } catch (_) {
      return null;
    }
  }
}
