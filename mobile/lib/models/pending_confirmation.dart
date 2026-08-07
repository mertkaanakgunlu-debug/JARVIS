/// pending_confirmation.dart -- an L3 approval prompt, reduced to what the
/// UI may actually show.
///
/// The wire payload is built by jarvis/graph/nodes.py's confirmation_node:
///
///     {"tools": [{"name", "args", "id", "description", "execution_id"}],
///      "count": <int>}
///
/// and reaches the phone two ways -- the `confirmation_required` SSE frame on
/// the turn's own stream, and the `confirmation_required` WS broadcast (which
/// also carries confirmations raised on OTHER transports, e.g. voice). Both
/// are parsed here so the two legs can never disagree about what a prompt
/// says.
///
/// `description` is the server's own plain-language one-liner
/// (jarvis/policy_guard.py's describe_call, documented "safe to read aloud or
/// print"). It is deliberately the ONLY human-facing field taken from the
/// payload: `args` is a raw tool-argument map, and falling back to it -- or to
/// the payload as JSON -- would put exactly the raw JSON on screen that this
/// whole classifier/model layer exists to keep off it. A tool with no usable
/// description degrades to its bare NAME, never to its arguments.
library;

class ConfirmationTool {
  /// The tool the graph is about to call ("gmail", "shell_run", ...).
  final String name;

  /// One-line plain-language description of the call, server-authored.
  /// Empty when the payload carried none -- render [label] instead of this.
  final String description;

  const ConfirmationTool({required this.name, required this.description});

  /// What to show for this call: the description when there is one, the tool
  /// name otherwise. Never arguments, never JSON.
  String get label => description.isNotEmpty ? description : name;

  static ConfirmationTool? _parse(Object? entry) {
    if (entry is! Map) return null;
    final name = (entry['name'] as String?)?.trim() ?? '';
    final description = (entry['description'] as String?)?.trim() ?? '';
    if (name.isEmpty && description.isEmpty) return null;
    return ConfirmationTool(name: name, description: description);
  }
}

class PendingConfirmation {
  /// The conf_id to POST back to /chat/confirm/{id}.
  final String id;
  final List<ConfirmationTool> tools;

  const PendingConfirmation({required this.id, required this.tools});

  /// Parse a payload into a prompt, or null when there is nothing safe to
  /// show. Null is a real outcome, not an error case to paper over: an empty
  /// id cannot be answered (there is no /chat/confirm/{id} to POST to), and a
  /// payload with no readable tool would render an approve button over a
  /// blank description -- asking the user to approve something the screen
  /// never named.
  static PendingConfirmation? fromPayload(String id, Map<String, dynamic> payload) {
    if (id.trim().isEmpty) return null;
    final raw = payload['tools'];
    if (raw is! List) return null;
    final tools = raw.map(ConfirmationTool._parse).nonNulls.toList(growable: false);
    if (tools.isEmpty) return null;
    return PendingConfirmation(id: id.trim(), tools: tools);
  }
}
