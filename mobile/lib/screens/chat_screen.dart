import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:speech_to_text/speech_to_text.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../widgets/grid_background.dart';
import '../core/chat_sse.dart';
import '../models/pending_confirmation.dart';
import '../models/transcript_turn.dart';
import '../providers/confirmation_provider.dart';
import '../providers/transcript_provider.dart';
import '../providers/api_provider.dart';
import '../providers/settings_provider.dart';
import '../providers/tasks_provider.dart';
import '../core/wake_service.dart';

/// User-facing text for a ChatProgress event -- never sourced from the model,
/// per jarvis/voice/session.py's describe_progress() (same wording, TR side).
String _progressNote(ChatProgress event) {
  if (event.kind == 'chart') return 'Grafik hazırlanıyor…';
  return 'İstenen çıktı hazırlanıyor…';
}

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
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
  }

  Future<void> _initTts() async {
    await _tts.setLanguage('tr-TR');
    await _tts.setSpeechRate(0.48);
    await _tts.setVolume(1.0);
    await _tts.setPitch(0.85); // biraz daha derin ses

    // Android'de mevcut Türkçe erkek sesini bul
    try {
      final voices = await _tts.getVoices as List?;
      if (voices != null) {
        // tr locale'li erkek ses ara
        Map? maleVoice = voices.cast<Map>().firstWhere(
          (v) => (v['locale'] as String? ?? '').startsWith('tr') &&
                 (v['name'] as String? ?? '').toLowerCase().contains('male'),
          orElse: () => <String, dynamic>{},
        );
        // Erkek yoksa herhangi Türkçe ses
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

  // ── TTS helpers ─────────────────────────────────────────────────────────────

  void _speakChunk(String chunk) {
    final settings = ref.read(settingsSyncProvider);
    if (!settings.ttsEnabled) return;
    _ttsBuffer.write(chunk);
    final text = _ttsBuffer.toString();
    final idx = _sentenceEnd(text);
    if (idx >= 0) {
      final sentence = text.substring(0, idx + 1).trim();
      _ttsBuffer.clear();
      if (idx + 1 < text.length) _ttsBuffer.write(text.substring(idx + 1));
      if (sentence.isNotEmpty) _tts.speak(sentence);
    }
  }

  void _flushTts() {
    final settings = ref.read(settingsSyncProvider);
    if (!settings.ttsEnabled) { _ttsBuffer.clear(); return; }
    final remaining = _ttsBuffer.toString().trim();
    _ttsBuffer.clear();
    if (remaining.isNotEmpty) _tts.speak(remaining);
  }

  static int _sentenceEnd(String text) {
    for (int i = 0; i < text.length; i++) {
      final c = text[i];
      if (c == '.' || c == '!' || c == '?') {
        if (i + 1 >= text.length || text[i + 1] == ' ' || text[i + 1] == '\n') {
          return i;
        }
      }
    }
    return -1;
  }

  // ── Send ─────────────────────────────────────────────────────────────────────

  Future<void> _send(String query) async {
    query = query.trim();
    if (query.isEmpty || _isSending) return;
    setState(() { _isSending = true; _loadingNote = null; });
    _tts.stop();
    _ttsBuffer.clear();

    ref.read(transcriptProvider.notifier).add(TranscriptTurn(who: 'u', text: query));
    _textCtrl.clear();
    setState(() => _isComposing = false);
    _scrollToBottom();

    final api = ref.read(apiClientProvider);
    if (api == null) {
      setState(() => _isSending = false);
      return;
    }

    final settings = ref.read(settingsSyncProvider);

    // Auto-wake PC if needed
    if (settings.autoWake) {
      final wake = WakeService(api: api, pcMac: settings.pcMac);
      final online = await wake.ensureAwake(
        onStatus: (msg) => setState(() => _loadingNote = msg),
      );
      if (!online) {
        ref.read(transcriptProvider.notifier).add(
          const TranscriptTurn(who: 'j', text: 'PC erişilemiyor. Manuel kontrol edin.'),
        );
        setState(() { _isSending = false; _loadingNote = null; });
        return;
      }
    }
    setState(() => _loadingNote = null);

    try {
      await _consume(api.chatStream(query, language: settings.language));
    } catch (e) {
      // add(), not appendToLast(): _consume() has already dropped the empty
      // bubble it opened, so there is no JARVIS turn left to append to and
      // appendToLast would silently drop the message on the user's own turn.
      ref.read(transcriptProvider.notifier).add(
        TranscriptTurn(who: 'j', text: 'Bağlantı hatası: $e'),
      );
    } finally {
      if (mounted) setState(() { _isSending = false; _loadingNote = null; });
      _scrollToBottom();
    }
  }

  // ── One SSE stream, one reader ───────────────────────────────────────────────

  /// Drive one SSE stream into the transcript.
  ///
  /// Shared by a new turn (/chat/stream) and a confirmation continuation
  /// (/chat/confirm/{id}) because they are the same stream: the server wraps
  /// both through jarvis/api.py's _sse_frames(), so a resumed turn can carry
  /// tokens, a final_answer, a progress marker -- and a SECOND
  /// confirmation_required. Reading the continuation with a narrower loop is
  /// exactly the omission that docstring records on the server side.
  ///
  /// Opens the empty JARVIS bubble itself, and removes it again if the stream
  /// produced no text at all (the L3 case: the graph interrupts for approval
  /// without saying anything).
  Future<void> _consume(Stream<String> chunks) async {
    final transcript = ref.read(transcriptProvider.notifier);
    transcript.add(const TranscriptTurn(who: 'j', text: ''));
    try {
      // Labeled so the terminal cases below can end the WHOLE stream from
      // inside the switch -- a bare `break` in a Dart switch statement only
      // exits the switch itself, not an enclosing loop.
      chunkLoop:
      await for (final chunk in chunks) {
        final event = classifyChatChunk(chunk);
        switch (event) {
          case ChatDone():
            break chunkLoop;
          case ChatError():
            transcript.appendToLast(event.message);
            break chunkLoop;
          case ChatAsyncTask():
            transcript.appendToLast(
              '🕐 Arka planda çalışıyor — task `${event.taskId ?? '?'}`',
            );
            ref.read(tasksProvider.notifier).refresh();
            break chunkLoop;
          case ChatConfirmationRequired():
            // An L3 tool call needs the user. The graph is now interrupted
            // server-side and this stream ends here; the prompt goes to the
            // app-wide provider (not local state) so it survives the stream
            // that delivered it -- see confirmation_provider.dart.
            final pending =
                PendingConfirmation.fromPayload(event.id, event.payload);
            if (pending != null) {
              ref.read(confirmationProvider.notifier).raise(pending);
            } else {
              // Unanswerable prompt (no id, or no tool we can name). Say so
              // plainly rather than rendering an approve button over a blank
              // description -- and never fall back to dumping the payload.
              transcript.appendToLast(
                '⚠️ Onay gerekiyor ama istek okunamadı. PC üzerinden yanıtlayın.',
              );
            }
            break chunkLoop;
          case ChatProgress():
            if (mounted) setState(() => _loadingNote = _progressNote(event));
            break;
          case ChatFinalAnswer():
            transcript.replaceLast(event.text);
            if (_loadingNote != null && mounted) {
              setState(() => _loadingNote = null);
            }
            // Not re-spoken: any prefix that already streamed was already
            // read aloud via _speakChunk as it arrived (a buffered turn
            // instead arrives here as a plain ChatToken, handled below, and
            // IS spoken -- see agent.chat_stream()'s buffered branch).
            _scrollToBottom();
            break;
          case ChatToken():
            if (_loadingNote != null && mounted) {
              setState(() => _loadingNote = null);
            }
            transcript.appendToLast(event.text);
            _speakChunk(event.text);
            _scrollToBottom();
            break;
        }
      }
      _flushTts();
    } finally {
      transcript.removeLastIfEmpty();
    }
  }

  // ── L3 confirmation ─────────────────────────────────────────────────────────

  /// Answer the pending confirmation and stream the graph's continuation back
  /// into the transcript.
  ///
  /// [decision] is the server's vocabulary ("approve" / "deny"), passed
  /// through untranslated -- confirmation_node validates it fail-closed.
  Future<void> _resolveConfirmation(String decision) async {
    final notifier = ref.read(confirmationProvider.notifier);
    final pending = ref.read(confirmationProvider).pending;
    if (pending == null) return;
    // The double-submit / stale-tap gate. Owned by the notifier, not by the
    // buttons' `onTap: null`, because both legs feed this prompt and a POST
    // that already claimed the interrupt server-side cannot be repeated.
    if (!notifier.beginSubmit(pending.id)) return;

    final api = ref.read(apiClientProvider);
    if (api == null) { notifier.failed(); return; }

    setState(() { _isSending = true; _loadingNote = null; });
    _tts.stop();
    _ttsBuffer.clear();
    _scrollToBottom();

    try {
      await _consume(api.confirmStream(pending.id, decision));
      // Clears the card -- unless the continuation raised a SECOND interrupt,
      // which resolved() leaves alone because it is id-checked. A server that
      // answers "expired or not found" lands here too, and correctly: that
      // confirmation is dead, so the card must go.
      notifier.resolved(pending.id);
    } catch (e) {
      // Transport failure, not a verdict. The interrupt may still be alive
      // server-side until its TTL, so the card STAYS and the user can retry
      // or deny.
      ref.read(transcriptProvider.notifier).add(
        TranscriptTurn(who: 'j', text: 'Onay iletilemedi: $e'),
      );
      notifier.failed();
    } finally {
      if (mounted) setState(() { _isSending = false; _loadingNote = null; });
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

  // ── Build ────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final transcript = ref.watch(transcriptProvider);
    final confirmation = ref.watch(confirmationProvider);

    return GridBackground(
      child: SafeArea(
        child: Stack(
          children: [
            Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(18, 12, 18, 0),
                  child: Text('COMMS', style: JarvisText.wordmark),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(18, 4, 18, 8),
                  child: Text('// Today · session', style: JarvisText.sectionHeader),
                ),
                Expanded(
                  child: ListView.builder(
                    controller: _scrollCtrl,
                    padding: const EdgeInsets.fromLTRB(18, 0, 18, 120),
                    itemCount: transcript.length + (_isSending ? 1 : 0),
                    itemBuilder: (context, index) {
                      if (index == transcript.length && _isSending) {
                        return _TypingIndicator(note: _loadingNote);
                      }
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: _ChatBubble(turn: transcript[index]),
                      );
                    },
                  ),
                ),
              ],
            ),
            Positioned(
              bottom: 0, left: 0, right: 0,
              // The approval card REPLACES the composer while an interrupt is
              // open. Structural, not cosmetic: it is what stops a new turn
              // being started on top of a graph that is still paused waiting
              // for this answer, without needing a disabled-state flag
              // threaded through the composer.
              child: confirmation.pending != null
                  ? _ConfirmationCard(
                      confirmation: confirmation.pending!,
                      submitting: confirmation.submitting,
                      onDecision: _resolveConfirmation,
                    )
                  : _Composer(
                      controller: _textCtrl,
                      isComposing: _isComposing,
                      onChanged: (v) => setState(() => _isComposing = v.isNotEmpty),
                      onSubmit: _send,
                      onStopTts: () { _tts.stop(); _ttsBuffer.clear(); },
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Chat bubbles ─────────────────────────────────────────────────────────────

class _ChatBubble extends StatelessWidget {
  final TranscriptTurn turn;
  const _ChatBubble({required this.turn});

  @override
  Widget build(BuildContext context) {
    final isUser = turn.who == 'u';
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.82),
        child: isUser ? _UserBubble(text: turn.text) : _JarvisBubble(text: turn.text),
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
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: JarvisColors.cyan.withValues(alpha: 0.12),
        border: Border.all(color: JarvisColors.cyan, width: 1),
        borderRadius: BorderRadius.circular(14),
        boxShadow: [BoxShadow(color: JarvisColors.cyan.withValues(alpha: 0.12), blurRadius: 12)],
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
              width: 6, height: 6,
              margin: const EdgeInsets.only(right: 6),
              decoration: const BoxDecoration(
                color: JarvisColors.cyanSoft,
                shape: BoxShape.circle,
                boxShadow: [BoxShadow(color: JarvisColors.cyan, blurRadius: 4)],
              ),
            ),
            Text('J.A.R.V.I.S',
                style: JarvisText.chip.copyWith(
                  fontSize: 8, color: JarvisColors.cyanSoft, letterSpacing: 8 * 0.24)),
          ],
        ),
        const SizedBox(height: 4),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: Colors.black.withValues(alpha: 0.5),
            border: Border.all(color: JarvisColors.lineDim, width: 1),
            borderRadius: BorderRadius.circular(14),
          ),
          child: Text(text, style: JarvisText.chatBody.copyWith(color: JarvisColors.cyanSoft)),
        ),
      ],
    );
  }
}

