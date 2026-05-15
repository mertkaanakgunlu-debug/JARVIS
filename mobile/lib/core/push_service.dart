import 'dart:io';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';

typedef PushHandler = void Function(RemoteMessage message);

class PushService {
  static PushHandler? _foregroundHandler;

  static Future<void> init() async {
    await Firebase.initializeApp();
    final messaging = FirebaseMessaging.instance;

    await messaging.requestPermission(
      alert: true, badge: true, sound: true,
    );

    // Background handler must be top-level
    FirebaseMessaging.onBackgroundMessage(_bgHandler);

    FirebaseMessaging.onMessage.listen((msg) {
      _foregroundHandler?.call(msg);
    });

    // Android notification channels
    if (Platform.isAndroid) {
      await _createChannels();
    }
  }

  static void setForegroundHandler(PushHandler handler) {
    _foregroundHandler = handler;
  }

  static Future<String?> getToken() =>
      FirebaseMessaging.instance.getToken();

  static Stream<String> get tokenRefreshStream =>
      FirebaseMessaging.instance.onTokenRefresh;

  static Future<void> _createChannels() async {
    // Channels are declared in AndroidManifest; messaging plugin handles them.
  }
}

@pragma('vm:entry-point')
Future<void> _bgHandler(RemoteMessage message) async {
  // Background messages are shown automatically by Firebase plugin.
}
