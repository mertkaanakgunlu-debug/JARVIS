import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

class AppConfig {
  static const _secureStorage = FlutterSecureStorage();

  // Keys
  static const _kHost = 'jarvis_host';
  static const _kApiKey = 'jarvis_api_key';
  static const _kMac = 'jarvis_pc_mac';
  static const _kLanIp = 'jarvis_lan_ip';
  static const _kAutoWake = 'jarvis_auto_wake';
  static const _kTtsEnabled = 'jarvis_tts_enabled';
  static const _kSttEnabled = 'jarvis_stt_enabled';
  static const _kPushEnabled = 'jarvis_push_enabled';
  static const _kAsyncHeuristic = 'jarvis_async_heuristic';
  static const _kWakeWordEnabled = 'jarvis_wake_word_enabled';
  static const _kLanguage = 'jarvis_language';
  static const _kGridAlpha = 'jarvis_grid_alpha';

  // Secure reads
  static Future<String> getApiKey() async =>
      await _secureStorage.read(key: _kApiKey) ?? '';

  static Future<String> getPcMac() async =>
      await _secureStorage.read(key: _kMac) ?? '';

  static Future<void> setApiKey(String v) =>
      _secureStorage.write(key: _kApiKey, value: v);

  static Future<void> setPcMac(String v) =>
      _secureStorage.write(key: _kMac, value: v);

  // Prefs reads/writes
  static Future<SharedPreferences> _prefs() => SharedPreferences.getInstance();

  static Future<String> getHost() async =>
      (await _prefs()).getString(_kHost) ?? '';

  static Future<String> getLanIp() async =>
      (await _prefs()).getString(_kLanIp) ?? '';

  static Future<bool> getAutoWake() async =>
      (await _prefs()).getBool(_kAutoWake) ?? true;

  static Future<bool> getTtsEnabled() async =>
      (await _prefs()).getBool(_kTtsEnabled) ?? true;

  static Future<bool> getSttEnabled() async =>
      (await _prefs()).getBool(_kSttEnabled) ?? true;

  static Future<bool> getPushEnabled() async =>
      (await _prefs()).getBool(_kPushEnabled) ?? true;

  static Future<bool> getAsyncHeuristic() async =>
      (await _prefs()).getBool(_kAsyncHeuristic) ?? true;

  static Future<bool> getWakeWordEnabled() async =>
      (await _prefs()).getBool(_kWakeWordEnabled) ?? false;

  static Future<String> getLanguage() async =>
      (await _prefs()).getString(_kLanguage) ?? 'tr';

  static Future<double> getGridAlpha() async =>
      (await _prefs()).getDouble(_kGridAlpha) ?? 0.04;

  static Future<void> setHost(String v) async =>
      (await _prefs()).setString(_kHost, v);

  static Future<void> setLanIp(String v) async =>
      (await _prefs()).setString(_kLanIp, v);

  static Future<void> setAutoWake(bool v) async =>
      (await _prefs()).setBool(_kAutoWake, v);

  static Future<void> setTtsEnabled(bool v) async =>
      (await _prefs()).setBool(_kTtsEnabled, v);

  static Future<void> setSttEnabled(bool v) async =>
      (await _prefs()).setBool(_kSttEnabled, v);

  static Future<void> setPushEnabled(bool v) async =>
      (await _prefs()).setBool(_kPushEnabled, v);

  static Future<void> setAsyncHeuristic(bool v) async =>
      (await _prefs()).setBool(_kAsyncHeuristic, v);

  static Future<void> setWakeWordEnabled(bool v) async =>
      (await _prefs()).setBool(_kWakeWordEnabled, v);

  static Future<void> setLanguage(String v) async =>
      (await _prefs()).setString(_kLanguage, v);

  static Future<void> setGridAlpha(double v) async =>
      (await _prefs()).setDouble(_kGridAlpha, v);
}

class JarvisSettings {
  final String host;
  final String apiKey;
  final String pcMac;
  final String lanIp;
  final bool autoWake;
  final bool ttsEnabled;
  final bool sttEnabled;
  final bool pushEnabled;
  final bool asyncHeuristic;
  final bool wakeWordEnabled;
  final String language;
  final double gridAlpha;

  const JarvisSettings({
    required this.host,
    required this.apiKey,
    required this.pcMac,
    required this.lanIp,
    required this.autoWake,
    required this.ttsEnabled,
    required this.sttEnabled,
    required this.pushEnabled,
    required this.asyncHeuristic,
    required this.wakeWordEnabled,
    required this.language,
    required this.gridAlpha,
  });

  static Future<JarvisSettings> load() async {
    final results = await Future.wait([
      AppConfig.getHost(),
      AppConfig.getApiKey(),
      AppConfig.getPcMac(),
      AppConfig.getLanIp(),
      AppConfig.getAutoWake(),
      AppConfig.getTtsEnabled(),
      AppConfig.getSttEnabled(),
      AppConfig.getPushEnabled(),
      AppConfig.getAsyncHeuristic(),
      AppConfig.getWakeWordEnabled(),
      AppConfig.getLanguage(),
      AppConfig.getGridAlpha(),
    ]);
    return JarvisSettings(
      host: results[0] as String,
      apiKey: results[1] as String,
      pcMac: results[2] as String,
      lanIp: results[3] as String,
      autoWake: results[4] as bool,
      ttsEnabled: results[5] as bool,
      sttEnabled: results[6] as bool,
      pushEnabled: results[7] as bool,
      asyncHeuristic: results[8] as bool,
      wakeWordEnabled: results[9] as bool,
      language: results[10] as String,
      gridAlpha: results[11] as double,
    );
  }
}
