import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'api_provider.dart';

final todosProvider = AsyncNotifierProvider<TodosNotifier, List<Map<String, dynamic>>>(
  TodosNotifier.new,
);

class TodosNotifier extends AsyncNotifier<List<Map<String, dynamic>>> {
  @override
  Future<List<Map<String, dynamic>>> build() async {
    final api = ref.read(apiClientProvider);
    if (api == null) return [];
    final resp = await api.get<List<dynamic>>('/todos/');
    return (resp.data ?? []).cast<Map<String, dynamic>>();
  }

  Future<void> refresh() => update((_) => build());

  Future<void> markDone(String id) async {
    final api = ref.read(apiClientProvider);
    if (api == null) return;
    await api.post('/todos/$id/done');
    refresh();
  }

  Future<void> delete(String id) async {
    final api = ref.read(apiClientProvider);
    if (api == null) return;
    await api.delete('/todos/$id');
    refresh();
  }

  Future<void> add(String title, {String description = '', String dueDate = ''}) async {
    final api = ref.read(apiClientProvider);
    if (api == null) return;
    await api.post('/todos/', data: {
      'title': title,
      'description': description,
      'due_date': dueDate,
    });
    refresh();
  }
}
