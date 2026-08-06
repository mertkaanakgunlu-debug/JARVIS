import 'dart:async';
import 'dart:convert';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:speech_to_text/speech_to_text.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../theme/state_accent.dart';
import '../widgets/grid_background.dart';
import '../widgets/jarvis_orb.dart';
import '../widgets/orbital_rings.dart';
import '../widgets/hud_panel_card.dart';
import '../models/conversation_state.dart';
import '../models/transcript_turn.dart';
import '../providers/state_provider.dart';
import '../providers/ws_provider.dart';
import '../providers/todos_provider.dart';
import '../providers/finance_provider.dart';
import '../providers/tasks_provider.dart';
import '../providers/transcript_provider.dart';
import '../providers/api_provider.dart';
import '../providers/settings_provider.dart';
import '../core/wake_service.dart';

// ─────────────────────────────────────────────────────────────────────────────
// HomeScreen — Merged CORE + COMMS
// ─────────────────────────────────────────────────────────────────────────────

class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});
  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  final _textCtrl   = TextEditingController();
  final _scrollCtrl = ScrollController();
  late final FlutterTts _tts;
  final _ttsBuffer  = StringBuffer();

  bool _isComposing = false;
  bool _isSending   = false;
  String? _loadingNote;

  @override
  void initState() {
    super.initState();
    _tts = FlutterTts();
    _initTts();
    _initTtsHandlers();
  }

  Future<void> _initTts() async {
    await _tts.setLanguage('tr-TR');
    await _tts.setSpeechRate(0.48);
    await _tts.setVolume(1.0);
    await _tts.setPitch(0.85);
    try {
      final voices = await _tts.getVoices as List?;
      if (voices != null) {
        Map maleVoice = voices.cast<Map>().firstWhere(
          (v) =>
              (v['locale'] as String? ?? '').startsWith('tr') &&
              (v['name'] as String? ?? '').toLowerCase().contains('male'),
          orElse: () => <String, dynamic>{},
        );
        if (maleVoice.isEmpty) {
          maleVoice = voices.cast<Map>().firstWhere(
            (v) => (v['locale'] as String? ?? '').startsWith('tr'),
            orElse: () => <String, dynamic>{},
          );
        }
        if (maleVoice.isNotEmpty && maleVoice['name'] != null) {
          await _tts.setVoice({
            'name': maleVoice['name'] as String,
            'locale': (maleVoice['locale'] as String? ?? 'tr-TR'),
          });
        }
      }
    } catch (_) {}
  }

  @override
  void dispose() {
    _tts.stop();
    _textCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  // ── TTS ──────────────────────────────────────────────────────────────────

  final _ttsQueue   = <String>[];
  bool  _ttsPlaying = false;

  Future<void> _initTtsHandlers() async {
    _tts.setCompletionHandler(() => _ttsPlayNext());
    _tts.setErrorHandler((_) { _ttsPlaying = false; _ttsPlayNext(); });
  }

  void _ttsPlayNext() {
    if (_ttsQueue.isEmpty) { _ttsPlaying = false; return; }
    _ttsPlaying = true;
    _tts.speak(_ttsQueue.removeAt(0));
  }

  void _ttsEnqueue(String sentence) {
    final clean = _sanitizeForTts(sentence);
    if (clean.isEmpty) return;
    _ttsQueue.add(clean);
    if (!_ttsPlaying) _ttsPlayNext();
  }

  void _speakChunk(String chunk) {
    if (!ref.read(settingsSyncProvider).ttsEnabled) return;
    _ttsBuffer.write(chunk);
    final text = _ttsBuffer.toString();
    final idx = _sentenceEnd(text);
    if (idx >= 0) {
      final sentence = text.substring(0, idx + 1).trim();
      _ttsBuffer.clear();
      if (idx + 1 < text.length) _ttsBuffer.write(text.substring(idx + 1));
      if (sentence.isNotEmpty) _ttsEnqueue(sentence);
    }
  }

  void _flushTts() {
    if (!ref.read(settingsSyncProvider).ttsEnabled) {
      _ttsBuffer.clear();
      return;
    }
    final remaining = _ttsBuffer.toString().trim();
    _ttsBuffer.clear();
    if (remaining.isNotEmpty) _ttsEnqueue(remaining);
  }

  static int _sentenceEnd(String text) {
    for (int i = 0; i < text.length; i++) {
      final c = text[i];
      if (c == '.' || c == '!' || c == '?') {
        if (i + 1 >= text.length ||
            text[i + 1] == ' ' ||
            text[i + 1] == '\n') {
          return i;
        }
      }
    }
    return -1;
  }

  static String _sanitizeForTts(String text) {
    // Bold / italic
    text = text.replaceAllMapped(RegExp(r'\*\*(.+?)\*\*', dotAll: true), (m) => m[1]!);
    text = text.replaceAllMapped(RegExp(r'\*(.+?)\*',     dotAll: true), (m) => m[1]!);
    text = text.replaceAllMapped(RegExp(r'__(.+?)__',     dotAll: true), (m) => m[1]!);
    text = text.replaceAllMapped(RegExp(r'_(.+?)_',       dotAll: true), (m) => m[1]!);
    // Headers
    text = text.replaceAll(RegExp(r'^#{1,6}\s+', multiLine: true), '');
    // Inline code
    text = text.replaceAllMapped(RegExp(r'`([^`]+)`'), (m) => m[1]!);
    // Markdown links
    text = text.replaceAllMapped(RegExp(r'\[([^\]]+)\]\([^)]+\)'), (m) => m[1]!);
    // Bullet / numbered list markers
    text = text.replaceAll(RegExp(r'^\s*[-•*]\s+', multiLine: true), '');
    text = text.replaceAll(RegExp(r'^\s*\d+\.\s+', multiLine: true), '');
    // Horizontal rules
    text = text.replaceAll(RegExp(r'^[-=_]{3,}$', multiLine: true), '');
    // Emojis (basic Unicode ranges for common emoji blocks)
    text = text.replaceAll(RegExp(r'[\u{1F300}-\u{1FAFF}]', unicode: true), '');
    text = text.replaceAll(RegExp(r'[\u{2600}-\u{27BF}]',   unicode: true), '');
    text = text.replaceAll(RegExp(r'[\u{FE00}-\u{FEFF}]',   unicode: true), '');
    // Ellipsis → pause
    text = text.replaceAll('…', ',').replaceAll('...', ',');
    // Strip any remaining bare markdown symbols
    text = text.replaceAll(RegExp(r'[*#_~]+'), '');
    // Paragraph breaks → sentence boundary
    text = text.replaceAll(RegExp(r'\n{2,}'), '. ');
    text = text.replaceAll('\n', ' ');
    // Collapse whitespace
    text = text.replaceAll(RegExp(r'\s+'), ' ');
    return text.trim();
  }

  // ── Send ─────────────────────────────────────────────────────────────────

  Future<void> _send(String query) async {
    query = query.trim();
    if (query.isEmpty || _isSending) return;
    setState(() {
      _isSending = true;
      _loadingNote = null;
    });
    _tts.stop();
    _ttsBuffer.clear();
    _ttsQueue.clear();
    _ttsPlaying = false;

    ref
        .read(transcriptProvider.notifier)
        .add(TranscriptTurn(who: 'u', text: query));
    _textCtrl.clear();
    setState(() => _isComposing = false);
    _scrollToBottom();

    final api = ref.read(apiClientProvider);
    if (api == null) {
      setState(() => _isSending = false);
      return;
    }

    final settings = ref.read(settingsSyncProvider);

    if (settings.autoWake) {
      final wake = WakeService(api: api, pcMac: settings.pcMac);
      final online = await wake.ensureAwake(
        onStatus: (msg) => setState(() => _loadingNote = msg),
      );
      if (!online) {
        ref.read(transcriptProvider.notifier).add(
          const TranscriptTurn(
              who: 'j', text: 'PC erişilemiyor. Manuel kontrol edin.'),
        );
        setState(() {
          _isSending = false;
          _loadingNote = null;
        });
        return;
      }
    }
    setState(() => _loadingNote = null);

    try {
      ref
          .read(transcriptProvider.notifier)
          .add(const TranscriptTurn(who: 'j', text: ''));

      await for (final chunk
          in api.chatStream(query, language: settings.language)) {
        if (chunk == '[DONE]') break;
        if (chunk.startsWith('[ERROR]')) {
          ref
              .read(transcriptProvider.notifier)
              .appendToLast(chunk.substring(7));
          break;
        }
        if (chunk.startsWith('{"async":')) {
          try {
            final j = jsonDecode(chunk) as Map<String, dynamic>;
            if (j['async'] == true) {
              final taskId = j['task_id'] as String?;
              ref.read(transcriptProvider.notifier).appendToLast(
                '🕐 Arka planda çalışıyor — task `$taskId`',
              );
              ref.read(tasksProvider.notifier).refresh();
              break;
            }
          } catch (_) {}
        }
        final clean = chunk.replaceAll(r'\n', '\n');
        ref.read(transcriptProvider.notifier).appendToLast(clean);
        _speakChunk(clean);
        _scrollToBottom();
      }
      _flushTts();
    } catch (e) {
      ref
          .read(transcriptProvider.notifier)
          .appendToLast('Bağlantı hatası: $e');
    } finally {
      setState(() {
        _isSending = false;
        _loadingNote = null;
      });
      _scrollToBottom();
    }
  }

  Future<void> _sendFile(PlatformFile file, String query) async {
    if (_isSending) return;
    setState(() { _isSending = true; _loadingNote = 'Dosya yükleniyor…'; });
    _tts.stop();
    _ttsBuffer.clear();
    _ttsQueue.clear();
    _ttsPlaying = false;

    final displayQuery = query.isNotEmpty ? query : 'Bu dosyayı analiz et.';
    ref.read(transcriptProvider.notifier).add(
      TranscriptTurn(who: 'u', text: '📎 ${file.name}${query.isNotEmpty ? '\n$query' : ''}'),
    );
    ref.read(transcriptProvider.notifier).add(
      const TranscriptTurn(who: 'j', text: ''),
    );
    _scrollToBottom();

    final api = ref.read(apiClientProvider);
    if (api == null) {
      setState(() => _isSending = false);
      return;
    }
    final settings = ref.read(settingsSyncProvider);

    try {
      setState(() => _loadingNote = 'Analiz ediliyor…');
      await for (final chunk in api.uploadFileStream(
        file.path!,
        file.name,
        query: displayQuery,
        language: settings.language,
      )) {
        if (chunk == '[DONE]') break;
        if (chunk.startsWith('[ERROR]')) {
          ref.read(transcriptProvider.notifier).appendToLast(chunk.substring(7));
          break;
        }
        final clean = chunk.replaceAll(r'\n', '\n');
        ref.read(transcriptProvider.notifier).appendToLast(clean);
        _speakChunk(clean);
        _scrollToBottom();
      }
      _flushTts();
    } catch (e) {
      ref.read(transcriptProvider.notifier).appendToLast('Yükleme hatası: $e');
    } finally {
      setState(() { _isSending = false; _loadingNote = null; });
      _scrollToBottom();
    }
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollCtrl.hasClients) {
        _scrollCtrl.animateTo(
          _scrollCtrl.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  // ── Build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final state      = ref.watch(conversationStateProvider);
    final accent     = accentForState(state);
    final wsCount    = ref.watch(vaultCountProvider);
    final restCount  = ref.watch(vaultCountRestProvider).valueOrNull ?? 0;
    final vaultCount = wsCount > 0 ? wsCount : restCount;
    final tasksAsync = ref.watch(tasksProvider);
    final todosAsync = ref.watch(todosProvider);
    final calEvents  = ref.watch(calendarWsProvider);
    final finAsync   = ref.watch(financeSummaryProvider);
    final transcript = ref.watch(transcriptProvider);

    final runningCount =
        tasksAsync.valueOrNull?.where((t) => t.isRunning).length ?? 0;
    final todayDone =
        todosAsync.valueOrNull?.where((t) => t['status'] == 'done').length ?? 0;
    final todayTotal = todosAsync.valueOrNull?.length ?? 0;
    final nextEvent  = calEvents.isNotEmpty ? calEvents.first : null;

    final budgetAsync = ref.watch(financeBudgetProvider);
    final double spend =
        (finAsync.valueOrNull?['expenses'] as num?)?.abs().toDouble() ?? 0;
    final double limit = budgetAsync.valueOrNull?.fold<double>(
          0, (sum, b) => sum + ((b['limit'] as num?)?.toDouble() ?? 0)) ??
        0;

    return GridBackground(
      child: SafeArea(
        child: Stack(
          children: [
            Column(
              children: [
                // ── Top bar ────────────────────────────────────────────────
                _TopBar(
                  state: state,
                  onSettings: () =>
                      Navigator.pushNamed(context, '/settings'),
                ),

                // ── Orb (compact) ──────────────────────────────────────────
                SizedBox(
                  width: 200,
                  height: 200,
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      OrbitalRings(size: 200, color: accent),
                      JarvisOrb(size: 160, state: state),
                    ],
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  labelForState(state),
                  style: JarvisText.sectionHeader.copyWith(
                    color: accent,
                    shadows: [
                      Shadow(color: accent.withValues(alpha: 0.6), blurRadius: 6)
                    ],
                  ),
                ),
                Text(
                  'GEMINI 2.5 FLASH · VERTEX',
                  style: JarvisText.chip
                      .copyWith(color: JarvisColors.inkFaint),
                ),
                const SizedBox(height: 8),

                // ── Compact stats bar ───────────────────────────────────────
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 18),
                  child: HudPanelCard(
                    accentColor: accent,
                    padding: const EdgeInsets.symmetric(
                        horizontal: 8, vertical: 8),
                    child: IntrinsicHeight(
                      child: Row(
                        children: [
                          Expanded(
                            child: _Stat(
                              label: 'TASKS',
                              value: todayTotal > 0
                                  ? '$todayDone/$todayTotal'
                                  : '—',
                            ),
                          ),
                          const _VDivider(),
                          Expanded(
                            child: _Stat(
                              label: 'JOBS',
                              value: '$runningCount',
                              valueColor: runningCount > 0
                                  ? JarvisColors.amber
                                  : null,
                            ),
                          ),
                          const _VDivider(),
                          Expanded(
                            child: _Stat(
                              label: 'VAULT',
                              value:
                                  vaultCount > 0 ? '$vaultCount' : '—',
                            ),
                          ),
                          const _VDivider(),
                          Expanded(
                            child: GestureDetector(
                              onTap: () => Navigator.pushNamed(
                                  context, '/finance_detail'),
                              child: _Stat(
                                label: 'SPEND',
                                value: spend > 0
                                    ? '₺${spend.toStringAsFixed(0)}'
                                    : '—',
                                valueColor: limit > 0 &&
                                        spend / limit > 0.8
                                    ? JarvisColors.amber
                                    : null,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 6),

                // ── COMMS section header ────────────────────────────────────
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 18),
                  child: Row(
                    children: [
                      Text('// COMMS', style: JarvisText.sectionHeader),
                      if (nextEvent != null) ...[
                        const SizedBox(width: 8),
                        Text(
                          '· ${_fmtEvent(nextEvent)}',
                          style: JarvisText.chip.copyWith(
                              color: JarvisColors.inkFaint, fontSize: 9),
                        ),
                      ],
                    ],
                  ),
                ),
                const SizedBox(height: 4),

                // ── Chat messages ───────────────────────────────────────────
                Expanded(
                  child: ListView.builder(
                    controller: _scrollCtrl,
                    padding:
                        const EdgeInsets.fromLTRB(18, 0, 18, 120),
                    itemCount:
                        transcript.length + (_isSending ? 1 : 0),
                    itemBuilder: (ctx, i) {
                      if (i == transcript.length && _isSending) {
                        return _TypingIndicator(note: _loadingNote);
                      }
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: _ChatBubble(turn: transcript[i]),
                      );
                    },
                  ),
                ),
              ],
            ),

            // ── Composer (fixed bottom) ─────────────────────────────────────
            Positioned(
              bottom: 0,
              left: 0,
              right: 0,
              child: _Composer(
                controller: _textCtrl,
                isComposing: _isComposing,
                onChanged: (v) =>
                    setState(() => _isComposing = v.isNotEmpty),
                onSubmit: _send,
                onStopTts: () {
                  _tts.stop();
                  _ttsBuffer.clear();
                  _ttsQueue.clear();
                  _ttsPlaying = false;
                },
                onFileUpload: _sendFile,
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _fmtEvent(Map<String, dynamic> ev) {
    final title = ev['title'] as String? ?? '';
    final start = ev['start'] as String? ?? '';
    if (start.length >= 16) {
      return '${start.substring(11, 16)} · $title';
    }
    return title;
  }
}

// ─────────────────────────── Shared widgets ───────────────────────────────────

class _TopBar extends StatefulWidget {
  final ConversationState state;
  final VoidCallback onSettings;
  const _TopBar({required this.state, required this.onSettings});
  @override
  State<_TopBar> createState() => _TopBarState();
}

class _TopBarState extends State<_TopBar> {
  late Timer _timer;
  late DateTime _now;

  @override
  void initState() {
    super.initState();
    _now = DateTime.now();
    _timer = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted) setState(() => _now = DateTime.now());
    });
  }

  @override
  void dispose() {
    _timer.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final h = _now.hour.toString().padLeft(2, '0');
    final m = _now.minute.toString().padLeft(2, '0');
    return Padding(
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 0),
      child: Row(
        children: [
          Text('JARVIS', style: JarvisText.wordmark),
          const Spacer(),
          Text('CORE', style: JarvisText.screenTitle),
          const Spacer(),
          Text('$h:$m', style: JarvisText.screenTitle),
          const SizedBox(width: 8),
          GestureDetector(
            onTap: widget.onSettings,
            child: const Icon(Icons.settings_outlined,
                size: 18, color: JarvisColors.inkDim),
          ),
        ],
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  final String label;
  final String value;
  final Color? valueColor;
  const _Stat(
      {required this.label, required this.value, this.valueColor});

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          value,
          style: JarvisText.chip.copyWith(
            fontSize: 14,
            color: valueColor ?? JarvisColors.cyanSoft,
          ),
        ),
        const SizedBox(height: 2),
        Text(
          label,
          style: JarvisText.chip.copyWith(
              fontSize: 7,
              color: JarvisColors.inkFaint,
              letterSpacing: 0.14),
        ),
      ],
    );
  }
}

class _VDivider extends StatelessWidget {
  const _VDivider();
  @override
  Widget build(BuildContext context) => Container(
        width: 1,
        margin: const EdgeInsets.symmetric(vertical: 4),
        color: JarvisColors.lineDim,
      );
}

// ── Chat bubbles ──────────────────────────────────────────────────────────────

class _ChatBubble extends StatelessWidget {
  final TranscriptTurn turn;
  const _ChatBubble({required this.turn});

  @override
  Widget build(BuildContext context) {
    final isUser = turn.who == 'u';
    return Align(
      alignment:
          isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.82),
        child: isUser
            ? _UserBubble(text: turn.text)
            : _JarvisBubble(text: turn.text),
      ),
    );
  }
}

class _UserBubble extends StatelessWidget {
  final String text;
  const _UserBubble({required this.text});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding:
          const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: JarvisColors.cyan.withValues(alpha: 0.12),
        border: Border.all(color: JarvisColors.cyan, width: 1),
        borderRadius: BorderRadius.circular(14),
        boxShadow: [
          BoxShadow(
              color: JarvisColors.cyan.withValues(alpha: 0.12),
              blurRadius: 12)
        ],
      ),
      child: Text(text, style: JarvisText.chatBody),
    );
  }
}

