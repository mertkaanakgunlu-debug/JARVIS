import 'package:flutter/material.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';

enum ChipVariant { cyan, red, amber, dim }

class HudChip extends StatelessWidget {
  final String label;
  final ChipVariant variant;

  const HudChip(this.label, {super.key, this.variant = ChipVariant.cyan});

  @override
  Widget build(BuildContext context) {
    final Color bg, border, text;
    switch (variant) {
      case ChipVariant.red:
        bg = JarvisColors.red.withValues(alpha: 0.15);
        border = JarvisColors.red.withValues(alpha: 0.5);
        text = JarvisColors.red;
      case ChipVariant.amber:
        bg = JarvisColors.amber.withValues(alpha: 0.15);
        border = JarvisColors.amber.withValues(alpha: 0.5);
        text = JarvisColors.amber;
      case ChipVariant.dim:
        bg = JarvisColors.inkFaint.withValues(alpha: 0.15);
        border = JarvisColors.inkFaint.withValues(alpha: 0.4);
        text = JarvisColors.inkDim;
      case ChipVariant.cyan:
        bg = JarvisColors.cyan.withValues(alpha: 0.12);
        border = JarvisColors.cyan.withValues(alpha: 0.5);
        text = JarvisColors.cyanSoft;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: bg,
        border: Border.all(color: border, width: 1),
        borderRadius: BorderRadius.circular(3),
      ),
      child: Text(
        label.toUpperCase(),
        style: JarvisText.chip.copyWith(color: text),
      ),
    );
  }
}