// ── L3 confirmation card ─────────────────────────────────────────────────────

/// The approve/deny prompt. Shows the server's own plain-language
/// description of each pending call (PendingConfirmation's `label`) -- never
/// the raw payload, never tool arguments.
class _ConfirmationCard extends StatelessWidget {
  final PendingConfirmation confirmation;
  final bool submitting;
  final ValueChanged<String> onDecision;

  const _ConfirmationCard({
    required this.confirmation,
    required this.submitting,
    required this.onDecision,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 12),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.bottomCenter,
          end: Alignment.topCenter,
          colors: [Colors.black.withValues(alpha: 0.95), Colors.transparent],
        ),
      ),
      child: SafeArea(
        top: false,
        child: Container(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
          decoration: BoxDecoration(
            color: Colors.black.withValues(alpha: 0.72),
            border: Border.all(color: JarvisColors.amber, width: 1),
            borderRadius: BorderRadius.circular(14),
            boxShadow: [
              BoxShadow(color: JarvisColors.amber.withValues(alpha: 0.18), blurRadius: 14),
            ],
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(Icons.shield_outlined,
                      size: 14, color: JarvisColors.amber),
                  const SizedBox(width: 6),
                  Text('ONAY GEREKİYOR',
                      style: JarvisText.chip.copyWith(
                        color: JarvisColors.amber,
                        letterSpacing: 8 * 0.24,
                      )),
                ],
              ),
              const SizedBox(height: 8),
              for (final tool in confirmation.tools)
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text('• ${tool.label}',
                      style: JarvisText.chatBody
                          .copyWith(color: JarvisColors.cyanSoft)),
                ),
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: _DecisionButton(
                      label: submitting ? 'GÖNDERİLİYOR…' : 'ONAYLA',
                      color: JarvisColors.cyan,
                      // Null disables the tap. The real guard is
                      // ConfirmationNotifier.beginSubmit(); this only keeps
                      // the button from looking live while a POST is open.
                      onTap: submitting ? null : () => onDecision('approve'),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: _DecisionButton(
                      label: 'REDDET',
                      color: JarvisColors.red,
                      onTap: submitting ? null : () => onDecision('deny'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _DecisionButton extends StatelessWidget {
  final String label;
  final Color color;
  final VoidCallback? onTap;

  const _DecisionButton({required this.label, required this.color, this.onTap});

  @override
  Widget build(BuildContext context) {
    final enabled = onTap != null;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 12),
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: color.withValues(alpha: enabled ? 0.16 : 0.05),
          border: Border.all(color: color.withValues(alpha: enabled ? 1 : 0.35)),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Text(label,
            style: JarvisText.chip.copyWith(
              color: color.withValues(alpha: enabled ? 1 : 0.45),
            )),
      ),
    );
  }
}

