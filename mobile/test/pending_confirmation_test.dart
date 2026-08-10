// pending_confirmation_test.dart -- payload -> displayable prompt.
//
// Deliberately plain `test()`, never `testWidgets()`: PendingConfirmation is
// a pure parser with no widget tree, so this suite does not touch
// MOBILE-TEST-01 (lib/app.dart's splash timer, which only fires when a
// widget is pumped). Confirmed by running this file alone:
// `flutter test test/pending_confirmation_test.dart`.
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/models/pending_confirmation.dart';

/// One confirmable call as jarvis/graph/nodes.py's confirmation_node builds
/// it -- `args` included, because keeping it OUT of the UI is the point.
Map<String, dynamic> _tool({
  String name = 'gmail',
  String description = 'send an email (to=baran@example.com)',
}) =>
    {
      'name': name,
      'description': description,
      'args': {'action': 'send', 'to': 'baran@example.com', 'body': 'gizli'},
      'id': 'call-1',
      'execution_id': 'exec-1',
    };

void main() {
  group('PendingConfirmation.fromPayload', () {
    test('reads the id and the server-authored descriptions', () {
      final pending = PendingConfirmation.fromPayload(
        'conf-1',
        {'tools': [_tool()], 'count': 1},
        conversationId: 'conv-mobile',
        expiresInSeconds: 300,
      );
      expect(pending, isNotNull);
      expect(pending!.id, 'conf-1');
      expect(pending.conversationId, 'conv-mobile');
      expect(pending.expiresInSeconds, 300);
      expect(pending.tools.single.label, 'send an email (to=baran@example.com)');
    });

    test('a tool with no description degrades to its NAME, never its args', () {
      // The regression this pins: the whole reason chat_sse.dart and this
      // model exist is that structured payloads must not reach the screen as
      // raw JSON. A fallback to `args` would reintroduce exactly that, and
      // would leak message bodies onto a lock screen alongside it.
      final pending = PendingConfirmation.fromPayload(
        'conf-1',
        {'tools': [_tool(description: '')], 'count': 1},
      );
      expect(pending!.tools.single.label, 'gmail');
      expect(pending.tools.single.label, isNot(contains('gizli')));
      expect(pending.tools.single.label, isNot(contains('{')));
    });

    test('keeps every confirmable call in a multi-tool batch', () {
      final pending = PendingConfirmation.fromPayload('conf-1', {
        'tools': [
          _tool(name: 'gmail', description: 'send an email'),
          _tool(name: 'shell_run', description: 'run the shell command: rm -rf x'),
        ],
        'count': 2,
      });
      expect(pending!.tools.map((t) => t.label), [
        'send an email',
        'run the shell command: rm -rf x',
      ]);
    });

    test('an empty id is unanswerable, so there is no prompt', () {
      // There is no /chat/confirm/{id} to POST to -- rendering an approve
      // button here would offer the user a decision the app cannot deliver.
      expect(PendingConfirmation.fromPayload('', {'tools': [_tool()]}), isNull);
      expect(PendingConfirmation.fromPayload('   ', {'tools': [_tool()]}), isNull);
    });

    test('a payload naming no readable tool yields no prompt', () {
      // Approving something the screen never named is worse than showing the
      // "answer it on the PC" note the caller falls back to.
      expect(PendingConfirmation.fromPayload('c-1', const {}), isNull);
      expect(PendingConfirmation.fromPayload('c-1', {'tools': []}), isNull);
      expect(PendingConfirmation.fromPayload('c-1', {'tools': 'nope'}), isNull);
      expect(
        PendingConfirmation.fromPayload('c-1', {
          'tools': [
            {'args': {'a': 1}},
          ],
        }),
        isNull,
      );
    });

    test('an unreadable entry is skipped without losing its siblings', () {
      final pending = PendingConfirmation.fromPayload('c-1', {
        'tools': ['junk', null, _tool(name: 'gmail', description: 'send an email')],
      });
      expect(pending!.tools.single.label, 'send an email');
    });
  });
}
