import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../widgets/grid_background.dart';
import '../widgets/hud_chip.dart';
import '../widgets/hud_meter.dart';
import '../providers/tasks_provider.dart';
import '../providers/api_provider.dart';
import '../models/async_task.dart';

class TaskDetailScreen extends ConsumerWidget {
  final String taskId;
  const TaskDetailScreen({super.key, required this.taskId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tasksAsync = ref.watch(tasksProvider);
    final task = tasksAsync.valueOrNull?.firstWhere(
      (t) => t.taskId == taskId,
      orElse: () => AsyncTask(taskId: taskId, userQuery: '', status: 'unknown'),
    );

    return Scaffold(
      backgroundColor: JarvisColors.bg,
      body: GridBackground(
        child: SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(18, 12, 18, 0),
                child: Row(
                  children: [
                    GestureDetector(
                      onTap: () => Navigator.pop(context),
                      child: const Icon(Icons.arrow_back_ios,
                          color: JarvisColors.inkDim, size: 18),
                    ),
                    const SizedBox(width: 12),
                    Text('TASK DETAIL', style: JarvisText.wordmark),
                  ],
                ),
              ),
              if (task == null)
                const Expanded(child: Center(child: CircularProgressIndicator()))
              else
                Expanded(child: _TaskBody(task: task, ref: ref)),
            ],
          ),
        ),
      ),
    );
  }
}

class _TaskBody extends StatelessWidget {
  final AsyncTask task;
  final WidgetRef ref;
  const _TaskBody({required this.task, required this.ref});

  @override
  Widget build(BuildContext context) {
    final statusColor = task.isDone
        ? JarvisColors.green
        : task.isFailed
            ? JarvisColors.red
            : JarvisColors.amber;

    return ListView(
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 100),
      children: [
        // Query + status
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: statusColor.withOpacity(0.08),
            border: Border.all(color: statusColor.withOpacity(0.4)),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  HudChip(task.status.toUpperCase(),
                      variant: task.isDone
                          ? ChipVariant.cyan
                          : task.isFailed
                              ? ChipVariant.red
                              : ChipVariant.amber),
                  const SizedBox(width: 8),
                  if (task.completedAt != null)
                    Text(
                      task.completedAt!.toLocal().toString().substring(0, 16),
                      style: JarvisText.chip.copyWith(color: JarvisColors.inkFaint),
                    ),
                ],
              ),
              const SizedBox(height: 8),
              Text(task.userQuery, style: JarvisText.chip.copyWith(
                fontSize: 12, color: JarvisColors.ink)),
            ],
          ),
        ),
        const SizedBox(height: 16),

        // Result
        if (task.resultText != null) ...[
          Text('// SONUÇ', style: JarvisText.sectionHeader),
          const SizedBox(height: 8),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Colors.black.withOpacity(0.4),
              border: Border.all(color: JarvisColors.lineDim),
              borderRadius: BorderRadius.circular(8),
            ),
            child: SelectableText(task.resultText!,
                style: JarvisText.chatBody.copyWith(
                    color: JarvisColors.ink, fontSize: 12)),
          ),
          const SizedBox(height: 16),
        ],

        // Error
        if (task.error != null) ...[
          Text('// HATA', style: JarvisText.sectionHeader.copyWith(
              color: JarvisColors.red)),
          const SizedBox(height: 8),
          Text(task.error!, style: JarvisText.chip.copyWith(
              color: JarvisColors.red)),
          const SizedBox(height: 16),
        ],

        // Artifacts
        if (task.resultArtifacts.isNotEmpty) ...[
          Text('// DOSYALAR', style: JarvisText.sectionHeader),
          const SizedBox(height: 8),
          ...task.resultArtifacts.map((art) => Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: _ArtifactCard(artifact: art),
          )),
          const SizedBox(height: 8),
        ],

        // Progress notes
        if (task.progressNotes.isNotEmpty) ...[
          Text('// PROGRESS LOG', style: JarvisText.sectionHeader),
          const SizedBox(height: 8),
          ...task.progressNotes.asMap().entries.map((e) => Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('${e.key + 1}. ',
                    style: JarvisText.chip.copyWith(color: JarvisColors.inkFaint)),
                Expanded(
                  child: Text(e.value,
                      style: JarvisText.chip.copyWith(color: JarvisColors.inkDim)),
                ),
              ],
            ),
          )),
        ],

        // Cancel button (running tasks)
        if (task.isRunning) ...[
          const SizedBox(height: 16),
          OutlinedButton.icon(
            onPressed: () async {
              final api = ref.read(apiClientProvider);
              if (api != null) {
                await api.delete('/tasks/${task.taskId}');
                ref.read(tasksProvider.notifier).refresh();
              }
              if (context.mounted) Navigator.pop(context);
            },
            icon: const Icon(Icons.cancel_outlined, color: JarvisColors.red, size: 16),
            label: const Text('İptal et', style: TextStyle(color: JarvisColors.red)),
            style: OutlinedButton.styleFrom(
              side: const BorderSide(color: JarvisColors.red),
            ),
          ),
        ],
      ],
    );
  }
}

class _ArtifactCard extends StatelessWidget {
  final Map<String, dynamic> artifact;
  const _ArtifactCard({required this.artifact});

  @override
  Widget build(BuildContext context) {
    final name = artifact['name'] as String? ?? '';
    final type = artifact['type'] as String? ?? '';
    final driveLink = artifact['drive_link'] as String?;

    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: JarvisColors.cyan.withOpacity(0.06),
        border: Border.all(color: JarvisColors.lineDim),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        children: [
          Icon(_iconForType(type), color: JarvisColors.cyanSoft, size: 18),
          const SizedBox(width: 8),
          Expanded(
            child: Text(name, style: JarvisText.chip.copyWith(
                fontSize: 11, color: JarvisColors.ink)),
          ),
          if (driveLink != null)
            GestureDetector(
              onTap: () => launchUrl(Uri.parse(driveLink)),
              child: const HudChip('Drive', variant: ChipVariant.cyan),
            ),
        ],
      ),
    );
  }

  IconData _iconForType(String type) {
    switch (type.toLowerCase()) {
      case 'pdf': return Icons.picture_as_pdf_outlined;
      case 'png': case 'svg': return Icons.image_outlined;
      case 'html': return Icons.web_outlined;
      default: return Icons.insert_drive_file_outlined;
    }
  }
}
