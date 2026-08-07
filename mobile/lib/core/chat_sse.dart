/// chat_sse.dart -- classifies one raw SSE `data:` payload (as yielded by
/// ApiClient.chatStream()/uploadFileStream(), already stripped of the
/// "data: " prefix) into the structured frame vocabulary jarvis/api.py's
/// _sse_frames() emits.
///
/// Mirrors electron/src/renderer/src/lib/chatStream.js's feed() -- one
/// canonical classifier so chat_screen.dart does not re-implement ad hoc
/// string-prefix checks per frame kind. Before this file existed,
/// chat_screen.dart only special-cased `[DONE]`, `[ERROR]` and a literal
/// `{"async":` prefix; a `{"type":"progress",...}` or `{"type":"final_answer",
/// ...}` frame fell through and was appended to the visible assistant
/// message (and spoken via TTS) as raw JSON.
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

/// An L3 tool call is pending approval. Mobile has no approve/deny UI
/// (pre-existing, out of scope for the change that added this classifier) --
/// callers must still recognize this shape so it is not displayed/spoken as
/// raw JSON, same as every other structured frame here.
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
