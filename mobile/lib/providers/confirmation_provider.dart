/// confirmation_provider.dart -- the phone's single source of truth for "is
/// an L3 tool call waiting on this user right now?".
///
/// Held in a provider rather than in the chat screen's own State because the
/// prompt must outlive the widget that received it: the SSE stream that
/// carried it ends immediately afterwards (the graph is interrupted
/// server-side, TTL-bound), so a rebuild, a tab switch or a dropped
/// connection would otherwise strand the interrupt with no way to answer it.
/// It is fed from BOTH legs for the same reason -- the turn's own SSE frame
/// and the WS broadcast (which also delivers confirmations raised on another
/// transport). The two can deliver the SAME id; [raise] is idempotent so no
/// dedup machinery is needed at either call site.
///
/// The submit guard lives here, not on the buttons. A disabled button is a
/// paint-time property of one widget; whether this specific interrupt has
/// already been answered is a fact about the turn, and both legs plus every
/// future call site have to honour it.
library;

import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/pending_confirmation.dart';

class ConfirmationState {
  final PendingConfirmation? pending;

  /// A decision for [pending] is in flight. Blocks a second POST for the same
  /// interrupt -- approving twice is not idempotent from here: the first POST
  /// claims the confirmation (JarvisAgent.claim_pending_confirmation pops it),
  /// so the second would resume nothing and come back as "expired or not
  /// found", overwriting the real continuation with a false failure.
  final bool submitting;

  const ConfirmationState({this.pending, this.submitting = false});

  bool get isPending => pending != null;
}

class ConfirmationNotifier extends StateNotifier<ConfirmationState> {
  ConfirmationNotifier() : super(const ConfirmationState());

  Timer? _expiryTimer;

  /// A confirmation arrived. Idempotent on id: re-delivery of the one already
  /// on screen (the SSE frame and the WS broadcast both fire for a
  /// phone-initiated turn) must not reset an in-flight submit.
  void raise(PendingConfirmation confirmation) {
    if (state.pending?.id == confirmation.id) return;
    // A DIFFERENT id replaces the current prompt and clears `submitting`:
    // this is the second-same-turn-interrupt case, where the POST that is
    // finishing belongs to the id being replaced. [resolved] is id-checked
    // precisely so that finishing POST cannot then clear this new prompt.
    _expiryTimer?.cancel();
    state = ConfirmationState(pending: confirmation);
    final ttl = confirmation.expiresInSeconds;
    if (ttl != null) {
      if (ttl <= 0) {
        resolved(confirmation.id);
      } else {
        _expiryTimer = Timer(
          Duration(seconds: ttl),
          () => resolved(confirmation.id),
        );
      }
    }
  }

  /// Claim the right to POST a decision for [id]. False means "do not send":
  /// either a decision is already in flight (double tap, or both buttons hit
  /// in the same frame), or [id] is no longer the prompt on screen (a stale
  /// tap on a card that has since been replaced or answered).
  bool beginSubmit(String id) {
    if (state.submitting) return false;
    final pending = state.pending;
    if (pending == null || pending.id != id) return false;
    state = ConfirmationState(pending: pending, submitting: true);
    return true;
  }

  /// The POST for [id] completed. Clears the prompt only if it is still the
  /// one that was answered -- a second interrupt raised mid-stream keeps its
  /// own prompt up.
  void resolved(String id) {
    if (state.pending?.id == id) {
      _expiryTimer?.cancel();
      _expiryTimer = null;
      state = const ConfirmationState();
      return;
    }
    state = ConfirmationState(pending: state.pending);
  }

  /// The POST failed to reach a verdict (transport error, not a server
  /// answer). The prompt STAYS: the confirmation may still be alive
  /// server-side until its TTL, so the user can retry or deny. Only the
  /// in-flight flag is released.
  void failed() {
    state = ConfirmationState(pending: state.pending);
  }

  @override
  void dispose() {
    _expiryTimer?.cancel();
    super.dispose();
  }
}

final confirmationProvider =
    StateNotifierProvider<ConfirmationNotifier, ConfirmationState>(
  (_) => ConfirmationNotifier(),
);
