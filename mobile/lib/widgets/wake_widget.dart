import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../models/conversation_state.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import 'jarvis_orb.dart';

enum WakeMode { listening, thinking, speaking }

class WakeWidget extends StatefulWidget {
  final WakeMode mode;
  final String? heard;
  final String? response;
  final VoidCallback? onTap;

  const WakeWidget({
    super.key,
    required this.mode,
    this.heard,
    this.response,
    this.onTap,
  });

  @override
  State<WakeWidget> createState() => _WakeWidgetState();
}

class _WakeWidgetState extends State<WakeWidget>
    with TickerProviderStateMixin {
  late AnimationController _enterCtrl;
  late Animation<double> _fadeIn;
  late Animation<double> _slideIn;
  late AnimationController _blinkCtrl;
  late AnimationController _spinCtrl;

  @override
  void initState() {
    super.initState();
    _enterCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 400),
    )..forward();
    _fadeIn = CurvedAnimation(parent: _enterCtrl, curve: Curves.easeOut);
    _slideIn = Tween<double>(begin: 16, end: 0).animate(
      CurvedAnimation(parent: _enterCtrl, curve: const Cubic(0.2, 0.7, 0.2, 1.15)),
    );

    _blinkCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1000),
    )..repeat(reverse: true);

    _spinCtrl = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 9),
    )..repeat();
  }

  @override
  void dispose() {
    _enterCtrl.dispose();
    _blinkCtrl.dispose();
    _spinCtrl.dispose();
    super.dispose();
  }

  ConversationState get _orbState {
    switch (widget.mode) {
      case WakeMode.listening: return ConversationState.listening;
      case WakeMode.thinking:  return ConversationState.thinking;
      case WakeMode.speaking:  return ConversationState.speaking;
    }
  }

  String get _stateLabel {
    switch (widget.mode) {
      case WakeMode.listening: return 'LISTENING';
      case WakeMode.thinking:  return 'THINKING';
      case WakeMode.speaking:  return 'RESPONDING';
    }
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _enterCtrl,
      builder: (_, child) => Transform.translate(
        offset: Offset(0, _slideIn.value),
        child: Opacity(opacity: _fadeIn.value, child: child),
      ),
      child: GestureDetector(
        onTap: widget.onTap,
        behavior: HitTestBehavior.opaque,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Orb halo
            _OrbHalo(
              size: 110,
              orbState: _orbState,
              spinCtrl: _spinCtrl,
            ),
            const SizedBox(height: 10),
            // Caption
            _caption(),
            const SizedBox(height: 6),
            // State label blinking
            AnimatedBuilder(
              animation: _blinkCtrl,
              builder: (_, __) => Opacity(
                opacity: 0.5 + 0.5 * _blinkCtrl.value,
                child: Text(
                  _stateLabel,
                  style: JarvisText.chip.copyWith(
                    color: JarvisColors.inkDim,
                    letterSpacing: 9 * 0.3,
                    fontSize: 9,
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _caption() {
    if (widget.response != null && widget.response!.isNotEmpty) {
      return Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Flexible(
            child: Text(
              widget.response!,
              textAlign: TextAlign.center,
              maxLines: 3,
              style: const TextStyle(
                fontFamily: null,
                fontSize: 14,
                color: JarvisColors.cyanSoft,
                height: 1.4,
                shadows: [Shadow(color: JarvisColors.cyan, blurRadius: 8)],
              ),
            ),
          ),
          const SizedBox(width: 4),
          // Blinking caret
          AnimatedBuilder(
            animation: _blinkCtrl,
            builder: (_, __) => Opacity(
              opacity: _blinkCtrl.value,
              child: Container(width: 6, height: 13, color: JarvisColors.cyan),
            ),
          ),
        ],
      );
    }
    if (widget.heard != null && widget.heard!.isNotEmpty) {
      return Text(
        '"${widget.heard}"',
        textAlign: TextAlign.center,
        maxLines: 2,
        style: const TextStyle(
          fontFamily: null,
          fontSize: 14,
          color: JarvisColors.ink,
          height: 1.4,
          fontStyle: FontStyle.italic,
        ),
      );
    }
    return const Text(
      'Listening…',
      style: TextStyle(
        fontFamily: null,
        fontSize: 14,
        color: JarvisColors.inkDim,
      ),
    );
  }
}

class _OrbHalo extends StatelessWidget {
  final double size;
  final ConversationState orbState;
  final AnimationController spinCtrl;

  const _OrbHalo({
    required this.size,
    required this.orbState,
    required this.spinCtrl,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        border: Border.all(color: JarvisColors.lineDim, width: 1),
        boxShadow: [BoxShadow(color: JarvisColors.cyan.withOpacity(0.2), blurRadius: 18)],
      ),
      child: Stack(
        alignment: Alignment.center,
        children: [
          JarvisOrb(size: size * 0.87, state: orbState),
          // Spinning border-top arc
          AnimatedBuilder(
            animation: spinCtrl,
            builder: (_, __) => CustomPaint(
              size: Size(size, size),
              painter: _SpinArcPainter(angle: spinCtrl.value * math.pi * 2),
            ),
          ),
        ],
      ),
    );
  }
}

class _SpinArcPainter extends CustomPainter {
  final double angle;
  const _SpinArcPainter({required this.angle});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = JarvisColors.cyan.withOpacity(0.7)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5
      ..strokeCap = StrokeCap.round;
    final r = size.width / 2 - 1;
    canvas.drawArc(
      Rect.fromCircle(center: Offset(size.width / 2, size.height / 2), radius: r),
      angle - 0.5,
      1.0,
      false,
      paint,
    );
  }

  @override
  bool shouldRepaint(_SpinArcPainter old) => old.angle != angle;
}
