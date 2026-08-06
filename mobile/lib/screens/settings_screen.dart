import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../widgets/grid_background.dart';
import '../providers/settings_provider.dart';
import '../providers/api_provider.dart';
import '../core/wake_service.dart';

class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  final _hostCtrl = TextEditingController();
  final _keyCtrl = TextEditingController();
  final _macCtrl = TextEditingController();
  final _lanIpCtrl = TextEditingController();
  bool _initialized = false;
  String? _pingResult;
  bool _pinging = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_initialized) {
      final s = ref.read(settingsSyncProvider);
      _hostCtrl.text = s.host;
      _keyCtrl.text = s.apiKey;
      _macCtrl.text = s.pcMac;
      _lanIpCtrl.text = s.lanIp;
      _initialized = true;
    }
  }

  @override
  void dispose() {
    _hostCtrl.dispose();
    _keyCtrl.dispose();
    _macCtrl.dispose();
    _lanIpCtrl.dispose();
    super.dispose();
  }

  Future<void> _testPing() async {
    final api = ref.read(apiClientProvider);
    if (api == null) {
      setState(() => _pingResult = '⚠ Host girilmemiş.');
      return;
    }
    setState(() { _pinging = true; _pingResult = null; });
    final ok = await api.ping();
    setState(() {
      _pinging = false;
      _pingResult = ok ? '✓ PC çevrimiçi ve yanıt veriyor.' : '✗ Bağlantı kurulamadı.';
    });
  }

  Future<void> _testWake() async {
    final api = ref.read(apiClientProvider);
    final settings = ref.read(settingsSyncProvider);
    if (api == null || settings.pcMac.isEmpty) {
      setState(() => _pingResult = '⚠ MAC adresi girilmemiş.');
      return;
    }
    final wake = WakeService(api: api, pcMac: settings.pcMac);
    setState(() { _pinging = true; _pingResult = 'Magic packet gönderiliyor…'; });
    final ok = await wake.ensureAwake(
      onStatus: (msg) => setState(() => _pingResult = msg),
    );
    setState(() {
      _pinging = false;
      _pingResult = ok ? '✓ PC uyandı!' : '✗ PC erişilemiyor.';
    });
  }

  Future<void> _save() async {
    await ref.read(settingsProvider.notifier).saveSettings(
      host: _hostCtrl.text.trim(),
      apiKey: _keyCtrl.text.trim(),
      pcMac: _macCtrl.text.trim(),
      lanIp: _lanIpCtrl.text.trim(),
    );
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Ayarlar kaydedildi.')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final settings = ref.watch(settingsSyncProvider);

    return Scaffold(
      backgroundColor: JarvisColors.bg,
      body: GridBackground(
        child: SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(18, 12, 18, 0),
                child: Row(
                  children: [
                    GestureDetector(
                      onTap: () => Navigator.pop(context),
                      child: const Icon(Icons.arrow_back_ios,
                          color: JarvisColors.inkDim, size: 18),
                    ),
                    const SizedBox(width: 12),
                    Text('SETTINGS', style: JarvisText.wordmark),
                    const Spacer(),
                    TextButton(
                      onPressed: _save,
                      child: Text('KAYDET',
                          style: JarvisText.chip.copyWith(
                              color: JarvisColors.cyanSoft)),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(18, 12, 18, 40),
                  children: [
                    _Section('BAĞLANTI'),
                    _Field('Backend Host', _hostCtrl,
                        hint: '100.64.1.5:8000'),
                    _Field('API Key', _keyCtrl,
                        hint: 'JARVIS_API_KEY', obscure: true),
                    _Field('PC MAC (WoL)', _macCtrl,
                        hint: 'AA:BB:CC:DD:EE:FF'),
                    _Field('PC LAN/Tailscale IP', _lanIpCtrl,
                        hint: '100.64.1.5'),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        _ActionButton('Bağlantı Testi', _testPing),
                        const SizedBox(width: 8),
                        _ActionButton('Wake Testi', _testWake),
                      ],
                    ),
                    if (_pinging) const Padding(
                      padding: EdgeInsets.only(top: 8),
                      child: LinearProgressIndicator(color: JarvisColors.cyan),
                    ),
                    if (_pingResult != null) Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(_pingResult!, style: JarvisText.chip.copyWith(
                          color: _pingResult!.startsWith('✓')
                              ? JarvisColors.green
                              : JarvisColors.amber)),
                    ),

                    _Section('OTOMATİK UYANDIRMA'),
                    _Switch('Auto-wake (PC uykudaysa uyandır)',
                        settings.autoWake,
                        (v) => ref.read(settingsProvider.notifier).saveSettings(autoWake: v)),

                    _Section('SES'),
                    _Switch('TTS (JARVIS sesli yanıt)',
                        settings.ttsEnabled,
                        (v) => ref.read(settingsProvider.notifier).saveSettings(ttsEnabled: v)),
                    _Switch('STT (mikrofon girişi)',
                        settings.sttEnabled,
                        (v) => ref.read(settingsProvider.notifier).saveSettings(sttEnabled: v)),
                    _Switch('Wake-word always-on ("Hey JARVIS")',
                        settings.wakeWordEnabled,
                        (v) => ref.read(settingsProvider.notifier).saveSettings(wakeWordEnabled: v)),

                    _Section('GÖREVLER'),
                    _Switch('Async heuristik (uzun task\'lar arka plana)',
                        settings.asyncHeuristic,
                        (v) => ref.read(settingsProvider.notifier).saveSettings(asyncHeuristic: v)),

                    _Section('BİLDİRİMLER'),
                    _Switch('Push aktif',
                        settings.pushEnabled,
                        (v) => ref.read(settingsProvider.notifier).saveSettings(pushEnabled: v)),
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: _ActionButton('Push Test', () async {
                        final api = ref.read(apiClientProvider);
                        if (api == null) return;
                        await api.post('/push/test', data: {
                          'title': 'JARVIS Test',
                          'body': 'Test bildirimi gönderildi.',
                        });
                      }),
                    ),

                    _Section('GÖRÜNÜM'),
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text('Grid yoğunluğu', style: JarvisText.sectionHeader),
                    ),
                    Slider(
                      value: settings.gridAlpha.clamp(0.02, 0.15),
                      min: 0.02, max: 0.15,
                      activeColor: JarvisColors.cyan,
                      inactiveColor: JarvisColors.lineDim,
                      onChanged: (v) =>
                          ref.read(settingsProvider.notifier).saveSettings(gridAlpha: v),
                    ),

                    const SizedBox(height: 24),
                    Text('JARVIS Mobile v1.0.0',
                        textAlign: TextAlign.center,
                        style: JarvisText.chip.copyWith(color: JarvisColors.inkFaint)),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Section extends StatelessWidget {
  final String title;
  const _Section(this.title);
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 20, bottom: 8),
    child: Text('// $title', style: JarvisText.sectionHeader),
  );
}

