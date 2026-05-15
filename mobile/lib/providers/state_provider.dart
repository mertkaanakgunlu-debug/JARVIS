import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/conversation_state.dart';
import '../models/ws_event.dart';
import 'ws_provider.dart';

final conversationStateProvider = StateProvider<ConversationState>(
  (_) => ConversationState.idle,
);

// Sync WS state events into conversationStateProvider
final stateListenerProvider = Provider<void>((ref) {
  ref.listen(wsStreamProvider, (_, next) {
    next.whenData((event) {
      if (event is StateEvent) {
        ref.read(conversationStateProvider.notifier).state = event.value;
      }
    });
  });
});