// ── Typing indicator ─────────────────────────────────────────────────────────

class _TypingIndicator extends StatefulWidget {
  final String? note;
  const _TypingIndicator({this.note});
  @override State<_TypingIndicator> createState() => _TypingIndicatorState();
}

class _TypingIndicatorState extends State<_TypingIndicator>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(vsync: this,
        duration: const Duration(milliseconds: 1400))..repeat();
  }

  @override void dispose() { _ctrl.dispose(); super.dispose(); }

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
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
              children: List.generate(3, (i) => AnimatedBuilder(
                animation: _ctrl,
                builder: (_, __) {
                  final phase = (_ctrl.value * 1.4 - i * 0.16) % 1.0;
                  return Container(
                    width: 6, height: 6,
                    margin: const EdgeInsets.only(right: 4),
                    decoration: BoxDecoration(
                      color: JarvisColors.cyan.withValues(
                          alpha: 0.3 +
                              0.7 * (phase < 0.5 ? phase * 2 : (1 - phase) * 2)),
                      shape: BoxShape.circle,
                    ),
                  );
                },
              )),
            ),
            if (widget.note != null) ...[
              const SizedBox(height: 4),
              Text(widget.note!,
                  style: JarvisText.chip.copyWith(color: JarvisColors.inkFaint)),
            ],
          ],
        ),
      ),
    );
  }
}

