import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:jarvis_mobile/app.dart';

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
}
