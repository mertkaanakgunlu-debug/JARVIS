import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api_client.dart';
import 'settings_provider.dart';

final apiClientProvider = Provider<ApiClient?>((ref) {
  final settings = ref.watch(settingsSyncProvider);
  if (settings.host.isEmpty) return null;
  return ApiClient(host: settings.host, apiKey: settings.apiKey);
});
