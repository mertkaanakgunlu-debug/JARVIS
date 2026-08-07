// chat_sse_test.dart -- pure unit tests for classifyChatChunk(), the mobile
// counterpart of electron/src/renderer/src/lib/chatStream.test.js.
//
// Deliberately plain `test()`, never `testWidgets()`: classifyChatChunk() is
// a pure String -> ChatSseEvent function with no widget tree, so this suite
// does not touch MOBILE-TEST-01 (lib/app.dart's uncancelled splash timer,
// which only fires when a widget is pumped). Confirmed by running this file
// alone: `flutter test test/chat_sse_test.dart`.
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/chat_sse.dart';

void main() {
  group('classifyChatChunk', () {
    test('plain text is a ChatToken, with \\n unescaped', () {
      final event = classifyChatChunk(r'Merhaba d\nnya\nikinci satir');
      expect(event, isA<ChatToken>());
      expect((event as ChatToken).text, 'Merhaba d\nnya\nikinci satir');
    });

    test('[DONE] is ChatDone', () {
      expect(classifyChatChunk('[DONE]'), isA<ChatDone>());
    });

    test('[ERROR] frame trims the leading space', () {
      final event = classifyChatChunk('[ERROR] boom');
      expect(event, isA<ChatError>());
      expect((event as ChatError).message, 'boom');
    });

    test('an async-divert frame is ChatAsyncTask', () {
      final event = classifyChatChunk('{"async": true, "task_id": "t-9"}');
      expect(event, isA<ChatAsyncTask>());
      expect((event as ChatAsyncTask).taskId, 't-9');
    });

    // ── completion-contract TTFB: the two new frame kinds ──────────────────

    test('a progress frame with a kind classifies as ChatProgress', () {
      final event = classifyChatChunk(
        '{"type":"progress","phase":"preparing_required_output","kind":"chart"}',
      );
      expect(event, isA<ChatProgress>());
      final p = event as ChatProgress;
      expect(p.phase, 'preparing_required_output');
      expect(p.kind, 'chart');
    });

    test('a progress frame with no kind leaves kind null', () {
      final event = classifyChatChunk(
        '{"type":"progress","phase":"resuming_required_output"}',
      );
      expect(event, isA<ChatProgress>());
      expect((event as ChatProgress).kind, isNull);
    });

    test('a final_answer frame classifies as ChatFinalAnswer, not a token', () {
      // The regression this pins: before classifyChatChunk existed, this
      // exact frame fell through to the plain-text branch and was appended
      // to the visible assistant message as literal JSON.
      final event = classifyChatChunk(
        '{"type":"final_answer","text":"gerçek cevap"}',
      );
      expect(event, isA<ChatFinalAnswer>());
      expect((event as ChatFinalAnswer).text, 'gerçek cevap');
    });

    test('confirmation_required still classifies correctly alongside the new kinds', () {
      final event = classifyChatChunk(
        '{"type":"confirmation_required","id":"c-1","payload":{"tools":[]}}',
      );
      expect(event, isA<ChatConfirmationRequired>());
      final c = event as ChatConfirmationRequired;
      expect(c.id, 'c-1');
      expect(c.payload, {'tools': []});
    });

    test('a JSON-lookalike token with no known type is plain text', () {
      final event = classifyChatChunk('{"foo": 1}');
      expect(event, isA<ChatToken>());
      expect((event as ChatToken).text, '{"foo": 1}');
    });

    test('malformed JSON starting with { falls through as plain text', () {
      final event = classifyChatChunk('{not valid json');
      expect(event, isA<ChatToken>());
      expect((event as ChatToken).text, '{not valid json');
    });
  });
}
