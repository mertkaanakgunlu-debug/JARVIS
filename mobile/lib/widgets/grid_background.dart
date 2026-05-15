import 'package:flutter/material.dart';
import '../theme/jarvis_theme.dart';

class GridBackground extends StatelessWidget {
  final double gridAlpha;
  final Widget child;

  const GridBackground({super.key, this.gridAlpha = 0.04, required this.child});

  @override
  Widget build(BuildContext context) {
    return Stack(
      fit: StackFit.expand,
      children: [
        // Two radial gradients matching m-screen in CSS
        Container(
          decoration: const BoxDecoration(
            color: JarvisColors.bg,
            gradient: RadialGradient(
              center: Alignment(0, -0.5),
              radius: 1.2,
              colors: [Color(0x1222D3EE), Colors.transparent],
              stops: [0, 1],
            ),
          ),
        ),
        Container(
          decoration: const BoxDecoration(
            gradient: RadialGradient(
              center: Alignment(0, 0.6),
              radius: 1.0,
              colors: [Color(0x290891B2), Colors.transparent],
              stops: [0, 1],
            ),
          ),
        ),
        // Grid overlay
        CustomPaint(
          painter: _GridPainter(alpha: gridAlpha),
          child: child,
        ),
      ],
    );
  }
}

class _GridPainter extends CustomPainter {
  final double alpha;
  const _GridPainter({required this.alpha});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = JarvisColors.cyan.withOpacity(alpha)
      ..strokeWidth = 0.5;

    const step = 24.0;
    for (double x = 0; x <= size.width; x += step) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), paint);
    }
    for (double y = 0; y <= size.height; y += step) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), paint);
    }
  }

  @override
  bool shouldRepaint(_GridPainter old) => old.alpha != alpha;
}
