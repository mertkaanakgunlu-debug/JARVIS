import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../widgets/grid_background.dart';
import '../widgets/hud_chip.dart';
import '../widgets/hud_panel_card.dart';
import '../providers/api_provider.dart';

final _calendarTodayProvider = FutureProvider<Map<String, dynamic>>((ref) async {
  final api = ref.read(apiClientProvider);
  if (api == null) return {};
  final resp = await api.get<Map<String, dynamic>>('/calendar/today');
  return resp.data ?? {};
});

class ScheduleScreen extends ConsumerWidget {
  const ScheduleScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final calAsync = ref.watch(_calendarTodayProvider);

    return GridBackground(
      child: SafeArea(
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(18, 12, 18, 0),
              child: Row(
                children: [
                  Text('SCHED', style: JarvisText.wordmark),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(Icons.refresh, size: 18,
                        color: JarvisColors.inkDim),
                    onPressed: () => ref.invalidate(_calendarTodayProvider),
                  ),
                ],
              ),
            ),
            Expanded(
              child: calAsync.when(
                data: (data) {
                  final date = data['date'] as String? ?? '';
                  final events = (data['events'] as List? ?? [])
                      .cast<Map<String, dynamic>>();
                  return RefreshIndicator(
                    onRefresh: () async => ref.invalidate(_calendarTodayProvider),
                    child: ListView(
                      padding: const EdgeInsets.fromLTRB(18, 12, 18, 100),
                      children: [
                        Padding(
                          padding: const EdgeInsets.only(bottom: 12),
                          child: Text('// $date',
                              style: JarvisText.sectionHeader),
                        ),
                        if (events.isEmpty)
                          Center(
                            child: Padding(
                              padding: const EdgeInsets.all(32),
                              child: Text('Bugün etkinlik yok.',
                                  style: JarvisText.sectionHeader),
                            ),
                          )
                        else
                          ...events.map((ev) => Padding(
                            padding: const EdgeInsets.only(bottom: 8),
                            child: _EventRow(event: ev),
                          )),
                      ],
                    ),
                  );
                },
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (e, _) => Center(
                  child: Text('Takvim yüklenemedi\n$e',
                      textAlign: TextAlign.center,
                      style: JarvisText.chip.copyWith(color: JarvisColors.red)),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _EventRow extends StatelessWidget {
  final Map<String, dynamic> event;
  const _EventRow({required this.event});

  @override
  Widget build(BuildContext context) {
    final title = event['title'] as String? ?? '';
    final location = event['location'] as String? ?? '';
    final start = event['start'] as String? ?? '';
    final timeStr = start.length >= 16 ? start.substring(11, 16) : start;

    return HudPanelCard(
      padding: const EdgeInsets.all(12),
      child: Row(
        children: [
          SizedBox(
            width: 58,
            child: Text(timeStr,
                style: JarvisText.chip.copyWith(
                  fontSize: 16,
                  color: JarvisColors.cyanSoft,
                  shadows: [const Shadow(
                      color: JarvisColors.cyan, blurRadius: 6)],
                )),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title,
                    style: JarvisText.chip.copyWith(
                        fontSize: 13, color: JarvisColors.ink)),
                if (location.isNotEmpty)
                  Text(location.toUpperCase(),
                      style: JarvisText.chip.copyWith(
                          fontSize: 9,
                          color: JarvisColors.inkDim,
                          letterSpacing: 9 * 0.14)),
              ],
            ),
          ),
          const HudChip('NEXT'),
        ],
      ),
    );
  }
}
