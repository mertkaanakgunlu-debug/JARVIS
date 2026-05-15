import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/transcript_turn.dart';
import '../models/ws_event.dart';
import 'ws_provider.dart';

final transcriptProvider =
    StateNotifierProvider<TranscriptNotifier, List<TranscriptTurn>>(
  (_) => TranscriptNotifier(),
);

class TranscriptNotifier extends StateNotifier<List<TranscriptTurn>> {
  TranscriptNotifier() : super([]);

  void add(TranscriptTurn turn) {
    state = [...state, turn];
  }

  void appendToLast(String delta) {
    if (state.isEmpty) return;
    final last = state.last;
    if (last.who != 'j') return;
    state = [
      ...state.sublist(0, state.length - 1),
      TranscriptTurn(who: 'j', text: last.text + delta),
    ];
  }

  void clear() => state = [];
}

// Sync WS message events into transcriptProvider
final transcriptListenerProvider = Provider<void>((ref) {
  ref.listen(wsStreamProvider, (_, next) {
    next.whenData((event) {
      if (event is MessageEvent) {
        ref.read(transcriptProvider.notifier).add(
          TranscriptTurn(who: event.who, text: event.text),
        );
      }
    });
  });
});
