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
        bg = JarvisColors.red.withOpacity(0.15);
        border = JarvisColors.red.withOpacity(0.5);
        text = JarvisColors.red;
      case ChipVariant.amber:
        bg = JarvisColors.amber.withOpacity(0.15);
        border = JarvisColors.amber.withOpacity(0.5);
        text = JarvisColors.amber;
      case ChipVariant.dim:
        bg = JarvisColors.inkFaint.withOpacity(0.15);
        border = JarvisColors.inkFaint.withOpacity(0.4);
        text = JarvisColors.inkDim;
      case ChipVariant.cyan:
        bg = JarvisColors.cyan.withOpacity(0.12);
        border = JarvisColors.cyan.withOpacity(0.5);
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
