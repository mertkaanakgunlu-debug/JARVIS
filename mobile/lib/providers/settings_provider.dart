import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/config.dart';

final settingsProvider = AsyncNotifierProvider<SettingsNotifier, JarvisSettings>(
  SettingsNotifier.new,
);

class SettingsNotifier extends AsyncNotifier<JarvisSettings> {
  @override
  Future<JarvisSettings> build() => JarvisSettings.load();

  Future<void> update({
    String? host,
    String? apiKey,
    String? pcMac,
    String? lanIp,
    bool? autoWake,
    bool? ttsEnabled,
    bool? sttEnabled,
    bool? pushEnabled,
    bool? asyncHeuristic,
    bool? wakeWordEnabled,
    String? language,
    double? gridAlpha,
  }) async {
    if (host != null) await AppConfig.setHost(host);
    if (apiKey != null) await AppConfig.setApiKey(apiKey);
    if (pcMac != null) await AppConfig.setPcMac(pcMac);
    if (lanIp != null) await AppConfig.setLanIp(lanIp);
    if (autoWake != null) await AppConfig.setAutoWake(autoWake);
    if (ttsEnabled != null) await AppConfig.setTtsEnabled(ttsEnabled);
    if (sttEnabled != null) await AppConfig.setSttEnabled(sttEnabled);
    if (pushEnabled != null) await AppConfig.setPushEnabled(pushEnabled);
    if (asyncHeuristic != null) await AppConfig.setAsyncHeuristic(asyncHeuristic);
    if (wakeWordEnabled != null) await AppConfig.setWakeWordEnabled(wakeWordEnabled);
    if (language != null) await AppConfig.setLanguage(language);
    if (gridAlpha != null) await AppConfig.setGridAlpha(gridAlpha);
    ref.invalidateSelf();
  }
}

// Sync convenience provider (falls back to empty settings while loading)
final settingsSyncProvider = Provider<JarvisSettings>((ref) {
  return ref.watch(settingsProvider).valueOrNull ?? const JarvisSettings(
    host: '', apiKey: '', pcMac: '', lanIp: '',
    autoWake: true, ttsEnabled: true, sttEnabled: true,
    pushEnabled: true, asyncHeuristic: true, wakeWordEnabled: false,
    language: 'tr', gridAlpha: 0.04,
  );
});
