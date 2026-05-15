import 'dart:async';
import 'dart:convert';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../models/ws_event.dart';

class WsClient {
  final String _host;
  final String _apiKey;

  WebSocketChannel? _channel;
  StreamController<WsEvent> _controller = StreamController<WsEvent>.broadcast();
  bool _disposed = false;
  Timer? _reconnectTimer;

  WsClient({required String host, required String apiKey})
      : _host = host, _apiKey = apiKey;

  Stream<WsEvent> get stream => _controller.stream;

  void connect() {
    if (_disposed || _host.isEmpty) return;
    final token = _apiKey.isNotEmpty ? '?token=$_apiKey' : '';
    final uri = Uri.parse('ws://$_host/ws$token');
    try {
      _channel = WebSocketChannel.connect(uri);
      _channel!.stream.listen(
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
    _reconnectTimer = Timer(const Duration(seconds: 3), connect);
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
    if (!_controller.hasListener || _controller.isClosed) {
      _controller = StreamController<WsEvent>.broadcast();
    }
    _disposed = false;
    connect();
  }
}
