import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/ws_client.dart';
import '../models/ws_event.dart';
import 'api_provider.dart';
import 'settings_provider.dart';

final wsClientProvider = Provider<WsClient>((ref) {
  final settings = ref.watch(settingsSyncProvider);
  final client = WsClient(host: settings.host, apiKey: settings.apiKey);
  client.connect();
  ref.onDispose(client.close);
  return client;
});

final wsStreamProvider = StreamProvider<WsEvent>((ref) {
  final client = ref.watch(wsClientProvider);
  return client.stream;
});

// Specific filtered providers
final metricsProvider = StateProvider<MetricsEvent?>((ref) => null);
final calendarWsProvider = StateProvider<List<Map<String, dynamic>>>((ref) => []);
final vaultCountProvider = StateProvider<int>((ref) => 0);
final progressProvider = StateProvider<ProgressEvent?>((ref) => null);

// Vault count via REST fallback (turns + docs)
final vaultCountRestProvider = FutureProvider<int>((ref) async {
  final api = ref.watch(apiClientProvider);
  if (api == null) return 0;
  try {
    final resp = await api.get<Map<String, dynamic>>('/vault/count');
    final data = resp.data ?? {};
    final docs  = (data['docs']  as num?)?.toInt() ?? 0;
    final turns = (data['turns'] as num?)?.toInt() ?? 0;
    return docs + turns;
  } catch (_) {
    return 0;
  }
});

// Listen to WS stream and dispatch events to state providers
final wsDispatcherProvider = Provider<void>((ref) {
  // Seed vault count from REST on startup
  ref.listen(vaultCountRestProvider, (_, next) {
    next.whenData((count) {
      if (count > 0) ref.read(vaultCountProvider.notifier).state = count;
    });
  });

  ref.listen(wsStreamProvider, (_, next) {
    next.whenData((event) {
      if (event is MetricsEvent) {
        ref.read(metricsProvider.notifier).state = event;
      } else if (event is CalendarEvent) {
        ref.read(calendarWsProvider.notifier).state = event.events;
      } else if (event is VaultEvent) {
        ref.read(vaultCountProvider.notifier).state = event.count;
      } else if (event is ProgressEvent) {
        ref.read(progressProvider.notifier).state = event;
      }
    });
  });
});
