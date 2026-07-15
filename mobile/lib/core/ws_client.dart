import 'dart:async';
import 'dart:convert';
import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../models/ws_event.dart';

class WsClient {
  final String _host;
  final String _apiKey;

  WebSocketChannel? _channel;
  StreamController<WsEvent> _controller = StreamController<WsEvent>.broadcast();
  bool _disposed = false;
  Timer? _reconnectTimer;

  // BUG-reconnect: this used to be a fixed 3s retry forever -- if the PC is
  // off for hours, that's a reconnect attempt every 3s the whole time.
  // Exponential backoff (reset on a successful connect) keeps quick recovery
  // for a brief drop without hammering the server/battery during a long one.
  static const Duration _baseReconnectDelay = Duration(seconds: 3);
  static const Duration _maxReconnectDelay = Duration(seconds: 60);
  Duration _reconnectDelay = _baseReconnectDelay;

  WsClient({required String host, required String apiKey})
      : _host = host, _apiKey = apiKey;

  Stream<WsEvent> get stream => _controller.stream;

  void connect() {
    if (_disposed || _host.isEmpty) return;
    // BUG-mob-tls: the token used to travel as a `?token=` query param --
    // visible in access logs, proxies, and OS/browser connection history.
    // IOWebSocketChannel (dart:io, Android-only -- fine, this app has no web
    // target) can set a real header on the upgrade request instead, same as
    // every other authenticated call this client makes. Server-side,
    // jarvis/api.py's ws_endpoint checks this header first, falling back to
    // the query param only for Electron (whose browser WebSocket API can't
    // set custom headers at all).
    final uri = Uri.parse('ws://$_host/ws');
    try {
      final channel = IOWebSocketChannel.connect(
        uri,
        headers: _apiKey.isNotEmpty ? {'X-API-Key': _apiKey} : null,
      );
      _channel = channel;
      // ignore: unawaited_futures
      channel.ready.then((_) {
        _reconnectDelay = _baseReconnectDelay;
      }).catchError((_) {
        // surfaced via the stream's onError below too; swallow here so it
        // doesn't also show up as an unhandled Future error.
      });
      channel.stream.listen(
        (data) {
          try {
            final j = jsonDecode(data as String) as Map<String, dynamic>;
            _controller.add(WsEvent.fromJson(j));
          } catch (_) {}
        },
        onDone: _scheduleReconnect,
        onError: (_) => _scheduleReconnect(),
        cancelOnError: true,
      );
    } catch (_) {
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    if (_disposed) return;
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(_reconnectDelay, connect);
    final next = _reconnectDelay * 2;
    _reconnectDelay = next > _maxReconnectDelay ? _maxReconnectDelay : next;
  }

  void close() {
    _disposed = true;
    _reconnectTimer?.cancel();
    _channel?.sink.close();
    _controller.close();
  }

  void reconnect(String host, String apiKey) {
    _channel?.sink.close();
    _reconnectTimer?.cancel();
    _reconnectDelay = _baseReconnectDelay;
    if (!_controller.hasListener || _controller.isClosed) {
      _controller = StreamController<WsEvent>.broadcast();
    }
    _disposed = false;
    connect();
  }
}
