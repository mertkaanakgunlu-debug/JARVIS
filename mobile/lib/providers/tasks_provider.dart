import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/async_task.dart';
import '../models/ws_event.dart';
import 'api_provider.dart';
import 'ws_provider.dart';

final tasksProvider = AsyncNotifierProvider<TasksNotifier, List<AsyncTask>>(
  TasksNotifier.new,
);

class TasksNotifier extends AsyncNotifier<List<AsyncTask>> {
  @override
  Future<List<AsyncTask>> build() async {
    // Listen for task_status WS events and update in real time
    ref.listen(wsStreamProvider, (_, next) {
      next.whenData((event) {
        if (event is TaskStatusEvent) {
          _updateFromWs(event);
        }
      });
    });
    return _fetch();
  }

  Future<List<AsyncTask>> _fetch() async {
    final api = ref.read(apiClientProvider);
    if (api == null) return [];
    final resp = await api.get<List<dynamic>>('/tasks');
    return (resp.data ?? [])
        .map((e) => AsyncTask.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  void _updateFromWs(TaskStatusEvent event) {
    state.whenData((tasks) {
      final idx = tasks.indexWhere((t) => t.taskId == event.taskId);
      if (idx == -1) {
        refresh();
        return;
      }
      final updated = tasks[idx].copyWith(
        status: event.status,
        progressNotes: [...tasks[idx].progressNotes, if (event.progressNote.isNotEmpty) event.progressNote],
      );
      final newList = [...tasks];
      newList[idx] = updated;
      state = AsyncValue.data(newList);
    });
  }

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }
}

final connectivityProvider = StateProvider<String>((ref) => 'unknown');
// Values: 'online' | 'sleeping' | 'unreachable' | 'unknown'
