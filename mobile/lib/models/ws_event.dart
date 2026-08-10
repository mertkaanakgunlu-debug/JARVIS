import 'conversation_state.dart';

sealed class WsEvent {
  WsEvent();
  factory WsEvent.fromJson(Map<String, dynamic> j) {
    switch (j['type'] as String?) {
      case 'state':
        return StateEvent(value: ConversationState.parse(j['value'] as String?));
      case 'message':
        return MessageEvent(
          who: j['who'] as String? ?? 'j',
          text: j['text'] as String? ?? '',
        );
      case 'tool_call':
        return ToolCallEvent(
          kind: j['kind'] as String? ?? 'tool',
          body: j['body'] as String? ?? '',
        );
      case 'task':
        return TaskEvent(
          name: j['name'] as String? ?? '',
          steps: List<String>.from((j['steps'] as List?) ?? []),
        );
      case 'metrics':
        return MetricsEvent(
          cpu: (j['cpu'] as num?)?.toDouble() ?? 0,
          gpu: (j['gpu'] as num?)?.toDouble() ?? 0,
          ram: (j['ram'] as num?)?.toDouble() ?? 0,
          vram: (j['vram'] as num?)?.toDouble() ?? 0,
          latency: (j['latency'] as num?)?.toInt() ?? 0,
        );
      case 'calendar':
        return CalendarEvent(
          events: List<Map<String, dynamic>>.from((j['events'] as List?) ?? []),
        );
      case 'vault':
        return VaultEvent(
          entries: List<Map<String, dynamic>>.from((j['entries'] as List?) ?? []),
          count: (j['count'] as num?)?.toInt() ?? 0,
        );
      case 'progress':
        return ProgressEvent(
          jobsDone: (j['jobsDone'] as num?)?.toInt() ?? 0,
          jobsTotal: (j['jobsTotal'] as num?)?.toInt() ?? 0,
          runtime: j['runtime'] as String? ?? '',
          tokensIn: (j['tokensIn'] as num?)?.toInt() ?? 0,
          tokensOut: (j['tokensOut'] as num?)?.toInt() ?? 0,
        );
      case 'session':
        return SessionEvent(
          id: j['id'] as String? ?? '',
          topic: j['topic'] as String?,
        );
      // An L3 tool call is waiting for approval. Broadcast by jarvis/ws.py's
      // confirmation_required() for EVERY transport, so this leg also
      // delivers confirmations the phone did not initiate (a voice turn on
      // the PC), and delivers the ones it did if their own SSE stream was
      // interrupted before the frame arrived.
      case 'confirmation_required':
        return ConfirmationRequiredEvent(
          id: j['id'] as String? ?? '',
          payload: (j['payload'] as Map<String, dynamic>?) ?? const {},
          conversationId: j['conversation_id'] as String? ?? '',
          expiresInSeconds: (j['expires_in_seconds'] as num?)?.toInt(),
        );
      case 'confirmation_closed':
        return ConfirmationClosedEvent(id: j['id'] as String? ?? '');
      case 'task_status':
        return TaskStatusEvent(
          taskId: j['task_id'] as String? ?? '',
          status: j['status'] as String? ?? 'queued',
          progressNote: j['progress_note'] as String? ?? '',
          elapsedSec: (j['elapsed_sec'] as num?)?.toInt() ?? 0,
        );
      default:
        return UnknownEvent(raw: j);
    }
  }
}

class StateEvent      extends WsEvent { final ConversationState value; StateEvent({required this.value}); }
class MessageEvent    extends WsEvent { final String who, text; MessageEvent({required this.who, required this.text}); }
class ToolCallEvent   extends WsEvent { final String kind, body; ToolCallEvent({required this.kind, required this.body}); }
class TaskEvent       extends WsEvent { final String name; final List<String> steps; TaskEvent({required this.name, required this.steps}); }
class MetricsEvent    extends WsEvent { final double cpu, gpu, ram, vram; final int latency; MetricsEvent({required this.cpu, required this.gpu, required this.ram, required this.vram, required this.latency}); }
class CalendarEvent   extends WsEvent { final List<Map<String,dynamic>> events; CalendarEvent({required this.events}); }
class VaultEvent      extends WsEvent { final List<Map<String,dynamic>> entries; final int count; VaultEvent({required this.entries, required this.count}); }
class ProgressEvent   extends WsEvent { final int jobsDone, jobsTotal, tokensIn, tokensOut; final String runtime; ProgressEvent({required this.jobsDone, required this.jobsTotal, required this.runtime, required this.tokensIn, required this.tokensOut}); }
class SessionEvent    extends WsEvent { final String id; final String? topic; SessionEvent({required this.id, this.topic}); }
class TaskStatusEvent extends WsEvent { final String taskId, status, progressNote; final int elapsedSec; TaskStatusEvent({required this.taskId, required this.status, required this.progressNote, required this.elapsedSec}); }
class ConfirmationRequiredEvent extends WsEvent { final String id, conversationId; final int? expiresInSeconds; final Map<String,dynamic> payload; ConfirmationRequiredEvent({required this.id, required this.payload, this.conversationId = '', this.expiresInSeconds}); }
class ConfirmationClosedEvent extends WsEvent { final String id; ConfirmationClosedEvent({required this.id}); }
class UnknownEvent    extends WsEvent { final Map<String,dynamic> raw; UnknownEvent({required this.raw}); }
