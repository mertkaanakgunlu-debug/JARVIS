// ws_event_test.dart -- the WS frames the confirmation round-trip depends on.
//
// The confirmation prompt has two legs: the turn's own SSE stream and this
// broadcast. The WS leg is what recovers a confirmation whose SSE stream died
// before the frame arrived, and the only leg that delivers one raised on
// another transport (a voice turn on the PC) -- so it must not fall through
// to UnknownEvent the way it did before mobile could answer a confirmation.
//
// Deliberately plain `test()`, never `testWidgets()`: WsEvent.fromJson is a
// pure parser. See test/chat_sse_test.dart's header for the MOBILE-TEST-01
// reasoning this follows.
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/models/ws_event.dart';

void main() {
  group('WsEvent.fromJson — confirmation_required', () {
    test('parses id and payload', () {
      final event = WsEvent.fromJson({
        'type': 'confirmation_required',
        'id': 'conf-7',
        'payload': {
          'tools': [
            {'name': 'gmail', 'description': 'send an email'},
          ],
          'count': 1,
        },
      });
      expect(event, isA<ConfirmationRequiredEvent>());
      final c = event as ConfirmationRequiredEvent;
      expect(c.id, 'conf-7');
      expect((c.payload['tools'] as List).length, 1);
    });

    test('a missing payload degrades to empty rather than throwing', () {
      final event = WsEvent.fromJson({'type': 'confirmation_required', 'id': 'c-1'});
      expect((event as ConfirmationRequiredEvent).payload, isEmpty);
      // PendingConfirmation.fromPayload turns this into "no prompt" -- the
      // dispatcher checks for null, so an unreadable broadcast is dropped
      // rather than rendered as an unanswerable card.
    });

    test('an unrelated frame is still UnknownEvent', () {
      expect(WsEvent.fromJson({'type': 'nope'}), isA<UnknownEvent>());
    });
  });
}