class _JarvisBubble extends StatelessWidget {
  final String text;
  const _JarvisBubble({required this.text});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 6,
              height: 6,
              margin: const EdgeInsets.only(right: 6),
              decoration: const BoxDecoration(
                color: JarvisColors.cyanSoft,
                shape: BoxShape.circle,
                boxShadow: [
                  BoxShadow(color: JarvisColors.cyan, blurRadius: 4)
                ],
              ),
            ),
            Text(
              'J.A.R.V.I.S',
              style: JarvisText.chip.copyWith(
                fontSize: 8,
                color: JarvisColors.cyanSoft,
                letterSpacing: 8 * 0.24,
              ),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Container(
          padding:
              const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: Colors.black.withValues(alpha: 0.5),
            border:
                Border.all(color: JarvisColors.lineDim, width: 1),
            borderRadius: BorderRadius.circular(14),
          ),
          child: Text(
            text,
            style: JarvisText.chatBody
                .copyWith(color: JarvisColors.cyanSoft),
          ),
        ),
      ],
    );
  }
}

// ── Typing indicator ──────────────────────────────────────────────────────────

class _TypingIndicator extends StatefulWidget {
  final String? note;
  const _TypingIndicator({this.note});
  @override
  State<_TypingIndicator> createState() => _TypingIndicatorState();
}

