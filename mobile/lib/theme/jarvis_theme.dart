import 'package:flutter/material.dart';

class JarvisColors {
  // Backgrounds
  static const bg = Color(0xFF000000);

  // Ink / text
  static const ink = Color(0xFFD6F1F7);
  static const inkDim = Color(0xFF7BA9B3);
  static const inkFaint = Color(0xFF3A6571);

  // Accent cyan
  static const cyan = Color(0xFF22D3EE);
  static const cyanSoft = Color(0xFF67E8F9);
  static const cyanDeep = Color(0xFF0891B2);

  // State accents
  static const thinking = Color(0xFFFFC857);   // working / thinking — yellow
  static const speaking = Color(0xFFFF5577);   // speaking — pink-red

  // Status colours
  static const red = Color(0xFFEF4444);
  static const amber = Color(0xFFFBBF24);
  static const green = Color(0xFF34D399);

  // Border / overlay alphas
  static const line = Color(0x8C22D3EE);       // rgba(34,211,238,.55)
  static const lineDim = Color(0x2E22D3EE);    // rgba(34,211,238,.18)
  static const lineGlow = Color(0x1422D3EE);   // rgba(34,211,238,.08) grid
}

class JarvisTheme {
  static ThemeData darkTheme() {
    return ThemeData(
      brightness: Brightness.dark,
      scaffoldBackgroundColor: JarvisColors.bg,
      colorScheme: const ColorScheme.dark(
        primary: JarvisColors.cyan,
        secondary: JarvisColors.cyanSoft,
        surface: JarvisColors.bg,
        error: JarvisColors.red,
      ),
      fontFamily: 'ShareTechMono',
      textTheme: const TextTheme(
        bodyMedium: TextStyle(
          color: JarvisColors.ink,
          fontSize: 13,
          letterSpacing: 0.04 * 13,
        ),
        bodySmall: TextStyle(
          color: JarvisColors.inkDim,
          fontSize: 11,
          letterSpacing: 0.04 * 11,
        ),
      ),
      iconTheme: const IconThemeData(color: JarvisColors.inkDim, size: 20),
      dividerColor: JarvisColors.lineDim,
      useMaterial3: true,
    );
  }
}
