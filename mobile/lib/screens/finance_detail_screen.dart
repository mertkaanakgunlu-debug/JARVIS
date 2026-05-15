import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:fl_chart/fl_chart.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../widgets/grid_background.dart';
import '../widgets/hud_panel_card.dart';
import '../widgets/hud_meter.dart';
import '../providers/finance_provider.dart';

class FinanceDetailScreen extends ConsumerWidget {
  const FinanceDetailScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final summaryAsync = ref.watch(financeSummaryProvider);
    final budgetAsync = ref.watch(financeBudgetProvider);
    final recentAsync = ref.watch(financeRecentProvider);
    final topCatsAsync = ref.watch(financeTopCategoriesProvider);

    return Scaffold(
      backgroundColor: JarvisColors.bg,
      body: GridBackground(
        child: SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(18, 12, 18, 0),
                child: Row(
                  children: [
                    GestureDetector(
                      onTap: () => Navigator.pop(context),
                      child: const Icon(Icons.arrow_back_ios,
                          color: JarvisColors.inkDim, size: 18),
                    ),
                    const SizedBox(width: 12),
                    Text('FINANCE', style: JarvisText.wordmark),
                    const Spacer(),
                    GestureDetector(
                      onTap: () {
                        ref.invalidate(financeSummaryProvider);
                        ref.invalidate(financeBudgetProvider);
                        ref.invalidate(financeRecentProvider);
                        ref.invalidate(financeTopCategoriesProvider);
                      },
                      child: const Icon(Icons.refresh,
                          color: JarvisColors.inkDim, size: 18),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(18, 12, 18, 100),
                  children: [
                    // Summary card
                    summaryAsync.when(
                      data: (s) => _SummaryCard(summary: s),
                      loading: () => const SizedBox.shrink(),
                      error: (_, __) => const SizedBox.shrink(),
                    ),
                    const SizedBox(height: 16),

                    // Pie chart
                    topCatsAsync.when(
                      data: (cats) => cats.isEmpty
                          ? const SizedBox.shrink()
                          : _CategoryPie(categories: cats),
                      loading: () => const SizedBox.shrink(),
                      error: (_, __) => const SizedBox.shrink(),
                    ),
                    const SizedBox(height: 16),

                    // Budget meters
                    budgetAsync.when(
                      data: (budgets) => budgets.isEmpty
                          ? const SizedBox.shrink()
                          : _BudgetBars(budgets: budgets),
                      loading: () => const SizedBox.shrink(),
                      error: (_, __) => const SizedBox.shrink(),
                    ),
                    const SizedBox(height: 16),

                    // Recent transactions
                    recentAsync.when(
                      data: (txs) => _RecentTransactions(transactions: txs),
                      loading: () => const CircularProgressIndicator(),
                      error: (e, _) => Text('Hata: $e',
                          style: JarvisText.chip.copyWith(color: JarvisColors.red)),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SummaryCard extends StatelessWidget {
  final Map<String, dynamic> summary;
  const _SummaryCard({required this.summary});

  @override
  Widget build(BuildContext context) {
    final income = (summary['income'] as num?)?.toDouble() ?? 0;
    final expenses = (summary['expenses'] as num?)?.toDouble() ?? 0;
    final net = income + expenses;

    return HudPanelCard(
      padding: const EdgeInsets.all(12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceAround,
        children: [
          _Cell('GELİR', '₺${income.abs().toStringAsFixed(0)}',
              JarvisColors.green),
          _Cell('GİDER', '₺${expenses.abs().toStringAsFixed(0)}',
              JarvisColors.red),
          _Cell('NET', '₺${net.toStringAsFixed(0)}',
              net >= 0 ? JarvisColors.cyanSoft : JarvisColors.amber),
        ],
      ),
    );
  }
}

class _Cell extends StatelessWidget {
  final String label, value;
  final Color color;
  const _Cell(this.label, this.value, this.color);

  @override
  Widget build(BuildContext context) => Column(
    children: [
      Text(label, style: JarvisText.chip),
      const SizedBox(height: 4),
      Text(value, style: JarvisText.chip.copyWith(
          fontSize: 14, color: color, fontWeight: FontWeight.bold)),
    ],
  );
}

class _CategoryPie extends StatelessWidget {
  final List<Map<String, dynamic>> categories;
  const _CategoryPie({required this.categories});

  static const _colors = [
    JarvisColors.cyan, JarvisColors.amber, JarvisColors.red,
    JarvisColors.green, JarvisColors.cyanSoft,
  ];

  @override
  Widget build(BuildContext context) {
    final total = categories.fold<double>(
        0, (sum, c) => sum + ((c['total'] as num?)?.abs() ?? 0));

    return HudPanelCard(
      padding: const EdgeInsets.all(12),
      child: Column(
        children: [
          Text('// KATEGORİLER', style: JarvisText.sectionHeader),
          const SizedBox(height: 12),
          SizedBox(
            height: 160,
            child: PieChart(
              PieChartData(
                sections: categories.asMap().entries.map((e) {
                  final cat = e.value;
                  final val = (cat['total'] as num?)?.abs().toDouble() ?? 0;
                  return PieChartSectionData(
                    value: val,
                    color: _colors[e.key % _colors.length],
                    radius: 55,
                    title: total > 0
                        ? '${(val / total * 100).toStringAsFixed(0)}%'
                        : '',
                    titleStyle: JarvisText.chip.copyWith(fontSize: 9),
                  );
                }).toList(),
                sectionsSpace: 2,
                centerSpaceRadius: 30,
              ),
            ),
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8, runSpacing: 4,
            children: categories.asMap().entries.map((e) {
              final cat = e.value['category'] as String? ?? '';
              return Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: 8, height: 8,
                    decoration: BoxDecoration(
                      color: _colors[e.key % _colors.length],
                      shape: BoxShape.circle,
                    ),
                  ),
                  const SizedBox(width: 4),
                  Text(cat, style: JarvisText.chip),
                ],
              );
            }).toList(),
          ),
        ],
      ),
    );
  }
}

class _BudgetBars extends StatelessWidget {
  final List<Map<String, dynamic>> budgets;
  const _BudgetBars({required this.budgets});

  @override
  Widget build(BuildContext context) => HudPanelCard(
    padding: const EdgeInsets.all(12),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('// BÜTÇE', style: JarvisText.sectionHeader),
        const SizedBox(height: 8),
        ...budgets.map((b) {
          final cat = b['category'] as String? ?? '';
          final pct = (b['pct'] as num?)?.toDouble() ?? 0;
          final spent = (b['spent'] as num?)?.toDouble() ?? 0;
          final limit = (b['limit'] as num?)?.toDouble() ?? 0;
          final over = b['over_threshold'] == true;
          return Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(cat, style: JarvisText.chip.copyWith(fontSize: 11)),
                    const Spacer(),
                    Text('₺${spent.toStringAsFixed(0)} / ₺${limit.toStringAsFixed(0)}',
                        style: JarvisText.chip.copyWith(
                            color: over ? JarvisColors.amber : JarvisColors.inkDim)),
                  ],
                ),
                const SizedBox(height: 4),
                HudMeter(
                  value: pct.clamp(0, 1),
                  color: over ? JarvisColors.amber : JarvisColors.cyan,
                ),
              ],
            ),
          );
        }),
      ],
    ),
  );
}