class _TypingIndicatorState extends State<_TypingIndicator>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
        vsync: this,
        duration: const Duration(milliseconds: 1400))
      ..repeat();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: Colors.black.withValues(alpha: 0.5),
          border: Border.all(color: JarvisColors.lineDim),
          borderRadius: BorderRadius.circular(14),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              mainAxisSize: MainAxisSize.min,
              children: List.generate(
                3,
                (i) => AnimatedBuilder(
                  animation: _ctrl,
                  builder: (_, __) {
                    final phase =
                        (_ctrl.value * 1.4 - i * 0.16) % 1.0;
                    return Container(
                      width: 6,
                      height: 6,
                      margin: const EdgeInsets.only(right: 4),
                      decoration: BoxDecoration(
                        color: JarvisColors.cyan.withValues(alpha: 0.3 +
                            0.7 *
                                (phase < 0.5
                                    ? phase * 2
                                    : (1 - phase) * 2)),
                        shape: BoxShape.circle,
                      ),
                    );
                  },
                ),
              ),
            ),
            if (widget.note != null) ...[
              const SizedBox(height: 4),
              Text(widget.note!,
                  style: JarvisText.chip
                      .copyWith(color: JarvisColors.inkFaint)),
            ],
          ],
        ),
      ),
    );
  }
}

