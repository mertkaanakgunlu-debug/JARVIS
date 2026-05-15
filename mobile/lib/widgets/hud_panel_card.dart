import 'package:flutter/material.dart';
import '../theme/jarvis_theme.dart';

class HudPanelCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final Color? accentColor;

  const HudPanelCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(14),
    this.accentColor,
  });

  @override
  Widget build(BuildContext context) {
    final accent = accentColor ?? JarvisColors.cyan;
    return ClipPath(
      clipper: _OctagonClipper(12),
      child: Container(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [
              const Color(0xFF0891B2).withOpacity(0.06),
              Colors.black.withOpacity(0.55),
            ],
          ),
          border: Border.all(color: accent.withOpacity(0.55), width: 1),
          boxShadow: [
            BoxShadow(color: accent.withOpacity(0.12), blurRadius: 12),
            BoxShadow(color: accent.withOpacity(0.08), blurRadius: 32),
          ],
        ),
        child: Stack(
          children: [
            Padding(padding: padding, child: child),
            ..._cornerBrackets(accent),
          ],
        ),
      ),
    );
  }

  List<Widget> _cornerBrackets(Color accent) {
    const s = 12.0;
    const t = 1.5;
    final color = accent.withOpacity(0.8);
    return [
      _bracket(top: 0, left: 0, color: color, s: s, t: t),
      _bracket(top: 0, right: 0, color: color, s: s, t: t, flipH: true),
      _bracket(bottom: 0, left: 0, color: color, s: s, t: t, flipV: true),
      _bracket(bottom: 0, right: 0, color: color, s: s, t: t, flipH: true, flipV: true),
    ];
  }

  Widget _bracket({
    double? top, double? bottom, double? left, double? right,
    required Color color, required double s, required double t,
    bool flipH = false, bool flipV = false,
  }) {
    return Positioned(
      top: top, bottom: bottom, left: left, right: right,
      child: Transform(
        alignment: Alignment.center,
        transform: Matrix4.identity()
          ..scale(flipH ? -1.0 : 1.0, flipV ? -1.0 : 1.0, 1.0),
        child: SizedBox(
          width: s,
          height: s,
          child: CustomPaint(painter: _BracketPainter(color: color, t: t)),
        ),
      ),
    );
  }
}

class _BracketPainter extends CustomPainter {
  final Color color;
  final double t;
  const _BracketPainter({required this.color, required this.t});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..color = color..strokeWidth = t..style = PaintingStyle.stroke;
    canvas.drawLine(Offset(0, size.height), const Offset(0, 0), paint);
    canvas.drawLine(const Offset(0, 0), Offset(size.width, 0), paint);
  }

  @override
  bool shouldRepaint(_BracketPainter old) => false;
}

class _OctagonClipper extends CustomClipper<Path> {
  final double radius;
  const _OctagonClipper(this.radius);

  @override
  Path getClip(Size size) {
    final r = radius;
    return Path()
      ..moveTo(r, 0)
      ..lineTo(size.width - r, 0)
      ..lineTo(size.width, r)
      ..lineTo(size.width, size.height - r)
      ..lineTo(size.width - r, size.height)
      ..lineTo(r, size.height)
      ..lineTo(0, size.height - r)
      ..lineTo(0, r)
      ..close();
  }

  @override
  bool shouldReclip(_OctagonClipper old) => old.radius != radius;
}
