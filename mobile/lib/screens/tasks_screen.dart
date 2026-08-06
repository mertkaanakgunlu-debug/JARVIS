import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../widgets/grid_background.dart';
import '../widgets/hud_panel_card.dart';
import '../widgets/hud_chip.dart';
import '../widgets/hud_meter.dart';
import '../providers/todos_provider.dart';
import '../providers/tasks_provider.dart';

class TasksScreen extends ConsumerWidget {
  const TasksScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final todosAsync = ref.watch(todosProvider);
    final asyncTasksAsync = ref.watch(tasksProvider);

    return GridBackground(
      child: SafeArea(
        child: Column(
          children: [
            _TopBar(),
            Expanded(
              child: RefreshIndicator(
                onRefresh: () async {
                  ref.invalidate(todosProvider);
                  ref.read(tasksProvider.notifier).refresh();
                },
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(18, 8, 18, 100),
                  children: [
                    // Async running tasks
                    asyncTasksAsync.when(
                      data: (tasks) {
                        final running = tasks.where((t) => t.isRunning).toList();
                        if (running.isEmpty) return const SizedBox.shrink();
                        return Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: Text('// RUNNING TASKS',
                                  style: JarvisText.sectionHeader),
                            ),
                            ...running.map((t) => Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: GestureDetector(
                                onTap: () => Navigator.pushNamed(
                                    context, '/task_detail', arguments: t.taskId),
                                child: HudPanelCard(
                                  accentColor: JarvisColors.amber,
                                  padding: const EdgeInsets.all(12),
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Row(
                                        children: [
                                          Expanded(
                                            child: Text(
                                              t.userQuery,
                                              maxLines: 2,
                                              overflow: TextOverflow.ellipsis,
                                              style: JarvisText.chip.copyWith(
                                                  fontSize: 11,
                                                  color: JarvisColors.cyanSoft),
                                            ),
                                          ),
                                          const SizedBox(width: 8),
                                          HudChip(t.status.toUpperCase(),
                                              variant: ChipVariant.amber),
                                        ],
                                      ),
                                      const SizedBox(height: 8),
                                      HudMeter(
                                          value: t.status == 'running' ? 0.6 : 0.1,
                                          color: JarvisColors.amber),
                                      const SizedBox(height: 4),
                                      Text(
                                        t.progressNotes.isNotEmpty
                                            ? t.progressNotes.last
                                            : 'Bekliyor…',
                                        style: JarvisText.chip.copyWith(
                                            color: JarvisColors.inkFaint),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            )),
                            const SizedBox(height: 16),
                          ],
                        );
                      },
                      loading: () => const SizedBox.shrink(),
                      error: (_, __) => const SizedBox.shrink(),
                    ),

                    // To-do list
                    todosAsync.when(
                      data: (todos) {
                        if (todos.isEmpty) {
                          return Center(
                            child: Padding(
                              padding: const EdgeInsets.all(32),
                              child: Text('Tüm görevler tamamlandı.',
                                  style: JarvisText.sectionHeader),
                            ),
                          );
                        }
                        final doneCount = todos.where((t) => t['status'] == 'done').length;
                        return Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: Text(
                                '// ${todos.length - doneCount} OPEN  · $doneCount DONE',
                                style: JarvisText.sectionHeader,
                              ),
                            ),
                            ...todos.where((t) => t['status'] == 'open').map((t) =>
                              Padding(
                                padding: const EdgeInsets.only(bottom: 8),
                                child: _TodoCard(todo: t, ref: ref),
                              ),
                            ),
                          ],
                        );
                      },
                      loading: () => const Center(child: CircularProgressIndicator()),
                      error: (e, _) => Text('Hata: $e',
                          style: JarvisText.chip.copyWith(color: JarvisColors.red)),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _TopBar extends StatelessWidget {
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(18, 12, 18, 0),
    child: Row(
      children: [
        Text('TASKS', style: JarvisText.wordmark),
        const Spacer(),
        GestureDetector(
          onTap: () => _showAddTodoSheet(context),
          child: const Icon(Icons.add, color: JarvisColors.cyanSoft, size: 20),
        ),
      ],
    ),
  );

  void _showAddTodoSheet(BuildContext context) {
    showModalBottomSheet(
      context: context,
      backgroundColor: JarvisColors.bg,
      builder: (_) => const _AddTodoSheet(),
    );
  }
}

class _TodoCard extends StatelessWidget {
  final Map<String, dynamic> todo;
  final WidgetRef ref;
  const _TodoCard({required this.todo, required this.ref});

  @override
  Widget build(BuildContext context) {
    final score = (todo['priority_score'] as num?)?.toDouble() ?? 0.5;
    final due = todo['due_date'] as String?;
    return Dismissible(
      key: Key(todo['id'] as String),
      background: Container(
        color: JarvisColors.green.withValues(alpha: 0.2),
        alignment: Alignment.centerLeft,
        padding: const EdgeInsets.only(left: 16),
        child: const Icon(Icons.check, color: JarvisColors.green),
      ),
      secondaryBackground: Container(
        color: JarvisColors.red.withValues(alpha: 0.2),
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.only(right: 16),
        child: const Icon(Icons.delete_outline, color: JarvisColors.red),
      ),
      confirmDismiss: (direction) async {
        if (direction == DismissDirection.startToEnd) {
          await ref.read(todosProvider.notifier).markDone(todo['id'] as String);
          return true;
        } else {
          await ref.read(todosProvider.notifier).delete(todo['id'] as String);
          return true;
        }
      },
      child: HudPanelCard(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(todo['title'] as String? ?? '',
                      style: JarvisText.chip.copyWith(
                          fontSize: 13, color: JarvisColors.cyanSoft)),
                ),
                const SizedBox(width: 8),
                if (due != null) HudChip('DUE', variant: ChipVariant.red),
              ],
            ),
            const SizedBox(height: 8),
            HudMeter(value: score),
            const SizedBox(height: 4),
            Text(
              '${(score * 100).toStringAsFixed(0)}% priority'
              '${due != null ? '  ·  $due' : ''}',
              style: JarvisText.chip.copyWith(color: JarvisColors.inkFaint),
            ),
          ],
        ),
      ),
    );
  }
}

