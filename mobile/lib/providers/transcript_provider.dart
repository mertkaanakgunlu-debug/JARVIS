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

  /// REPLACES the last JARVIS turn's text instead of appending to it.
  ///
  /// Completion-contract TTFB: the server can send a `final_answer` frame
  /// that supersedes whatever draft already streamed (a critic revision or
  /// verification repair) -- appendToLast would glue the correction onto the
  /// end of the text it corrects, producing a duplicated/garbled message.
  void replaceLast(String text) {
    if (state.isEmpty) return;
    final last = state.last;
    if (last.who != 'j') return;
    state = [
      ...state.sublist(0, state.length - 1),
      TranscriptTurn(who: 'j', text: text),
    ];
  }

  /// Drop the trailing JARVIS turn if nothing was ever streamed into it.
  ///
  /// A turn opens an empty 'j' bubble before the stream starts, so tokens
  /// have somewhere to land. A stream that ends without producing any text
  /// -- the L3 case: the graph interrupts for approval and says nothing --
  /// would otherwise leave a blank bubble sitting above the approval card.
  void removeLastIfEmpty() {
    if (state.isEmpty) return;
    final last = state.last;
    if (last.who != 'j' || last.text.isNotEmpty) return;
    state = state.sublist(0, state.length - 1);
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
