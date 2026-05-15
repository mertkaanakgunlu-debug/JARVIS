import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'api_provider.dart';

final financeSummaryProvider = FutureProvider<Map<String, dynamic>>((ref) async {
  final api = ref.read(apiClientProvider);
  if (api == null) return {};
  final resp = await api.get<Map<String, dynamic>>('/finance/summary');
  return resp.data ?? {};
});

final financeBudgetProvider = FutureProvider<List<Map<String, dynamic>>>((ref) async {
  final api = ref.read(apiClientProvider);
  if (api == null) return [];
  final resp = await api.get<List<dynamic>>('/finance/budget_status');
  return (resp.data ?? []).cast<Map<String, dynamic>>();
});

final financeRecentProvider = FutureProvider<List<Map<String, dynamic>>>((ref) async {
  final api = ref.read(apiClientProvider);
  if (api == null) return [];
  final resp = await api.get<List<dynamic>>('/finance/recent');
  return (resp.data ?? []).cast<Map<String, dynamic>>();
});

final financeTopCategoriesProvider = FutureProvider<List<Map<String, dynamic>>>((ref) async {
  final api = ref.read(apiClientProvider);
  if (api == null) return [];
  final resp = await api.get<List<dynamic>>('/finance/top_categories');
  return (resp.data ?? []).cast<Map<String, dynamic>>();
});