class _RecentTransactions extends StatelessWidget {
  final List<Map<String, dynamic>> transactions;
  const _RecentTransactions({required this.transactions});

  @override
  Widget build(BuildContext context) => HudPanelCard(
    padding: const EdgeInsets.all(12),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('// SON İŞLEMLER', style: JarvisText.sectionHeader),
        const SizedBox(height: 8),
        ...transactions.take(15).map((tx) {
          final merchant = tx['merchant'] as String? ?? '';
          final amount = (tx['amount'] as num?)?.toDouble() ?? 0;
          final date = (tx['date'] as String? ?? '').substring(0, 10.clamp(0, (tx['date'] as String? ?? '').length));
          final isExpense = amount < 0;
          return Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(
              children: [
                Text(date,
                    style: JarvisText.chip.copyWith(
                        color: JarvisColors.inkFaint, fontSize: 9)),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(merchant.isEmpty ? tx['category'] as String? ?? '' : merchant,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: JarvisText.chip.copyWith(fontSize: 11)),
                ),
                Text(
                  '${isExpense ? '-' : '+'}₺${amount.abs().toStringAsFixed(0)}',
                  style: JarvisText.chip.copyWith(
                    color: isExpense ? JarvisColors.red : JarvisColors.green,
                  ),
                ),
              ],
            ),
          );
        }),
      ],
    ),
  );
}
