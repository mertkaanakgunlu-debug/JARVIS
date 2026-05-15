import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../models/conversation_state.dart';
import '../theme/state_accent.dart';

class VoiceBars extends StatefulWidget {
  final int barCount;
  final ConversationState state;
  final double height;

  const VoiceBars({
    super.key,
    this.barCount = 28,
    this.state = ConversationState.idle,
    this.height = 48,
  });

  @override
  State<VoiceBars> createState() => _VoiceBarsState();
}

class _VoiceBarsState extends State<VoiceBars>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 800),
    )..repeat();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final accent = accentForState(widget.state);
    return AnimatedBuilder(
      animation: _ctrl,
      builder: (_, __) => CustomPaint(
        size: Size(widget.barCount * 6.0, widget.height),
        painter: _BarsPainter(
          t: _ctrl.value,
          state: widget.state,
          accent: accent,
          count: widget.barCount,
          maxH: widget.height,
        ),
      ),
    );
  }
}

class _BarsPainter extends CustomPainter {
  final double t;
  final ConversationState state;
  final Color accent;
  final int count;
  final double maxH;

  const _BarsPainter({
    required this.t,
    required this.state,
    required this.accent,
    required this.count,
    required this.maxH,
  });

  double _barHeight(int i) {
    final phase = t * math.pi * 2;
    switch (state) {
      case ConversationState.idle:
        return maxH * (0.08 + 0.05 * math.sin(phase * 0.3 + i * 0.4));
      case ConversationState.listening:
        return maxH * (0.1 + 0.6 * math.abs(math.sin(phase * 1.5 + i * 0.5)));
      case ConversationState.thinking:
        return maxH * (0.15 + 0.4 * math.sin(phase * 1.2 + i * 0.35).abs());
      case ConversationState.working:
        return maxH * (0.2 + 0.55 * ((math.sin(phase * 2 + i * 0.3) + 1) / 2));
      case ConversationState.speaking:
        final envelope = math.sin(phase * 3 + i * 0.25);
        return maxH * (0.25 + 0.65 * envelope.abs());
    }
  }

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = accent
      ..style = PaintingStyle.fill;
    final glowPaint = Paint()
      ..color = accent.withOpacity(0.35)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3)
      ..style = PaintingStyle.fill;

    const barW = 2.0;
    const gap = 4.0;
    for (int i = 0; i < count; i++) {
      final bh = _barHeight(i).clamp(2.0, size.height);
      final x = i * (barW + gap);
      final y = (size.height - bh) / 2;
      final rect = RRect.fromRectAndRadius(
        Rect.fromLTWH(x, y, barW, bh),
        const Radius.circular(1),
      );
      canvas.drawRRect(rect, glowPaint);
      canvas.drawRRect(rect, paint);
    }
  }

  @override
  bool shouldRepaint(_BarsPainter old) => old.t != t || old.state != state;
}
