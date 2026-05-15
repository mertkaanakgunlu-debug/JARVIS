import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../theme/state_accent.dart';
import '../widgets/grid_background.dart';
import '../widgets/jarvis_orb.dart';
import '../widgets/orbital_rings.dart';
import '../widgets/hud_panel_card.dart';
import '../models/conversation_state.dart';
import '../providers/state_provider.dart';
import '../providers/ws_provider.dart';
import '../providers/todos_provider.dart';
import '../providers/finance_provider.dart';
import '../providers/tasks_provider.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(conversationStateProvider);
    final accent = accentForState(state);
    final vaultCount = ref.watch(vaultCountProvider);
    final tasksAsync = ref.watch(tasksProvider);
    final todosAsync = ref.watch(todosProvider);
    final calendarEvents = ref.watch(calendarWsProvider);
    final financeSummary = ref.watch(financeSummaryProvider);

    final runningCount = tasksAsync.valueOrNull
            ?.where((t) => t.isRunning)
            .length ?? 0;
    final todayDone = todosAsync.valueOrNull
            ?.where((t) => t['status'] == 'done')
            .length ?? 0;
    final todayTotal = todosAsync.valueOrNull?.length ?? 0;
    final nextEvent = calendarEvents.isNotEmpty ? calendarEvents.first : null;

    final budgetAsync = ref.watch(financeBudgetProvider);
    final spendingData = financeSummary.valueOrNull;
    final double spendingTotal = (spendingData?['expenses'] as num?)?.abs().toDouble() ?? 0;
    final double spendingLimit = budgetAsync.valueOrNull?.fold<double>(
      0, (sum, b) => sum + ((b['limit'] as num?)?.toDouble() ?? 0),
    ) ?? 0;

    return GridBackground(
      child: SafeArea(
        child: Column(
          children: [
            _TopBar(state: state, onSettings: () =>
                Navigator.pushNamed(context, '/settings')),
            const SizedBox(height: 12),
            // Orb
            SizedBox(
              width: 320,
              height: 320,
              child: Stack(
                alignment: Alignment.center,
                children: [
                  OrbitalRings(size: 320, color: accent),
                  JarvisOrb(size: 240, state: state),
                ],
              ),
            ),
            const SizedBox(height: 8),
            Text(
              labelForState(state),
              style: JarvisText.sectionHeader.copyWith(
                color: accent,
                shadows: [Shadow(color: accent.withOpacity(0.6), blurRadius: 6)],
              ),
            ),
            Text('GEMINI 2.5 FLASH · VERTEX',
                style: JarvisText.chip.copyWith(color: JarvisColors.inkFaint)),
            const SizedBox(height: 16),
            // Summary card
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 18),
              child: HudPanelCard(
                accentColor: accent,
                padding: const EdgeInsets.all(12),
                child: Column(
                  children: [
                    _SummaryRow(label: 'Active jobs', value: '$runningCount running',
                        valueColor: accent),
                    _SummaryRow(label: 'Today', value: '$todayDone of $todayTotal done'),
                    _SummaryRow(
                      label: 'Next event',
                      value: nextEvent != null
                          ? _fmtEvent(nextEvent)
                          : '—',
                    ),
                    _SummaryRow(label: 'Vault', value: '$vaultCount vectors'),
                    GestureDetector(
                      onTap: () => Navigator.pushNamed(context, '/finance_detail'),
                      child: _SummaryRow(
                        label: 'Spending',
                        value: spendingLimit > 0
                            ? '₺${spendingTotal.toStringAsFixed(0)} / ₺${spendingLimit.toStringAsFixed(0)}'
                            : '—',
                        valueColor: spendingLimit > 0 && spendingTotal / spendingLimit > 0.8
                            ? JarvisColors.amber
                            : null,
                        trailing: const Icon(Icons.chevron_right, size: 14,
                            color: JarvisColors.inkFaint),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }

  String _fmtEvent(Map<String, dynamic> ev) {
    final title = ev['title'] as String? ?? '';
    final start = ev['start'] as String? ?? '';
    if (start.length >= 16) {
      final time = start.substring(11, 16);
      return '$time · $title';
    }
    return title;
  }
}

class _TopBar extends StatefulWidget {
  final ConversationState state;
  final VoidCallback onSettings;
  const _TopBar({required this.state, required this.onSettings});
  @override State<_TopBar> createState() => _TopBarState();
}

class _TopBarState extends State<_TopBar> {
  late Timer _timer;
  late DateTime _now;

  @override
  void initState() {
    super.initState();
    _now = DateTime.now();
    _timer = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted) setState(() => _now = DateTime.now());
    });
  }

  @override
  void dispose() {
    _timer.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final h = _now.hour.toString().padLeft(2, '0');
    final m = _now.minute.toString().padLeft(2, '0');
    return Padding(
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 0),
      child: Row(
        children: [
          Text('JARVIS', style: JarvisText.wordmark),
          const Spacer(),
          Text('CORE', style: JarvisText.screenTitle),
          const Spacer(),
          Text('$h:$m', style: JarvisText.screenTitle),
          const SizedBox(width: 8),
          GestureDetector(
            onTap: widget.onSettings,
            child: const Icon(Icons.settings_outlined, size: 18,
                color: JarvisColors.inkDim),
          ),
        ],
      ),
    );
  }
}

class _SummaryRow extends StatelessWidget {
  final String label;
  final String value;
  final Color? valueColor;
  final Widget? trailing;

  const _SummaryRow({
    required this.label,
    required this.value,
    this.valueColor,
    this.trailing,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        children: [
          Text(label, style: JarvisText.sectionHeader.copyWith(fontSize: 10)),
          const Spacer(),
          Text(value, style: JarvisText.chip.copyWith(
            fontSize: 11,
            color: valueColor ?? JarvisColors.cyanSoft,
          )),
          if (trailing != null) ...[const SizedBox(width: 4), trailing!],
        ],
      ),
    );
  }
}
