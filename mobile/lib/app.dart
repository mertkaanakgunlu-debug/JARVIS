import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'providers/settings_provider.dart';
import 'providers/ws_provider.dart';
import 'screens/home_shell.dart';
import 'screens/lock_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/task_detail_screen.dart';
import 'screens/finance_detail_screen.dart';
import 'theme/jarvis_theme.dart';

class JarvisApp extends ConsumerWidget {
  const JarvisApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final settings = ref.watch(settingsSyncProvider);

    // Kick off WebSocket connection when settings are ready
    if (settings.host.isNotEmpty) {
      ref.read(wsClientProvider); // trigger provider initialisation
    }

    return MaterialApp(
      title: 'J.A.R.V.I.S.',
      debugShowCheckedModeBanner: false,
      theme: JarvisTheme.darkTheme(),
      initialRoute: '/',
      routes: {
        '/': (_) => const _SplashRouter(),
        '/home': (_) => const HomeShell(),
        '/settings': (_) => const SettingsScreen(),
      },
      onGenerateRoute: (settings) {
        if (settings.name == '/task_detail') {
          final taskId = settings.arguments as String;
          return MaterialPageRoute(
            builder: (_) => TaskDetailScreen(taskId: taskId),
          );
        }
        if (settings.name == '/finance_detail') {
          return MaterialPageRoute(
            builder: (_) => const FinanceDetailScreen(),
          );
        }
        return null;
      },
    );
  }
}

class _SplashRouter extends ConsumerStatefulWidget {
  const _SplashRouter();

  @override
  ConsumerState<_SplashRouter> createState() => _SplashRouterState();
}

class _SplashRouterState extends ConsumerState<_SplashRouter> {
  @override
  void initState() {
    super.initState();
    Future.delayed(const Duration(seconds: 2), () {
      if (mounted) {
        Navigator.pushReplacementNamed(context, '/home');
      }
    });
  }

  @override
  Widget build(BuildContext context) => const LockScreen();
}