// ── Composer (STT entegre) ───────────────────────────────────────────────────

class _Composer extends StatefulWidget {
  final TextEditingController controller;
  final bool isComposing;
  final ValueChanged<String> onChanged;
  final ValueChanged<String> onSubmit;
  final VoidCallback onStopTts;

  const _Composer({
    required this.controller,
    required this.isComposing,
    required this.onChanged,
    required this.onSubmit,
    required this.onStopTts,
  });

  @override
  State<_Composer> createState() => _ComposerState();
}

class _ComposerState extends State<_Composer> {
  final SpeechToText _stt = SpeechToText();
  bool _sttReady    = false;
  bool _isListening = false;

  @override
  void initState() {
    super.initState();
    _initStt();
  }

  Future<void> _initStt() async {
    _sttReady = await _stt.initialize(
      onStatus: (status) {
        if (!mounted) return;
        if (status == 'done' || status == 'notListening') {
          setState(() => _isListening = false);
          final text = widget.controller.text.trim();
          if (text.isNotEmpty) widget.onSubmit(text);
        }
      },
      onError: (_) {
        if (mounted) setState(() => _isListening = false);
      },
    );
    if (mounted) setState(() {});
  }

  // Yaygın Türkçe STT yanlış tanıma düzeltmeleri
  static String _correctStt(String text) {
    if (text.isEmpty) return text;
    var t = text;
    // "Jarvis" → STT genellikle "servis", "jarwis", "cervis" tanıyor
    t = t.replaceAllMapped(
      RegExp(r'\b(servis|jarwis|cervis|gervis|harvis|garvis)\b',
          caseSensitive: false),
      (_) => 'JARVIS',
    );
    // Cümle başındaki büyük harf
    if (t.isNotEmpty) t = t[0].toUpperCase() + t.substring(1);
    return t;
  }