class _AddTodoSheet extends ConsumerStatefulWidget {
  const _AddTodoSheet();
  @override ConsumerState<_AddTodoSheet> createState() => _AddTodoSheetState();
}

class _AddTodoSheetState extends ConsumerState<_AddTodoSheet> {
  final _ctrl = TextEditingController();

  @override
  Widget build(BuildContext context) => Padding(
    padding: EdgeInsets.only(
      left: 18, right: 18, top: 18,
      bottom: MediaQuery.of(context).viewInsets.bottom + 18,
    ),
    child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        TextField(
          controller: _ctrl,
          autofocus: true,
          style: const TextStyle(color: JarvisColors.ink, fontFamily: 'ShareTechMono'),
          decoration: const InputDecoration(
            hintText: 'Yeni görev başlığı…',
            hintStyle: TextStyle(color: JarvisColors.inkFaint),
            border: OutlineInputBorder(),
            focusedBorder: OutlineInputBorder(
              borderSide: BorderSide(color: JarvisColors.cyan),
            ),
          ),
        ),
        const SizedBox(height: 12),
        ElevatedButton(
          onPressed: () async {
            if (_ctrl.text.trim().isEmpty) return;
            await ref.read(todosProvider.notifier).add(_ctrl.text.trim());
            if (context.mounted) Navigator.pop(context);
          },
          style: ElevatedButton.styleFrom(
            backgroundColor: JarvisColors.cyan.withValues(alpha: 0.15),
            foregroundColor: JarvisColors.cyanSoft,
          ),
          child: const Text('Ekle'),
        ),
      ],
    ),
  );
}
