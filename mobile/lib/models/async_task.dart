class AsyncTask {
  final String taskId;
  final String userQuery;
  final String status; // queued | running | done | failed | cancelled
  final DateTime? createdAt;
  final DateTime? completedAt;
  final String? resultText;
  final List<Map<String, dynamic>> resultArtifacts;
  final String? error;
  final List<String> progressNotes;

  const AsyncTask({
    required this.taskId,
    required this.userQuery,
    required this.status,
    this.createdAt,
    this.completedAt,
    this.resultText,
    this.resultArtifacts = const [],
    this.error,
    this.progressNotes = const [],
  });

  factory AsyncTask.fromJson(Map<String, dynamic> j) => AsyncTask(
    taskId: j['task_id'] as String? ?? '',
    userQuery: j['user_query'] as String? ?? '',
    status: j['status'] as String? ?? 'queued',
    createdAt: j['created_at'] != null ? DateTime.tryParse(j['created_at'] as String) : null,
    completedAt: j['completed_at'] != null ? DateTime.tryParse(j['completed_at'] as String) : null,
    resultText: j['result_text'] as String?,
    resultArtifacts: List<Map<String, dynamic>>.from((j['result_artifacts'] as List?) ?? []),
    error: j['error'] as String?,
    progressNotes: List<String>.from((j['progress_notes'] as List?) ?? []),
  );

  bool get isDone => status == 'done';
  bool get isFailed => status == 'failed';
  bool get isRunning => status == 'running' || status == 'queued';

  AsyncTask copyWith({String? status, String? resultText, List<String>? progressNotes}) => AsyncTask(
    taskId: taskId,
    userQuery: userQuery,
    status: status ?? this.status,
    createdAt: createdAt,
    completedAt: completedAt,
    resultText: resultText ?? this.resultText,
    resultArtifacts: resultArtifacts,
    error: error,
    progressNotes: progressNotes ?? this.progressNotes,
  );
}
