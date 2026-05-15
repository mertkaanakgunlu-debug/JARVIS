import 'package:flutter/material.dart';
import 'jarvis_theme.dart';

class JarvisText {
  // JARVIS wordmark (TopBar)
  static const wordmark = TextStyle(
    fontFamily: 'Orbitron',
    fontWeight: FontWeight.w800,
    fontSize: 10,
    letterSpacing: 10 * 0.32,
    color: JarvisColors.cyanSoft,
    shadows: [Shadow(color: JarvisColors.cyan, blurRadius: 8)],
  );

  // Lock screen clock — Orbitron 200 weight 96px
  static const clock = TextStyle(
    fontFamily: 'Orbitron',
    fontWeight: FontWeight.w400, // Regular as closest to 200 weight
    fontSize: 96,
    letterSpacing: 96 * 0.01,
    color: JarvisColors.ink,
    shadows: [Shadow(color: Colors.white, blurRadius: 28)],
  );

  // Section headers (m-h): Share Tech Mono 9px uppercase dim
  static const sectionHeader = TextStyle(
    fontFamily: 'ShareTechMono',
    fontSize: 9,
    letterSpacing: 9 * 0.32,
    color: JarvisColors.inkFaint,
  );

  // Chip labels (m-chip): 8px uppercase
  static const chip = TextStyle(
    fontFamily: 'ShareTechMono',
    fontSize: 8,
    letterSpacing: 8 * 0.16,
    color: JarvisColors.inkDim,
  );

  // Bottom tab labels
  static const tabLabel = TextStyle(
    fontFamily: 'ShareTechMono',
    fontSize: 8,
    letterSpacing: 8 * 0.18,
    color: JarvisColors.inkFaint,
  );

  // Chat message body — system font for readability
  static const chatBody = TextStyle(
    fontFamily: null, // system font
    fontSize: 13,
    letterSpacing: 0,
    height: 1.45,
    color: JarvisColors.ink,
  );

  // Heading within screens
  static const screenTitle = TextStyle(
    fontFamily: 'ShareTechMono',
    fontSize: 10,
    letterSpacing: 10 * 0.28,
    color: JarvisColors.cyanSoft,
  );
}
