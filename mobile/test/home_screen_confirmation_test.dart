// home_screen_confirmation_test.dart -- structured-SSE regression guards on the
// app's REAL chat surface.
//
// Why these live on HomeScreen and not on some chat screen: HomeShell's tab
// list is CORE/TASKS/SCHED/VAULT, and CORE *is* HomeScreen. There is no other
// reachable chat surface. The L3 approve/deny round-trip was originally wired
// to a ChatScreen that nothing ever routed to, so it passed its own unit tests
// while the live app printed the raw `confirmation_required` frame into the
// transcript and spoke it aloud (2026-08-08 live run). Every test here drives
// the widget the user actually touches.
//
// Never pumpAndSettle in this tree -- JarvisOrb's AnimationController calls
// repeat(), so it never settles. Pump explicit durations.

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:jarvis_mobile/core/api_client.dart';
import 'package:jarvis_mobile/core/config.dart';
import 'package:jarvis_mobile/providers/api_provider.dart';
import 'package:jarvis_mobile/providers/confirmation_provider.dart';
import 'package:jarvis_mobile/providers/settings_provider.dart';
import 'package:jarvis_mobile/screens/home_screen.dart';
import 'package:jarvis_mobile/screens/home_shell.dart';

/// One well-formed confirmation frame, byte-identical in shape to what
/// jarvis/api.py's _confirmation_sse_frame() puts on the wire (captured from a
/// live run against the real server on 2026-08-08).
String confirmFrame(String id, {String description = 'run the shell command: echo hi'}) =>
    '{"type": "confirmation_required", "id": "$id", "payload": {"tools": '
    '[{"name": "shell_run", "args": {"command": "echo hi"}, "id": "call_x", '
    '"description": "$description", "execution_id": "call_x-abc"}], "count": 1}}';

/// Drives whatever the test queues onto the three SSE-bearing endpoints, and
/// records what was called so double-submit can be asserted rather than
/// assumed.
class FakeApi extends ApiClient {
  FakeApi() : super(host: '127.0.0.1:8000', apiKey: 'test-key');

  List<String> chatFrames = const [];
  List<String> uploadFrames = const [];

  /// Frames returned by the NEXT confirmStream call, keyed by call order.
  List<List<String>> confirmFrameQueue = [];

  final List<String> chatCalls = [];
  final List<({String id, String decision})> confirmCalls = [];
  final List<String> uploadCalls = [];

  @override
  Stream<String> chatStream(String message, {String language = 'tr'}) async* {
    chatCalls.add(message);
    for (final f in chatFrames) {
      yield f;
    }
  }

  @override
  Stream<String> confirmStream(String confId, String decision) async* {
    confirmCalls.add((id: confId, decision: decision));
    final frames = confirmFrameQueue.isNotEmpty
        ? confirmFrameQueue.removeAt(0)
        : const <String>[];
    for (final f in frames) {
      yield f;
    }
  }

  @override
  Stream<String> uploadFileStream(
    String filePath,
    String fileName, {
    String query = '',
    String language = 'tr',
  }) async* {
    uploadCalls.add(fileName);
    for (final f in uploadFrames) {
      yield f;
    }
  }

  // The dashboard providers (tasks/todos/finance/vault) fire on build. Fail
  // them instantly rather than letting Dio attempt a real socket: Riverpod
  // captures the error into AsyncValue, and the screen already renders those
  // as empty.
  @override
  Future<Response<T>> get<T>(String path, {Map<String, dynamic>? params}) =>
      Future.error(StateError('no network in tests'));
}

const _settings = JarvisSettings(
  host: '127.0.0.1:8000',
  apiKey: 'test-key',
  pcMac: '',
  lanIp: '',
  // autoWake off: WakeService would otherwise try to ping/WoL before sending.
  autoWake: false,
  // ttsEnabled off: this suite asserts what is on SCREEN. The "never spoken"
  // half of the guarantee is structural -- _speakChunk is only ever reached
  // from the ChatToken branch, which a control frame never takes.
  ttsEnabled: false,
  sttEnabled: false,
  pushEnabled: false,
  asyncHeuristic: true,
  wakeWordEnabled: false,
  language: 'tr',
  gridAlpha: 0.04,
);

Future<void> pumpHome(WidgetTester tester, FakeApi api) async {
  tester.view.physicalSize = const Size(1080, 2400);
  tester.view.devicePixelRatio = 3.0;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        settingsSyncProvider.overrideWithValue(_settings),
        apiClientProvider.overrideWithValue(api),
      ],
      child: const MaterialApp(home: Scaffold(body: HomeScreen())),
    ),
  );
  await tester.pump();
}

