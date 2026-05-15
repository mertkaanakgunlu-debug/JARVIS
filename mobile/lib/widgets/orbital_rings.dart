import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../theme/jarvis_theme.dart';

class OrbitalRings extends StatefulWidget {
  final double size;
  final Color? color;

  const OrbitalRings({super.key, this.size = 320, this.color});

  @override
  State<OrbitalRings> createState() => _OrbitalRingsState();
}

class _OrbitalRingsState extends State<OrbitalRings>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 30),
    )..repeat();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _ctrl,
      builder: (_, __) => CustomPaint(
        size: Size(widget.size, widget.size),
        painter: _RingsPainter(
          t: _ctrl.value * math.pi * 2,
          color: widget.color ?? JarvisColors.cyan,
        ),
      ),
    );
  }
}

class _RingsPainter extends CustomPainter {
  final double t;
  final Color color;

  const _RingsPainter({required this.t, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final cx = size.width / 2;
    final cy = size.height / 2;
    final base = size.width / 2;

    final paint = Paint()
      ..color = color.withOpacity(0.4)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.8;

    // Ring 1: 60 tick marks, +14°/s
    _drawTickRing(canvas, cx, cy, base * 0.46, 60, t * (14 / 360), paint);

    // Ring 2: dashed arcs, -22°/s, 4 corner squares
    _drawDashedRing(canvas, cx, cy, base * 0.42, t * (-22 / 360), paint);

    // Ring 3: 90 dots, +6°/s + cardinal labels
    _drawDotRing(canvas, cx, cy, base * 0.50, 90, t * (6 / 360), paint);
  }

  void _drawTickRing(Canvas c, double cx, double cy, double r,
      int n, double angle, Paint p) {
    for (int i = 0; i < n; i++) {
      final a = angle + (i / n) * math.pi * 2;
      final cos = math.cos(a), sin = math.sin(a);
      final inner = r - (i % 5 == 0 ? 6 : 3);
      c.drawLine(
        Offset(cx + cos * inner, cy + sin * inner),
        Offset(cx + cos * r, cy + sin * r),
        p,
      );
    }
    c.drawCircle(Offset(cx, cy), r, p..style = PaintingStyle.stroke);
  }

  void _drawDashedRing(Canvas c, double cx, double cy, double r,
      double angle, Paint p) {
    const dashPattern = [40.0, 14.0, 6.0, 14.0, 90.0, 14.0, 30.0, 14.0];
    double progress = 0;
    bool drawing = true;
    while (progress < 360) {
      for (final seg in dashPattern) {
        if (drawing) {
          final startRad = (progress / 180) * math.pi + angle;
          final endRad = ((progress + seg) / 180) * math.pi + angle;
          c.drawArc(
            Rect.fromCircle(center: Offset(cx, cy), radius: r),
            startRad, endRad - startRad, false, p,
          );
        }
        progress += seg;
        drawing = !drawing;
        if (progress >= 360) break;
      }
    }
    // 4 corner squares
    for (int i = 0; i < 4; i++) {
      final a = angle + (i / 4) * math.pi * 2;
      final cx2 = cx + math.cos(a) * r;
      final cy2 = cy + math.sin(a) * r;
      c.drawRect(Rect.fromCenter(center: Offset(cx2, cy2), width: 4, height: 4), p);
    }
  }

  void _drawDotRing(Canvas c, double cx, double cy, double r,
      int n, double angle, Paint p) {
    final dotPaint = Paint()
      ..color = p.color
      ..style = PaintingStyle.fill;
    for (int i = 0; i < n; i++) {
      final a = angle + (i / n) * math.pi * 2;
      c.drawCircle(Offset(cx + math.cos(a) * r, cy + math.sin(a) * r), 1.2, dotPaint);
    }
    // Cardinal labels N/E/S/W
    const labels = ['N', 'E', 'S', 'W'];
    for (int i = 0; i < 4; i++) {
      final a = angle + (i / 4) * math.pi * 2 - math.pi / 2;
      final lx = cx + math.cos(a) * (r + 14);
      final ly = cy + math.sin(a) * (r + 14);
      final span = TextSpan(
        text: labels[i],
        style: TextStyle(
          fontFamily: 'ShareTechMono',
          fontSize: 7,
          color: p.color.withOpacity(0.7),
        ),
      );
      final tp2 = TextPainter(text: span, textDirection: TextDirection.ltr)..layout();
      tp2.paint(c, Offset(lx - tp2.width / 2, ly - tp2.height / 2));
    }
  }

  @override
  bool shouldRepaint(_RingsPainter old) => old.t != t;
}
