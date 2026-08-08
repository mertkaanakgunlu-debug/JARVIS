/// chat_sse.dart -- classifies one raw SSE `data:` payload (as yielded by
/// ApiClient.chatStream()/uploadFileStream(), already stripped of the
/// "data: " prefix) into the structured frame vocabulary jarvis/api.py's
/// _sse_frames() emits.
///
/// Mirrors electron/src/renderer/src/lib/chatStream.js's feed() -- one
/// canonical classifier so no screen re-implements ad hoc string-prefix checks
/// per frame kind. A screen that only special-cases `[DONE]`, `[ERROR]` and a
/// literal `{"async":` prefix lets every other structured frame fall through
/// and be appended to the visible assistant message (and spoken via TTS) as
/// raw JSON.
///
/// That is not hypothetical. It was fixed once and then reintroduced by a
/// SECOND copy of the reader: home_screen.dart -- the app's only reachable
/// chat surface -- kept its own prefix loop, and a live L3 run on 2026-08-08
/// printed a `confirmation_required` frame into the transcript and spoke it
/// aloud, leaving a real approval prompt unanswerable. Both readers now go
/// through this function. If a new surface needs to read a chat stream, route
/// it here rather than adding a third loop.
library;

import 'dart:convert';

sealed class ChatSseEvent {
  const ChatSseEvent();
}

/// An ordinary text token/delta to append to the visible answer.
class ChatToken extends ChatSseEvent {
  final String text;
  const ChatToken(this.text);
}

/// Completion-contract TTFB: a contracted+enforce turn is buffering, so this
/// control signal -- not the answer -- is what arrives first. Must never be
/// appended to the visible answer or spoken as raw JSON.
class ChatProgress extends ChatSseEvent {
  final String phase;
  final String? kind;
  const ChatProgress(this.phase, this.kind);
}

/// The graph's authoritative terminal answer, when it differs from whatever
/// already streamed (a critic revision or verification repair). REPLACES the
/// visible answer -- appending would glue the correction onto the draft it
/// is correcting.
class ChatFinalAnswer extends ChatSseEvent {
  final String text;
  const ChatFinalAnswer(this.text);
}

/// An L3 tool call is pending approval. home_screen.dart turns this into the
/// approve/deny card (via PendingConfirmation + confirmationProvider) and ends
/// the stream; the graph stays interrupted server-side until it is answered.
/// Recognizing this shape is load-bearing twice over: rendering it as raw JSON
/// both leaks the payload and strands a prompt the user cannot answer.
class ChatConfirmationRequired extends ChatSseEvent {
  final String id;
  final Map<String, dynamic> payload;
  const ChatConfirmationRequired(this.id, this.payload);
}

/// The query was diverted to a background task instead of answered inline.
class ChatAsyncTask extends ChatSseEvent {
  final String? taskId;
  const ChatAsyncTask(this.taskId);
}

class ChatError extends ChatSseEvent {
  final String message;
  const ChatError(this.message);
}

class ChatDone extends ChatSseEvent {
  const ChatDone();
}

/// Classify one raw `data:` payload (WITHOUT the leading "data: " -- callers
/// strip that; see ApiClient.chatStream()). A payload that looks like JSON
/// but carries none of the known `type`s falls through as a plain token: a
/// real answer token can legitimately start with "{".
ChatSseEvent classifyChatChunk(String payload) {
  if (payload == '[DONE]') return const ChatDone();
  if (payload.startsWith('[ERROR]')) {
    return ChatError(payload.substring(7).trim());
  }
  if (payload.startsWith('{')) {
    Map<String, dynamic>? obj;
    try {
      final decoded = jsonDecode(payload);
      if (decoded is Map<String, dynamic>) obj = decoded;
    } catch (_) {
      obj = null;
    }
    if (obj != null) {
      final type = obj['type'];
      if (type == 'confirmation_required') {
        return ChatConfirmationRequired(
          (obj['id'] as String?) ?? '',
          (obj['payload'] as Map<String, dynamic>?) ?? const {},
        );
      }
      if (type == 'progress') {
        return ChatProgress(
          (obj['phase'] as String?) ?? '',
          obj['kind'] as String?,
        );
      }
      if (type == 'final_answer') {
        return ChatFinalAnswer((obj['text'] as String?) ?? '');
      }
      if (obj['async'] == true) {
        return ChatAsyncTask(obj['task_id'] as String?);
      }
    }
  }
  return ChatToken(payload.replaceAll(r'\n', '\n'));
}