/// Type into the composer and fire its onSubmitted.
Future<void> sendTurn(WidgetTester tester, String text) async {
  await tester.enterText(find.byType(TextField).first, text);
  await tester.pump();
  await tester.testTextInput.receiveAction(TextInputAction.done);
  // Let the async stream drain; never pumpAndSettle (orb repeats forever).
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 50));
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    final messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    // HomeScreen builds a FlutterTts in initState and _Composer builds a
    // SpeechToText. Unmocked, their first invokeMethod throws
    // MissingPluginException as an unhandled async error and fails the test
    // before anything under test runs.
    messenger.setMockMethodCallHandler(
        const MethodChannel('flutter_tts'), (call) async => 1);
    messenger.setMockMethodCallHandler(
        const MethodChannel('plugin.csdcorp.com/speech_to_text'),
        (call) async => false);
  });

  // ── The wiring fact the original bug hid behind ───────────────────────────

  test('HomeScreen is the CORE tab, so the chat surface is reachable', () {
    // A confirmation UI attached to a screen no tab routes to passes its own
    // tests and is dead in the app. That is exactly what happened.
    expect(HomeShell.screens.first, isA<HomeScreen>());
  });

  // ── 1. confirmation_required renders a card, never JSON ───────────────────

  testWidgets('a confirmation frame raises the card and never shows JSON',
      (tester) async {
    final api = FakeApi()..chatFrames = [confirmFrame('conf-1'), '[DONE]'];
    await pumpHome(tester, api);
    await sendTurn(tester, 'komut çalıştır');

    expect(find.text('ONAY GEREKİYOR'), findsOneWidget);
    expect(find.text('ONAYLA'), findsOneWidget);
    expect(find.text('REDDET'), findsOneWidget);
    // The server's plain-language line, not the payload.
    expect(find.textContaining('run the shell command'), findsOneWidget);

    // The regression itself: no frame internals anywhere on screen.
    expect(find.textContaining('confirmation_required'), findsNothing);
    expect(find.textContaining('"type"'), findsNothing);
    expect(find.textContaining('execution_id'), findsNothing);
    // And never the raw arguments.
    expect(find.textContaining('"args"'), findsNothing);
  });

  testWidgets('the card replaces the composer while an interrupt is open',
      (tester) async {
    final api = FakeApi()..chatFrames = [confirmFrame('conf-1'), '[DONE]'];
    await pumpHome(tester, api);
    expect(find.byType(TextField), findsWidgets);

    await sendTurn(tester, 'komut çalıştır');

    // No composer to start a second turn on top of a paused graph.
    expect(find.byType(TextField), findsNothing);
  });

  // ── 2. approve: exactly one POST, continuation rendered ───────────────────

  testWidgets('approve POSTs once and streams the continuation', (tester) async {
    final api = FakeApi()
      ..chatFrames = [confirmFrame('conf-1'), '[DONE]']
      ..confirmFrameQueue = [
        ['Komut çalıştırıldı.', '[DONE]'],
      ];
    await pumpHome(tester, api);
    await sendTurn(tester, 'komut çalıştır');

    await tester.tap(find.text('ONAYLA'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    expect(api.confirmCalls, hasLength(1));
    expect(api.confirmCalls.single.id, 'conf-1');
    expect(api.confirmCalls.single.decision, 'approve');

    expect(find.text('Komut çalıştırıldı.'), findsOneWidget);
    // Card gone, composer back.
    expect(find.text('ONAY GEREKİYOR'), findsNothing);
    expect(find.byType(TextField), findsWidgets);
  });

  testWidgets('a double tap on approve still POSTs only once', (tester) async {
    // Approving twice is not idempotent server-side: the first POST claims the
    // interrupt, so a second answers "expired or not found" OVER the real
    // continuation. The guard is beginSubmit(), not the button's enabled state.
    final api = FakeApi()
      ..chatFrames = [confirmFrame('conf-1'), '[DONE]']
      ..confirmFrameQueue = [
        ['ok', '[DONE]'],
        ['SHOULD NOT HAPPEN', '[DONE]'],
      ];
    await pumpHome(tester, api);
    await sendTurn(tester, 'komut çalıştır');

    // Two taps before any frame is pumped -- the same-frame double tap.
    await tester.tap(find.text('ONAYLA'), warnIfMissed: false);
    await tester.tap(find.text('ONAYLA'), warnIfMissed: false);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    expect(api.confirmCalls, hasLength(1));
    expect(find.textContaining('SHOULD NOT HAPPEN'), findsNothing);
  });

  // ── 3. deny: no execution, card closes ────────────────────────────────────

  testWidgets('deny sends the deny decision and closes the card',
      (tester) async {
    final api = FakeApi()
      ..chatFrames = [confirmFrame('conf-1'), '[DONE]']
      ..confirmFrameQueue = [
        ['Komut çalıştırılmadı.', '[DONE]'],
      ];
    await pumpHome(tester, api);
    await sendTurn(tester, 'komut çalıştır');

    await tester.tap(find.text('REDDET'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    expect(api.confirmCalls, hasLength(1));
    expect(api.confirmCalls.single.decision, 'deny');
    // 'approve' must never reach the wire on a deny -- the two branches are
    // separate protections and one says nothing about the other.
    expect(
      api.confirmCalls.where((c) => c.decision == 'approve'),
      isEmpty,
    );

    expect(find.text('ONAY GEREKİYOR'), findsNothing);
    expect(find.text('Komut çalıştırılmadı.'), findsOneWidget);
  });

  // ── 4. a second confirmation can arrive on the continuation ───────────────

  testWidgets('a second interrupt raised by the continuation keeps a card up',
      (tester) async {
    final api = FakeApi()
      ..chatFrames = [confirmFrame('conf-1'), '[DONE]']
      ..confirmFrameQueue = [
        [
          confirmFrame('conf-2', description: 'run the shell command: second'),
          '[DONE]',
        ],
      ];
    await pumpHome(tester, api);
    await sendTurn(tester, 'komut çalıştır');

    await tester.tap(find.text('ONAYLA'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    // resolved() is id-checked, so finishing conf-1 must not clear conf-2.
    expect(find.text('ONAY GEREKİYOR'), findsOneWidget);
    expect(find.textContaining('second'), findsOneWidget);
    expect(find.textContaining('confirmation_required'), findsNothing);

    final container = ProviderScope.containerOf(
      tester.element(find.byType(HomeScreen)),
    );
    expect(container.read(confirmationProvider).pending?.id, 'conf-2');
    // The second prompt must be answerable, not stuck behind a stale in-flight
    // flag from the POST that just finished.
    expect(container.read(confirmationProvider).submitting, isFalse);
  });

  // ── 5. progress / final_answer are never raw JSON ─────────────────────────

  testWidgets('progress and final_answer never reach the transcript as JSON',
      (tester) async {
    final api = FakeApi()
      ..chatFrames = [
        '{"type": "progress", "phase": "building", "kind": "chart"}',
        'taslak',
        '{"type": "final_answer", "text": "Nihai cevap."}',
        '[DONE]',
      ];
    await pumpHome(tester, api);
    await sendTurn(tester, 'grafik çiz');

    expect(find.textContaining('"type"'), findsNothing);
    expect(find.textContaining('final_answer'), findsNothing);
    expect(find.textContaining('progress'), findsNothing);

    // final_answer REPLACES the draft rather than being glued onto it.
    expect(find.text('Nihai cevap.'), findsOneWidget);
    expect(find.textContaining('taslak'), findsNothing);
  });

  testWidgets('an async-task frame is rendered as a note, not JSON',
      (tester) async {
    final api = FakeApi()
      ..chatFrames = ['{"async": true, "task_id": "t-42", "status": "queued"}'];
    await pumpHome(tester, api);
    await sendTurn(tester, 'uzun iş');

    expect(find.textContaining('t-42'), findsOneWidget);
    expect(find.textContaining('"async"'), findsNothing);
  });

  // ── 6. upload uses the same reader ────────────────────────────────────────

  testWidgets('an upload stream raises the card too, and shows no JSON',
      (tester) async {
    // /chat/upload goes through the server's same _sse_frames() wrapper, so a
    // file whose analysis reaches an L3 call must behave identically. This is
    // the leg that had its own second copy of the prefix loop.
    final api = FakeApi()
      ..uploadFrames = [confirmFrame('conf-up'), '[DONE]'];
    await pumpHome(tester, api);

    final state =
        tester.state<HomeScreenState>(find.byType(HomeScreen));
    await state.consumeStreamForTest(
      api.uploadFileStream('/tmp/x.pdf', 'x.pdf'),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    expect(api.uploadCalls, hasLength(1));
    expect(find.text('ONAY GEREKİYOR'), findsOneWidget);
    expect(find.textContaining('confirmation_required'), findsNothing);
  });
}
