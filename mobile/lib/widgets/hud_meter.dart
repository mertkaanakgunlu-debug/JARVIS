import 'package:flutter/material.dart';
import '../theme/jarvis_theme.dart';

class HudMeter extends StatelessWidget {
  final double value; // 0.0 – 1.0
  final Color? color;
  final double height;

  const HudMeter({super.key, required this.value, this.color, this.height = 4});

  @override
  Widget build(BuildContext context) {
    final accent = color ?? JarvisColors.cyan;
    return Container(
      height: height,
      decoration: BoxDecoration(
        color: JarvisColors.lineDim,
        borderRadius: BorderRadius.circular(2),
      ),
      child: FractionallySizedBox(
        alignment: Alignment.centerLeft,
        widthFactor: value.clamp(0.0, 1.0),
        child: Container(
          decoration: BoxDecoration(
            color: accent,
            borderRadius: BorderRadius.circular(2),
            boxShadow: [BoxShadow(color: accent.withValues(alpha: 0.5), blurRadius: 6)],
          ),
        ),
      ),
    );
  }
}