// ── Composer — Hold-to-speak ──────────────────────────────────────────────────

class _Composer extends StatefulWidget {
  final TextEditingController controller;
  final bool isComposing;
  final ValueChanged<String> onChanged;
  final ValueChanged<String> onSubmit;
  final VoidCallback onStopTts;
  final Future<void> Function(PlatformFile file, String query)? onFileUpload;

  const _Composer({
    required this.controller,
    required this.isComposing,
    required this.onChanged,
    required this.onSubmit,
    required this.onStopTts,
    this.onFileUpload,
  });

  @override
  State<_Composer> createState() => _ComposerState();
}

class _ComposerState extends State<_Composer> {
  final SpeechToText _stt = SpeechToText();
  bool _sttReady    = false;
  bool _isListening = false;
  PlatformFile? _pendingFile;

  @override
  void initState() {
    super.initState();
    _initStt();
  }

  Future<void> _initStt() async {
    _sttReady = await _stt.initialize(
      onStatus: (status) {
        if (!mounted) return;
        // STT engine durduysa (hold modunda biz zaten stop ettik, sorun değil)
        if (status == 'done' || status == 'notListening') {
          if (_isListening) setState(() => _isListening = false);
        }
      },
      onError: (_) {
        if (mounted) setState(() => _isListening = false);
      },
    );
    if (mounted) setState(() {});
  }

