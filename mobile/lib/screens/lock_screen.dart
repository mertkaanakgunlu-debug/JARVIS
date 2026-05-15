import 'dart:async';
import 'package:flutter/material.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../widgets/grid_background.dart';
import '../widgets/jarvis_orb.dart';
import '../models/conversation_state.dart';

class LockScreen extends StatefulWidget {
  const LockScreen({super.key});

  @override
  State<LockScreen> createState() => _LockScreenState();
}

class _LockScreenState extends State<LockScreen> {
  late Timer _clockTimer;
  late DateTime _now;

  @override
  void initState() {
    super.initState();
    _now = DateTime.now();
    _clockTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() => _now = DateTime.now());
    });
  }

  @override
  void dispose() {
    _clockTimer.cancel();
    super.dispose();
  }

  String get _timeStr {
    final h = _now.hour.toString().padLeft(2, '0');
    final m = _now.minute.toString().padLeft(2, '0');
    return '$h:$m';
  }

  String get _dateStr {
    const days = ['MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY', 'SUNDAY'];
    const months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                    'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
    return '${days[_now.weekday - 1]}, ${months[_now.month - 1]} ${_now.day}';
  }

  @override
  Widget build(BuildContext context) {
    return GridBackground(
      child: SafeArea(
        child: Column(
          children: [
            const SizedBox(height: 40),
            // STARK INDUSTRIES
            Text('STARK INDUSTRIES',
                style: JarvisText.wordmark.copyWith(letterSpacing: 10 * 0.36)),
            const SizedBox(height: 4),
            Text('/// JARVIS · STANDBY',
                style: JarvisText.sectionHeader.copyWith(letterSpacing: 8 * 0.26)),
            const SizedBox(height: 28),
            // Clock
            Text(_timeStr, style: JarvisText.clock),
            const SizedBox(height: 8),
            Text(_dateStr,
                style: JarvisText.sectionHeader.copyWith(
                  fontSize: 11,
                  letterSpacing: 11 * 0.26,
                )),
            const Spacer(),
            // Orb
            const JarvisOrb(size: 200, state: ConversationState.idle),
            const SizedBox(height: 28),
            Text('AT YOUR SERVICE, SIR',
                style: JarvisText.screenTitle.copyWith(
                  color: JarvisColors.cyanSoft,
                  shadows: [const Shadow(color: JarvisColors.cyan, blurRadius: 8)],
                )),
            const SizedBox(height: 8),
            Text('SAY "HEY JARVIS" TO WAKE',
                style: JarvisText.chip.copyWith(
                  fontSize: 9,
                  letterSpacing: 9 * 0.24,
                  color: JarvisColors.inkDim,
                )),
            const Spacer(),
          ],
        ),
      ),
    );
  }
}