  Future<void> _toggleListen() async {
    if (_isListening) {
      await _stt.stop();
      setState(() => _isListening = false);
      final text = widget.controller.text.trim();
      if (text.isNotEmpty) widget.onSubmit(text);
      return;
    }
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
      localeId: 'tr_TR',   // alt çizgi: bazı Android sürümlerinde zorunlu
      listenFor: const Duration(seconds: 45),
      pauseFor: const Duration(seconds: 2), // 3→2: daha hızlı sonuç
      listenOptions: SpeechListenOptions(partialResults: true),
    );
  }

  @override
  void dispose() {
    _stt.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final listening = _isListening;
    final micColor  = listening ? JarvisColors.red : JarvisColors.cyan;

    return Container(
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 12),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.bottomCenter,
          end: Alignment.topCenter,
          colors: [Colors.black.withValues(alpha: 0.95), Colors.transparent],
        ),
      ),
      child: SafeArea(
        top: false,
        child: Row(
          children: [
            Expanded(
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                decoration: BoxDecoration(
                  color: Colors.black.withValues(alpha: 0.6),
                  border: Border.all(
                    color: listening ? JarvisColors.red.withValues(alpha: 0.6) : JarvisColors.lineDim),
                  borderRadius: BorderRadius.circular(22),
                ),
                child: TextField(
                  controller: widget.controller,
                  onChanged: widget.onChanged,
                  onSubmitted: widget.onSubmit,
                  style: JarvisText.chatBody,
                  maxLines: 1,
                  textInputAction: TextInputAction.send,
                  decoration: InputDecoration(
                    hintText: listening
                        ? 'Dinliyorum…'
                        : 'Type or hold to speak…',
                    hintStyle: JarvisText.chatBody.copyWith(
                      color: listening
                          ? JarvisColors.red.withValues(alpha: 0.7)
                          : JarvisColors.inkFaint,
                    ),
                    border: InputBorder.none,
                    isDense: true,
                    contentPadding: EdgeInsets.zero,
                  ),
                ),
              ),
            ),
            const SizedBox(width: 8),
            // Send button (when composing) / Mic button (when idle)
            widget.isComposing && !listening
                ? GestureDetector(
                    onTap: () => widget.onSubmit(widget.controller.text),
                    child: Container(
                      width: 48, height: 48,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: JarvisColors.cyan.withValues(alpha: 0.2),
                        border: Border.all(color: JarvisColors.cyan),
                        boxShadow: [BoxShadow(
                            color: JarvisColors.cyan.withValues(alpha: 0.3), blurRadius: 12)],
                      ),
                      child: const Icon(Icons.send_rounded,
                          color: JarvisColors.cyanSoft, size: 20),
                    ),
                  )
                : GestureDetector(
                    onTap: _toggleListen,
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 200),
                      width: 48, height: 48,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        gradient: RadialGradient(colors: [
                          micColor.withValues(alpha: 0.35),
                          micColor.withValues(alpha: 0.08),
                        ]),
                        border: Border.all(color: micColor, width: listening ? 2 : 1),
                        boxShadow: [BoxShadow(
                            color: micColor.withValues(alpha: listening ? 0.5 : 0.3),
                            blurRadius: listening ? 18 : 12)],
                      ),
                      child: Icon(
                        listening ? Icons.stop_rounded : Icons.mic,
                        color: listening ? JarvisColors.red : JarvisColors.cyanSoft,
                        size: 20,
                      ),
                    ),
                  ),
          ],
        ),
      ),
    );
  }
}
