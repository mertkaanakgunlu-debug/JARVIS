import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../models/conversation_state.dart';
import '../theme/state_accent.dart';

class JarvisOrb extends StatefulWidget {
  final double size;
  final ConversationState state;

  const JarvisOrb({super.key, this.size = 200, this.state = ConversationState.idle});

  @override
  State<JarvisOrb> createState() => _JarvisOrbState();
}

class _JarvisOrbState extends State<JarvisOrb> with SingleTickerProviderStateMixin {
  late AnimationController _ticker;
  static const _pointCount = 600;
  static final List<List<double>> _pts = _genFibPoints(_pointCount);

  @override
  void initState() {
    super.initState();
    _ticker = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 1000),
    )..repeat();
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  static List<List<double>> _genFibPoints(int n) {
    final goldenAngle = math.pi * (3 - math.sqrt(5));
    final pts = <List<double>>[];
    for (int i = 0; i < n; i++) {
      final y = 1 - (i / (n - 1)) * 2;
      final r = math.sqrt(1 - y * y);
      final theta = goldenAngle * i;
      pts.add([r * math.cos(theta), y, r * math.sin(theta)]);
    }
    return pts;
  }

  @override
  Widget build(BuildContext context) {
    final accent = accentForState(widget.state);
    return RepaintBoundary(
      child: AnimatedBuilder(
        animation: _ticker,
        builder: (_, __) => CustomPaint(
          size: Size(widget.size, widget.size),
          painter: _OrbPainter(
            t: _ticker.value * 1000,
            state: widget.state,
            accent: accent,
            points: _pts,
          ),
        ),
      ),
    );
  }
}

class _OrbPainter extends CustomPainter {
  final double t;
  final ConversationState state;
  final Color accent;
  final List<List<double>> points;

  const _OrbPainter({
    required this.t,
    required this.state,
    required this.accent,
    required this.points,
  });

  double get speedMul {
    switch (state) {
      case ConversationState.thinking: return 1.8;
      case ConversationState.working:  return 1.2;
      case ConversationState.speaking: return 0.9;
      case ConversationState.listening: return 0.55;
      case ConversationState.idle:     return 0.45;
    }
  }

  @override
  void paint(Canvas canvas, Size size) {
    final cx = size.width / 2;
    final cy = size.height / 2;
    final R = size.width * 0.35;
    final focal = size.width * 0.85;

    final sm = speedMul;
    final rx = math.sin(t * 0.35 * sm) * 0.5 + 0.2;
    final ry = t * 0.45 * sm;

    final cosRx = math.cos(rx), sinRx = math.sin(rx);
    final cosRy = math.cos(ry), sinRy = math.sin(ry);

    final List<({double x2d, double y2d, double z3d})> projected = [];
    for (final p in points) {
      double x = p[0], y = p[1], z = p[2];
      // Rotate Y
      final x1 = x * cosRy - z * sinRy;
      final z1 = x * sinRy + z * cosRy;
      // Rotate X
      final y2 = y * cosRx - z1 * sinRx;
      final z2 = y * sinRx + z1 * cosRx;
      final scale = focal / (focal + z2 * R);
      projected.add((x2d: cx + x1 * R * scale, y2d: cy + y2 * R * scale, z3d: z2));
    }

    final dotPaint = Paint()
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 1.5)
      ..style = PaintingStyle.fill;

    for (final p in projected) {
      final front = (p.z3d + 1) / 2;
      final alpha = p.z3d > 0 ? 0.5 * front : 0.12 * front;
      final dotSize = p.z3d > 0 ? 1.0 + front * 0.8 : 0.6;
      dotPaint.color = accent.withValues(alpha: alpha.clamp(0.0, 1.0));
      canvas.drawCircle(Offset(p.x2d, p.y2d), dotSize, dotPaint);
    }

    // Core glow
    final coreR = R * 0.28;
    final coreGradient = RadialGradient(
      colors: [accent.withValues(alpha: 0.9), accent.withValues(alpha: 0)],
      stops: const [0, 1],
    );
    final corePaint = Paint()
      ..shader = coreGradient.createShader(
          Rect.fromCircle(center: Offset(cx, cy), radius: coreR * 4));
    canvas.drawCircle(Offset(cx, cy), coreR * 4, corePaint);

    // Speaking: equator scan ellipse
    if (state == ConversationState.speaking || state == ConversationState.thinking) {
      final scanPaint = Paint()
        ..color = accent.withValues(alpha: 0.25)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1;
      canvas.drawOval(
        Rect.fromCenter(center: Offset(cx, cy), width: R * 2.1, height: R * 0.3),
        scanPaint,
      );
    }
  }

  @override
  bool shouldRepaint(_OrbPainter old) => old.t != t || old.state != state;
}