class _Field extends StatelessWidget {
  final String label;
  final TextEditingController ctrl;
  final String? hint;
  final bool obscure;

  const _Field(this.label, this.ctrl, {this.hint, this.obscure = false});

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 10),
    child: TextField(
      controller: ctrl,
      obscureText: obscure,
      style: const TextStyle(
          color: JarvisColors.ink, fontFamily: 'ShareTechMono', fontSize: 12),
      decoration: InputDecoration(
        labelText: label,
        labelStyle: JarvisText.chip,
        hintText: hint,
        hintStyle: JarvisText.chip.copyWith(color: JarvisColors.inkFaint),
        border: const OutlineInputBorder(),
        enabledBorder: const OutlineInputBorder(
          borderSide: BorderSide(color: JarvisColors.lineDim),
        ),
        focusedBorder: const OutlineInputBorder(
          borderSide: BorderSide(color: JarvisColors.cyan),
        ),
        isDense: true,
        filled: true,
        fillColor: Colors.black.withValues(alpha: 0.3),
      ),
    ),
  );
}

class _Switch extends StatelessWidget {
  final String label;
  final bool value;
  final ValueChanged<bool> onChanged;
  const _Switch(this.label, this.value, this.onChanged);

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 2),
    child: Row(
      children: [
        Expanded(child: Text(label, style: JarvisText.chip.copyWith(fontSize: 11))),
        Switch(
          value: value,
          onChanged: onChanged,
          activeThumbColor: JarvisColors.cyan,
          trackColor: WidgetStateProperty.resolveWith(
              (s) => s.contains(WidgetState.selected)
                  ? JarvisColors.cyan.withValues(alpha: 0.3)
                  : JarvisColors.lineDim),
        ),
      ],
    ),
  );
}

class _ActionButton extends StatelessWidget {
  final String label;
  final VoidCallback onPressed;
  const _ActionButton(this.label, this.onPressed);

  @override
  Widget build(BuildContext context) => OutlinedButton(
    onPressed: onPressed,
    style: OutlinedButton.styleFrom(
      foregroundColor: JarvisColors.cyanSoft,
      side: const BorderSide(color: JarvisColors.lineDim),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      textStyle: JarvisText.chip,
    ),
    child: Text(label),
  );
}
