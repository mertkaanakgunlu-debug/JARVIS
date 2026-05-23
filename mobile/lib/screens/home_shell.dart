import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../theme/jarvis_theme.dart';
import '../theme/typography.dart';
import '../providers/state_provider.dart';
import '../providers/ws_provider.dart';
import 'home_screen.dart';
import 'tasks_screen.dart';
import 'schedule_screen.dart';
import 'vault_screen.dart';

class HomeShell extends ConsumerStatefulWidget {
  const HomeShell({super.key});

  @override
  ConsumerState<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends ConsumerState<HomeShell> {
  int _index = 0;

  static const _screens = [
    HomeScreen(),
    TasksScreen(),
    ScheduleScreen(),
    VaultScreen(),
  ];

  @override
  void initState() {
    super.initState();
    // Kick off WS listeners
    ref.read(stateListenerProvider);
    ref.read(wsDispatcherProvider);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: JarvisColors.bg,
      body: IndexedStack(index: _index, children: _screens),
      bottomNavigationBar: _JarvisBottomNav(
        currentIndex: _index,
        onTap: (i) => setState(() => _index = i),
      ),
    );
  }
}

class _JarvisBottomNav extends ConsumerWidget {
  final int currentIndex;
  final ValueChanged<int> onTap;

  const _JarvisBottomNav({required this.currentIndex, required this.onTap});

  static const _tabs = [
    (label: 'CORE',  icon: _CoreIcon()),
    (label: 'TASKS', icon: _TasksIcon()),
    (label: 'SCHED', icon: _SchedIcon()),
    (label: 'VAULT', icon: _VaultIcon()),
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Container(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.bottomCenter,
          end: Alignment.topCenter,
          colors: [Colors.black.withOpacity(0.92), Colors.transparent],
        ),
      ),
      child: ClipRect(
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
          child: SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(0, 10, 0, 8),
              child: Row(
                children: List.generate(_tabs.length, (i) {
                  final active = i == currentIndex;
                  final accent = active ? JarvisColors.cyanSoft : JarvisColors.inkFaint;
                  return Expanded(
                    child: GestureDetector(
                      onTap: () => onTap(i),
                      behavior: HitTestBehavior.opaque,
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          IconTheme(
                            data: IconThemeData(
                              color: accent,
                              size: 20,
                            ),
                            child: _tabs[i].icon,
                          ),
                          const SizedBox(height: 5),
                          Text(
                            _tabs[i].label,
                            style: JarvisText.tabLabel.copyWith(
                              color: accent,
                              shadows: active
                                  ? [Shadow(color: JarvisColors.cyan, blurRadius: 8)]
                                  : null,
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                }),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// Tab icons as simple custom painters
class _CoreIcon extends StatelessWidget {
  const _CoreIcon();
  @override
  Widget build(BuildContext context) => CustomPaint(
    size: const Size(20, 20),
    painter: _CircleDotPainter(IconTheme.of(context).color ?? JarvisColors.inkFaint),
  );
}

class _TasksIcon extends StatelessWidget {
  const _TasksIcon();
  @override
  Widget build(BuildContext context) => CustomPaint(
    size: const Size(20, 20),
    painter: _ListIconPainter(IconTheme.of(context).color ?? JarvisColors.inkFaint),
  );
}

class _SchedIcon extends StatelessWidget {
  const _SchedIcon();
  @override
  Widget build(BuildContext context) =>
      Icon(Icons.calendar_today_outlined, size: 18,
          color: IconTheme.of(context).color);
}

class _VaultIcon extends StatelessWidget {
  const _VaultIcon();
  @override
  Widget build(BuildContext context) => CustomPaint(
    size: const Size(20, 20),
    painter: _CrosshairCirclePainter(IconTheme.of(context).color ?? JarvisColors.inkFaint),
  );
}

class _CircleDotPainter extends CustomPainter {
  final Color c;
  const _CircleDotPainter(this.c);
  @override
  void paint(Canvas canvas, Size s) {
    final p = Paint()..color = c..style = PaintingStyle.stroke..strokeWidth = 1.2;
    canvas.drawCircle(Offset(s.width / 2, s.height / 2), 7, p);
    canvas.drawCircle(Offset(s.width / 2, s.height / 2), 2,
        Paint()..color = c..style = PaintingStyle.fill);
  }
  @override bool shouldRepaint(_) => false;
}

class _ListIconPainter extends CustomPainter {
  final Color c;
  const _ListIconPainter(this.c);
  @override
  void paint(Canvas canvas, Size s) {
    final p = Paint()..color = c..strokeWidth = 1.5..strokeCap = StrokeCap.round;
    for (int i = 0; i < 3; i++) {
      final y = s.height * (0.3 + i * 0.2);
      canvas.drawLine(Offset(2, y), Offset(s.width - 2, y), p);
    }
  }
  @override bool shouldRepaint(_) => false;
}

class _CrosshairCirclePainter extends CustomPainter {
  final Color c;
  const _CrosshairCirclePainter(this.c);
  @override
  void paint(Canvas canvas, Size s) {
    final p = Paint()..color = c..style = PaintingStyle.stroke..strokeWidth = 1.2;
    canvas.drawCircle(Offset(s.width / 2, s.height / 2), 7, p);
    canvas.drawLine(Offset(s.width / 2, 1), Offset(s.width / 2, s.height - 1), p);
    canvas.drawLine(Offset(1, s.height / 2), Offset(s.width - 1, s.height / 2), p);
  }
  @override bool shouldRepaint(_) => false;
}
