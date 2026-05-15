import 'api_client.dart';
import '../models/async_task.dart';

class TaskService {
  final ApiClient _api;

  TaskService(this._api);

  Future<AsyncTask> submit(String query, {bool forceAsync = false}) async {
    final resp = await _api.post<Map<String, dynamic>>(
      '/tasks',
      data: {'query': query, 'force_async': forceAsync},
    );
    return AsyncTask.fromJson(resp.data!);
  }

  Future<AsyncTask> get(String taskId) async {
    final resp = await _api.get<Map<String, dynamic>>('/tasks/$taskId');
    return AsyncTask.fromJson(resp.data!);
  }

  Future<List<AsyncTask>> list({String? status}) async {
    final resp = await _api.get<List<dynamic>>(
      '/tasks',
      params: status != null ? {'status': status} : null,
    );
    return (resp.data ?? [])
        .map((e) => AsyncTask.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<void> cancel(String taskId) =>
      _api.delete('/tasks/$taskId');
}
