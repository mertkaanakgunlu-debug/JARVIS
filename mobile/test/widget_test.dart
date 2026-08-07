// widget_test.dart -- JarvisApp smoke test plus the MOBILE-TEST-01 regression
// guards around the splash router's 2s timer.
//
// flutter_test unmounts the widget tree at the end of every test and then
// asserts that no Timer is left pending ("A Timer is still pending even after
// the widget tree was disposed", _verifyInvariants in
// flutter_test/src/binding.dart). Before the fix, _SplashRouterState armed an
// uncancellable Future.delayed, so every test that pumped JarvisApp failed
// there -- including the smoke test below.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:jarvis_mobile/app.dart';
import 'package:jarvis_mobile/screens/home_shell.dart';
import 'package:jarvis_mobile/screens/lock_screen.dart';

void main() {
  testWidgets('App smoke test — renders without crash', (WidgetTester tester) async {
    await tester.pumpWidget(
      const ProviderScope(
        child: JarvisApp(),
      ),
    );
    // Just verify it builds without throwing
    expect(find.byType(MaterialApp), findsOneWidget);
  });

  testWidgets('splash timer does not outlive a tree disposed mid-splash',
      (WidgetTester tester) async {
    await tester.pumpWidget(const ProviderScope(child: JarvisApp()));
    expect(find.byType(LockScreen), findsOneWidget);

    // Dispose the tree while the 2s timer is still armed: no fake time has
    // elapsed yet, and pumping a different root tears _SplashRouterState down.
    await tester.pumpWidget(const SizedBox.shrink());
    expect(find.byType(LockScreen), findsNothing);

    // Deliberately do NOT elapse past 2s from here. Elapsing would let a leaked
    // timer fire and retire itself, and the end-of-test check would then pass
    // vacuously -- disposal *inside* the window is the only case that a
    // cancelled timer survives and an uncancellable one does not.
  });

  testWidgets('splash still routes to /home after 2 seconds',
      (WidgetTester tester) async {
    await tester.pumpWidget(const ProviderScope(child: JarvisApp()));
    expect(find.byType(LockScreen), findsOneWidget);

    // Just short of the deadline: still on the lock screen.
    await tester.pump(const Duration(milliseconds: 1999));
    expect(find.byType(LockScreen), findsOneWidget);
    expect(find.byType(HomeShell), findsNothing);

    // Crossing 2s fires the timer; pump the route transition through by hand.
    // Never pumpAndSettle here -- JarvisOrb's controller repeats forever, so
    // this tree never settles.
    await tester.pump(const Duration(milliseconds: 2));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));

    expect(find.byType(HomeShell), findsOneWidget);
    expect(find.byType(LockScreen), findsNothing);
  });
}
