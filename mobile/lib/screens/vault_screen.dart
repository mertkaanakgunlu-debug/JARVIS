import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../widgets/grid_background.dart';
import '../widgets/hud_chip.dart';
import '../widgets/hud_panel_card.dart';
import '../providers/api_provider.dart';
import '../providers/ws_provider.dart';

final _vaultRecentProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  final api = ref.read(apiClientProvider);
  if (api == null) return [];
  final resp = await api.get<List<dynamic>>('/vault/recent?n=40');
  return (resp.data ?? []).cast<Map<String, dynamic>>();
});

class VaultScreen extends ConsumerStatefulWidget {
  const VaultScreen({super.key});
  @override ConsumerState<VaultScreen> createState() => _VaultScreenState();
}

class _VaultScreenState extends ConsumerState<VaultScreen> {
  String _filter = '';
  String _tagFilter = '';

  @override
  Widget build(BuildContext context) {
    final count = ref.watch(vaultCountProvider);
    final entriesAsync = ref.watch(_vaultRecentProvider);

    return GridBackground(
      child: SafeArea(
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(18, 12, 18, 0),
              child: Row(
                children: [
                  Text('VAULT', style: JarvisText.wordmark),
                  const Spacer(),
                  Text('$count vectors',
                      style: JarvisText.chip.copyWith(color: JarvisColors.inkFaint)),
                ],
              ),
            ),
            // Tag filter chips
            Padding(
              padding: const EdgeInsets.fromLTRB(18, 8, 18, 0),
              child: SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: ['', 'NOTE', 'CONVO', 'MEMORY', 'REPORT'].map((tag) {
                    final active = _tagFilter == tag;
                    return Padding(
                      padding: const EdgeInsets.only(right: 6),
                      child: GestureDetector(
                        onTap: () => setState(() => _tagFilter = tag),
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 8, vertical: 3),
                          decoration: BoxDecoration(
                            color: active
                                ? JarvisColors.cyan.withValues(alpha: 0.2)
                                : Colors.transparent,
                            border: Border.all(
                              color: active
                                  ? JarvisColors.cyan
                                  : JarvisColors.lineDim,
                            ),
                            borderRadius: BorderRadius.circular(3),
                          ),
                          child: Text(
                            tag.isEmpty ? 'ALL' : tag,
                            style: JarvisText.chip.copyWith(
                              color: active
                                  ? JarvisColors.cyanSoft
                                  : JarvisColors.inkDim,
                            ),
                          ),
                        ),
                      ),
                    );
                  }).toList(),
                ),
              ),
            ),
            // Search
            Padding(
              padding: const EdgeInsets.fromLTRB(18, 8, 18, 0),
              child: TextField(
                onChanged: (v) => setState(() => _filter = v.toLowerCase()),
                style: const TextStyle(
                    color: JarvisColors.ink, fontFamily: 'ShareTechMono', fontSize: 12),
                decoration: InputDecoration(
                  hintText: 'Search…',
                  hintStyle: JarvisText.chip.copyWith(color: JarvisColors.inkFaint),
                  prefixIcon: const Icon(Icons.search,
                      color: JarvisColors.inkFaint, size: 16),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(6),
                    borderSide: const BorderSide(color: JarvisColors.lineDim),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(6),
                    borderSide: const BorderSide(color: JarvisColors.lineDim),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(6),
                    borderSide: const BorderSide(color: JarvisColors.cyan),
                  ),
                  contentPadding: const EdgeInsets.symmetric(
                      horizontal: 12, vertical: 8),
                  isDense: true,
                  filled: true,
                  fillColor: Colors.black.withValues(alpha: 0.4),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Expanded(
              child: entriesAsync.when(
                data: (entries) {
                  var filtered = entries;
                  if (_tagFilter.isNotEmpty) {
                    filtered = filtered.where((e) {
                      final type = (e['type'] as String? ?? '').toUpperCase();
                      final meta = e['metadata'] as Map? ?? {};
                      final t = type.isNotEmpty ? type :
                          (meta['type'] as String? ?? '').toUpperCase();
                      return t == _tagFilter;
                    }).toList();
                  }
                  if (_filter.isNotEmpty) {
                    filtered = filtered.where((e) {
                      final text = (e['text'] as String? ?? '').toLowerCase();
                      final id = (e['id'] as String? ?? '').toLowerCase();
                      return text.contains(_filter) || id.contains(_filter);
                    }).toList();
                  }
                  if (filtered.isEmpty) {
                    return Center(
                      child: Padding(
                        padding: const EdgeInsets.all(32),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.folder_open_outlined,
                                size: 36, color: JarvisColors.inkFaint),
                            const SizedBox(height: 12),
                            Text(
                              _tagFilter.isNotEmpty
                                  ? '$_tagFilter kategorisinde giriş yok'
                                  : _filter.isNotEmpty
                                      ? '"$_filter" ile eşleşen giriş yok'
                                      : 'Vault henüz boş',
                              textAlign: TextAlign.center,
                              style: JarvisText.sectionHeader
                                  .copyWith(color: JarvisColors.inkDim),
                            ),
                          ],
                        ),
                      ),
                    );
                  }
                  return ListView.builder(
                    padding: const EdgeInsets.fromLTRB(18, 0, 18, 100),
                    itemCount: filtered.length,
                    itemBuilder: (_, i) => Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: _VaultEntry(entry: filtered[i]),
                    ),
                  );
                },
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (e, _) => Center(
                  child: Text('Vault yüklenemedi',
                      style: JarvisText.chip.copyWith(color: JarvisColors.red)),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _VaultEntry extends StatelessWidget {
  final Map<String, dynamic> entry;
  const _VaultEntry({required this.entry});

  @override
  Widget build(BuildContext context) {
    final text = (entry['text'] as String? ?? '').trim();
    final rawType = entry['type'] as String?
        ?? (entry['metadata'] as Map?)?['type'] as String?
        ?? 'NOTE';
    final type = rawType.toUpperCase();
    final ts = (entry['metadata'] as Map?)?['created_at'] as String? ?? '';
    final tsShort = ts.length >= 10 ? ts.substring(0, 10) : ts;

    ChipVariant chipVariant;
    switch (type) {
      case 'CONVO': chipVariant = ChipVariant.dim; break;
      case 'REPORT': chipVariant = ChipVariant.amber; break;
      case 'MEMORY': chipVariant = ChipVariant.cyan; break;
      default: chipVariant = ChipVariant.dim;
    }

    return HudPanelCard(
      padding: const EdgeInsets.all(10),
      child: Row(
        children: [
          Expanded(
            child: Text(text,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: JarvisText.chip.copyWith(
                    fontSize: 11, color: JarvisColors.cyanSoft)),
          ),
          const SizedBox(width: 8),
          HudChip(type, variant: chipVariant),
          const SizedBox(width: 8),
          if (tsShort.isNotEmpty)
            Text(tsShort,
                style: JarvisText.chip.copyWith(
                    color: JarvisColors.inkFaint,
                    fontSize: 9)),
        ],
      ),
    );
  }
}
