// confirmation_provider_test.dart -- the approve/deny state machine.
//
// The three hazards this file exists for are the ones a widget test would
// not reach reliably: a double submit, a stale tap on a prompt that has
// already been swapped, and a transport failure that must NOT throw away a
// confirmation still alive server-side.
//
// Deliberately plain `test()`, never `testWidgets()`: ConfirmationNotifier is
// a StateNotifier with no widget tree, so this suite does not touch
// MOBILE-TEST-01 (lib/app.dart's splash timer). Confirmed by running this
// file alone: `flutter test test/confirmation_provider_test.dart`.
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/models/pending_confirmation.dart';
import 'package:jarvis_mobile/providers/confirmation_provider.dart';

PendingConfirmation _conf(String id) => PendingConfirmation(
      id: id,
      tools: const [ConfirmationTool(name: 'gmail', description: 'send an email')],
    );

void main() {
  group('ConfirmationNotifier', () {
    test('raise puts a prompt up', () {
      final n = ConfirmationNotifier();
      expect(n.state.isPending, isFalse);
      n.raise(_conf('c-1'));
      expect(n.state.pending!.id, 'c-1');
      expect(n.state.submitting, isFalse);
    });

    test('re-raising the SAME id does not disturb an in-flight submit', () {
      // Both legs deliver a phone-initiated prompt: the turn's own SSE frame
      // and the WS broadcast. If the second delivery reset `submitting`, the
      // approve button would go live again mid-POST and the user could send
      // a second decision for an interrupt already claimed server-side.
      final n = ConfirmationNotifier();
      n.raise(_conf('c-1'));
      expect(n.beginSubmit('c-1'), isTrue);

      n.raise(_conf('c-1'));

      expect(n.state.submitting, isTrue);
      expect(n.beginSubmit('c-1'), isFalse);
    });

    // ── double submit ────────────────────────────────────────────────────────

    test('only the first beginSubmit for an id wins', () {
      final n = ConfirmationNotifier();
      n.raise(_conf('c-1'));
      expect(n.beginSubmit('c-1'), isTrue);
      // A double tap, or approve and deny hit in the same frame. Approving
      // twice is not idempotent: the first POST pops the confirmation
      // server-side (claim_pending_confirmation), so the second would resume
      // nothing and answer "expired or not found".
      expect(n.beginSubmit('c-1'), isFalse);
    });

    test('a tap for an id that is no longer on screen is refused', () {
      final n = ConfirmationNotifier();
      n.raise(_conf('c-1'));
      n.raise(_conf('c-2')); // second same-turn interrupt swapped the card
      expect(n.beginSubmit('c-1'), isFalse);
      expect(n.beginSubmit('c-2'), isTrue);
    });

    test('beginSubmit on an empty state is refused', () {
      expect(ConfirmationNotifier().beginSubmit('c-1'), isFalse);
    });

    // ── resolution ───────────────────────────────────────────────────────────

    test('resolved clears the prompt it answered', () {
      final n = ConfirmationNotifier();
      n.raise(_conf('c-1'));
      n.beginSubmit('c-1');
      n.resolved('c-1');
      expect(n.state.isPending, isFalse);
      expect(n.state.submitting, isFalse);
    });

    test('resolving the OLD id leaves a second interrupt standing', () {
      // The continuation of an approved call can interrupt again. That new
      // prompt arrives while the first POST is still draining its stream, so
      // the resolution that follows must not clear it -- the graph would be
      // left interrupted with nothing on screen to answer it.
      final n = ConfirmationNotifier();
      n.raise(_conf('c-1'));
      n.beginSubmit('c-1');
      n.raise(_conf('c-2')); // raised mid-stream
      n.resolved('c-1');
      expect(n.state.pending!.id, 'c-2');
      expect(n.state.submitting, isFalse, reason: 'the new card must be tappable');
      expect(n.beginSubmit('c-2'), isTrue);
    });

    // ── transport failure ────────────────────────────────────────────────────

    test('failed keeps the prompt and releases the button', () {
      // A dropped connection is not a verdict. The interrupt may still be
      // alive server-side until its TTL, so the user must be able to retry
      // or deny rather than be left with a paused graph and no card.
      final n = ConfirmationNotifier();
      n.raise(_conf('c-1'));
      n.beginSubmit('c-1');
      n.failed();
      expect(n.state.pending!.id, 'c-1');
      expect(n.state.submitting, isFalse);
      expect(n.beginSubmit('c-1'), isTrue, reason: 'retry must be possible');
    });
  });
}
