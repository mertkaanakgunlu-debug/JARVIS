import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../widgets/grid_background.dart';
import '../models/transcript_turn.dart';
import '../providers/transcript_provider.dart';
import '../providers/api_provider.dart';
import '../providers/settings_provider.dart';
import '../providers/tasks_provider.dart';
import '../core/wake_service.dart';

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _textCtrl = TextEditingController();
  final _scrollCtrl = ScrollController();
  bool _isComposing = false;
  bool _isSending = false;
  String? _loadingNote;

  @override
  void dispose() {
    _textCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  Future<void> _send(String query) async {
    if (query.trim().isEmpty || _isSending) return;
    setState(() { _isSending = true; _loadingNote = null; });

    ref.read(transcriptProvider.notifier).add(
      TranscriptTurn(who: 'u', text: query),
    );
    _textCtrl.clear();
    _scrollToBottom();

    final api = ref.read(apiClientProvider);
    if (api == null) {
      setState(() => _isSending = false);
      return;
    }

    final settings = ref.read(settingsSyncProvider);

    // Auto-wake if needed
    if (settings.autoWake) {
      final wake = WakeService(
        api: api,
        pcMac: settings.pcMac,
      );
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
      // Add empty JARVIS turn to stream into
      ref.read(transcriptProvider.notifier).add(
        const TranscriptTurn(who: 'j', text: ''),
      );

      final buf = StringBuffer();
      bool isAsync = false;
      String? taskId;

      await for (final chunk in api.chatStream(query, language: settings.language)) {
        if (chunk == '[DONE]') break;
        if (chunk.startsWith('[ERROR]')) {
          ref.read(transcriptProvider.notifier).appendToLast(
            chunk.substring(7),
          );
          break;
        }

        // Check for async response JSON
        if (chunk.startsWith('{"async":')) {
          try {
            final j = _parseJson(chunk);
            if (j['async'] == true) {
              isAsync = true;
              taskId = j['task_id'] as String?;
              break;
            }
          } catch (_) {}
        }

        buf.write(chunk.replaceAll(r'\n', '\n'));
        ref.read(transcriptProvider.notifier).appendToLast(
          chunk.replaceAll(r'\n', '\n'),
        );
        _scrollToBottom();
      }

      if (isAsync && taskId != null) {
        // Replace last turn with async task card
        ref.read(transcriptProvider.notifier).appendToLast(
          '🕐 Arka planda çalışıyor — task `$taskId`',
        );
        ref.read(tasksProvider.notifier).refresh();
      }
    } catch (e) {
      ref.read(transcriptProvider.notifier).appendToLast('Bağlantı hatası: $e');
    } finally {
      setState(() { _isSending = false; _loadingNote = null; });
      _scrollToBottom();
    }
  }

  Map<String, dynamic> _parseJson(String s) =>
      jsonDecode(s) as Map<String, dynamic>;

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

  @override
  Widget build(BuildContext context) {
    final transcript = ref.watch(transcriptProvider);
    final isSending = _isSending;

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
                  child: Text('// Today · session',
                      style: JarvisText.sectionHeader),
                ),
                Expanded(
                  child: ListView.builder(
                    controller: _scrollCtrl,
                    padding: const EdgeInsets.fromLTRB(18, 0, 18, 120),
                    itemCount: transcript.length + (isSending ? 1 : 0),
                    itemBuilder: (context, index) {
                      if (index == transcript.length && isSending) {
                        return _TypingIndicator(note: _loadingNote);
                      }
                      final turn = transcript[index];
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: _ChatBubble(turn: turn),
                      );
                    },
                  ),
                ),
              ],
            ),
            // Composer
            Positioned(
              bottom: 0, left: 0, right: 0,
              child: _Composer(
                controller: _textCtrl,
                isComposing: _isComposing,
                onChanged: (v) => setState(() => _isComposing = v.isNotEmpty),
                onSubmit: (v) => _send(v),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ChatBubble extends StatelessWidget {
  final TranscriptTurn turn;
  const _ChatBubble({required this.turn});

  @override
  Widget build(BuildContext context) {
    final isUser = turn.who == 'u';
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.82,
        ),
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
        color: JarvisColors.cyan.withOpacity(0.12),
        border: Border.all(color: JarvisColors.cyan, width: 1),
        borderRadius: BorderRadius.circular(14),
        boxShadow: [BoxShadow(color: JarvisColors.cyan.withOpacity(0.12), blurRadius: 12)],
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
                  fontSize: 8,
                  color: JarvisColors.cyanSoft,
                  letterSpacing: 8 * 0.24,
                )),
          ],
        ),
        const SizedBox(height: 4),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: Colors.black.withOpacity(0.5),
            border: Border.all(color: JarvisColors.lineDim, width: 1),
            borderRadius: BorderRadius.circular(14),
          ),
          child: Text(text,
              style: JarvisText.chatBody.copyWith(color: JarvisColors.cyanSoft)),
        ),
      ],
    );
  }
}

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
          color: Colors.black.withOpacity(0.5),
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
                      color: JarvisColors.cyan.withOpacity(0.3 + 0.7 * (phase < 0.5 ? phase * 2 : (1 - phase) * 2)),
                      shape: BoxShape.circle,
                    ),
                  );
                },
              )),
            ),
            if (widget.note != null) ...[
              const SizedBox(height: 4),
              Text(widget.note!, style: JarvisText.chip.copyWith(
                  color: JarvisColors.inkFaint)),
            ],
          ],
        ),
      ),
    );
  }
}

class _Composer extends StatelessWidget {
  final TextEditingController controller;
  final bool isComposing;
  final ValueChanged<String> onChanged;
  final ValueChanged<String> onSubmit;

  const _Composer({
    required this.controller,
    required this.isComposing,
    required this.onChanged,
    required this.onSubmit,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 12),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.bottomCenter,
          end: Alignment.topCenter,
          colors: [Colors.black.withOpacity(0.95), Colors.transparent],
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
                  color: Colors.black.withOpacity(0.6),
                  border: Border.all(color: JarvisColors.lineDim),
                  borderRadius: BorderRadius.circular(22),
                ),
                child: TextField(
                  controller: controller,
                  onChanged: onChanged,
                  onSubmitted: onSubmit,
                  style: JarvisText.chatBody,
                  maxLines: 1,
                  textInputAction: TextInputAction.send,
                  decoration: InputDecoration(
                    hintText: 'Type or hold to speak…',
                    hintStyle: JarvisText.chatBody.copyWith(
                      color: JarvisColors.inkFaint),
                    border: InputBorder.none,
                    isDense: true,
                    contentPadding: EdgeInsets.zero,
                  ),
                ),
              ),
            ),
            const SizedBox(width: 8),
            // Send / mic button
            GestureDetector(
              onTap: () => onSubmit(controller.text),
              child: Container(
                width: 48, height: 48,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: RadialGradient(
                    colors: [
                      JarvisColors.cyan.withOpacity(0.3),
                      JarvisColors.cyan.withOpacity(0.08),
                    ],
                  ),
                  border: Border.all(color: JarvisColors.cyan, width: 1),
                  boxShadow: [BoxShadow(
                      color: JarvisColors.cyan.withOpacity(0.3), blurRadius: 12)],
                ),
                child: const Icon(Icons.mic, color: JarvisColors.cyanSoft, size: 20),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
