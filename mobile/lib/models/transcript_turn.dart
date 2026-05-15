class TranscriptTurn {
  final String who;   // 'u' (user) or 'j' (JARVIS)
  final String text;
  final bool isAsync;
  final String? taskId;

  const TranscriptTurn({
    required this.who,
    required this.text,
    this.isAsync = false,
    this.taskId,
  });
}