  Future<void> _pickFile() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['pdf', 'jpg', 'jpeg', 'png', 'webp', 'gif',
                          'xlsx', 'xls', 'csv', 'docx', 'doc', 'txt'],
      withData: false,
      withReadStream: false,
    );
    if (result == null || result.files.isEmpty) return;
    final file = result.files.first;
    if (!mounted) return;
    setState(() => _pendingFile = file);
  }

  Future<void> _sendWithFile() async {
    final file = _pendingFile;
    if (file == null) return;
    final query = widget.controller.text.trim();
    setState(() { _pendingFile = null; });
    widget.controller.clear();
    widget.onChanged('');
    await widget.onFileUpload?.call(file, query);
  }

  // STT yanlış anlamaları düzelt
  static String _correctStt(String text) {
    if (text.isEmpty) return text;
    var t = text.replaceAllMapped(
      RegExp(r'\b(servis|jarwis|cervis|gervis|harvis|garvis)\b',
          caseSensitive: false),
      (_) => 'JARVIS',
    );
    if (t.isNotEmpty) t = t[0].toUpperCase() + t.substring(1);
    return t;
  }

  // Parmak basıldığında — anında başla (no threshold delay)
  Future<void> _startListening() async {
    if (_isListening) return;
    if (!_sttReady) {
      await _initStt();
      if (!_sttReady) return;
    }
    widget.onStopTts();
    widget.controller.clear();
    widget.onChanged('');
    setState(() => _isListening = true);

    await _stt.listen(
      onResult: (result) {
        if (!mounted) return;
        final corrected = _correctStt(result.recognizedWords);
        widget.controller.text = corrected;
        widget.onChanged(corrected);
      },
      localeId: 'tr_TR',
      listenFor: const Duration(seconds: 60),
      pauseFor: const Duration(seconds: 30), // Hold modunda otomatik durdurma yok
      listenOptions: SpeechListenOptions(partialResults: true),
    );
  }

  // Parmak bırakıldığında — durdur ve gönder
  Future<void> _stopAndSend() async {
    if (!_isListening) return;
    setState(() => _isListening = false);
    await _stt.stop();
    // Son STT sonucunun gelmesi için kısa bekle
    await Future.delayed(const Duration(milliseconds: 350));
    final text = widget.controller.text.trim();
    if (text.isNotEmpty) widget.onSubmit(text);
  }

  // İptal et (parmak kayarsa)
  Future<void> _cancelListen() async {
    if (!_isListening) return;
    setState(() => _isListening = false);
    await _stt.cancel();
  }

  @override
  Widget build(BuildContext context) {
    final bottomPad = MediaQuery.of(context).padding.bottom;
    return Container(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.bottomCenter,
          end: Alignment.topCenter,
          colors: [
            Colors.black.withValues(alpha: 0.97),
            Colors.black.withValues(alpha: 0.65),
          ],
        ),
      ),
      padding: EdgeInsets.fromLTRB(18, 10, 18, bottomPad + 10),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // ── Pending file chip ─────────────────────────────────────
          if (_pendingFile != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(
                children: [
                  Expanded(
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 6),
                      decoration: BoxDecoration(
                        color: JarvisColors.cyan.withValues(alpha: 0.08),
                        border: Border.all(
                            color: JarvisColors.cyan.withValues(alpha: 0.4)),
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.attach_file_rounded,
                              size: 14, color: JarvisColors.cyanSoft),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              _pendingFile!.name,
                              style: JarvisText.chip.copyWith(
                                  color: JarvisColors.cyanSoft,
                                  fontSize: 11),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          GestureDetector(
                            onTap: () =>
                                setState(() => _pendingFile = null),
                            child: const Icon(Icons.close_rounded,
                                size: 14,
                                color: JarvisColors.inkDim),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),

          // ── Input row ─────────────────────────────────────────────
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              // 📎 Attach button
              GestureDetector(
                onTap: _pickFile,
                child: Container(
                  width: 40,
                  height: 40,
                  margin: const EdgeInsets.only(right: 8),
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: Colors.black.withValues(alpha: 0.5),
                    border: Border.all(
                      color: _pendingFile != null
                          ? JarvisColors.cyan
                          : JarvisColors.lineDim,
                    ),
                  ),
                  child: Icon(
                    Icons.attach_file_rounded,
                    size: 18,
                    color: _pendingFile != null
                        ? JarvisColors.cyan
                        : JarvisColors.inkDim,
                  ),
                ),
              ),

              // ── Text field ────────────────────────────────────────
              Expanded(
                child: Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 16, vertical: 8),
                  decoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.6),
                    border: Border.all(color: JarvisColors.lineDim),
                    borderRadius: BorderRadius.circular(22),
                  ),
                  child: TextField(
                    controller: widget.controller,
                    onChanged: widget.onChanged,
                    onSubmitted: widget.onSubmit,
                    style: JarvisText.chatBody.copyWith(fontSize: 13),
                    maxLines: 4,
                    minLines: 1,
                    decoration: InputDecoration.collapsed(
                      hintText: _isListening
                          ? 'Dinleniyor… bırakınca gönderilir'
                          : _pendingFile != null
                              ? 'Dosya hakkında bir soru sor…'
                              : 'Yaz veya mic basılı tut…',
                      hintStyle: JarvisText.chip.copyWith(
                          color: JarvisColors.inkFaint, fontSize: 11),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 10),

              // ── Send / Mic button ─────────────────────────────────
              if (_pendingFile != null)
                // Dosya seçili → gönder
                GestureDetector(
                  onTap: _sendWithFile,
                  child: _CircleBtn(
                    icon: Icons.send_rounded,
                    color: JarvisColors.cyan,
                    glowColor: JarvisColors.cyan,
                    isActive: true,
                  ),
                )
              else if (widget.isComposing)
                // Metin varsa → gönder
                GestureDetector(
                  onTap: () => widget.onSubmit(widget.controller.text),
                  child: _CircleBtn(
                    icon: Icons.send_rounded,
                    color: JarvisColors.cyan,
                    glowColor: JarvisColors.cyan,
                    isActive: true,
                  ),
                )
              else
                // Boşken → hold-to-speak mic
                Listener(
                  onPointerDown: (_) => _startListening(),
                  onPointerUp: (_) => _stopAndSend(),
                  onPointerCancel: (_) => _cancelListen(),
                  child: _CircleBtn(
                    icon: _isListening
                        ? Icons.stop_rounded
                        : Icons.mic_rounded,
                    color: _isListening
                        ? JarvisColors.red
                        : JarvisColors.cyanSoft,
                    glowColor: _isListening
                        ? JarvisColors.red
                        : JarvisColors.cyan,
                    isActive: _isListening,
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _CircleBtn extends StatelessWidget {
  final IconData icon;
  final Color color;
  final Color glowColor;
  final bool isActive;

  const _CircleBtn({
    required this.icon,
    required this.color,
    required this.glowColor,
    required this.isActive,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 48,
      height: 48,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: RadialGradient(
          colors: [
            glowColor.withValues(alpha: isActive ? 0.35 : 0.18),
            Colors.black,
          ],
          radius: 0.85,
        ),
        border: Border.all(
          color: glowColor,
          width: isActive ? 2 : 1,
        ),
        boxShadow: [
          BoxShadow(
            color: glowColor.withValues(alpha: isActive ? 0.55 : 0.3),
            blurRadius: isActive ? 18 : 10,
          ),
        ],
      ),
      child: Icon(icon, size: 22, color: color),
    );
  }
}
