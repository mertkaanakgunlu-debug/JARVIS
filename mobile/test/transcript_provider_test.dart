// transcript_provider_test.dart -- pure unit tests for TranscriptNotifier,
// in particular replaceLast() (completion-contract TTFB: a final_answer
// frame must REPLACE the last JARVIS turn's text, never append to it).
//
// Deliberately plain `test()`, never `testWidgets()`: TranscriptNotifier is
// a StateNotifier<List<TranscriptTurn>> with no widget tree involved, so
// this suite does not touch MOBILE-TEST-01 (lib/app.dart's uncancelled
// splash timer). Confirmed by running this file alone:
// `flutter test test/transcript_provider_test.dart`.
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/models/transcript_turn.dart';
import 'package:jarvis_mobile/providers/transcript_provider.dart';

void main() {
  group('TranscriptNotifier.replaceLast', () {
    test('replaces the last JARVIS turn text instead of appending', () {
      final notifier = TranscriptNotifier();
      notifier.add(const TranscriptTurn(who: 'u', text: 'grafik çiz'));
      notifier.add(const TranscriptTurn(who: 'j', text: ''));
      notifier.appendToLast('Hangi ');
      notifier.appendToLast('format?');
      expect(notifier.state.last.text, 'Hangi format?');

      notifier.replaceLast('İşte grafiğiniz: chart.png');

      expect(notifier.state.length, 2);
      expect(notifier.state.last.text, 'İşte grafiğiniz: chart.png');
      expect(notifier.state.last.who, 'j');
      // The regression this guards against: appendToLast would have left
      // "Hangi format?İşte grafiğiniz: chart.png" -- a garbled duplicate.
      expect(notifier.state.last.text, isNot(contains('Hangi format?')));
    });

    test('does nothing when the transcript is empty', () {
      final notifier = TranscriptNotifier();
      notifier.replaceLast('gerçek cevap');
      expect(notifier.state, isEmpty);
    });

    test('does nothing when the last turn belongs to the user', () {
      final notifier = TranscriptNotifier();
      notifier.add(const TranscriptTurn(who: 'u', text: 'merhaba'));
      notifier.replaceLast('should not apply');
      expect(notifier.state.last.text, 'merhaba');
    });

    test('leaves earlier turns untouched', () {
      final notifier = TranscriptNotifier();
      notifier.add(const TranscriptTurn(who: 'u', text: 'ilk soru'));
      notifier.add(const TranscriptTurn(who: 'j', text: 'ilk cevap'));
      notifier.add(const TranscriptTurn(who: 'u', text: 'grafik çiz'));
      notifier.add(const TranscriptTurn(who: 'j', text: 'taslak'));

      notifier.replaceLast('gerçek cevap');

      expect(notifier.state.length, 4);
      expect(notifier.state[0].text, 'ilk soru');
      expect(notifier.state[1].text, 'ilk cevap');
      expect(notifier.state[2].text, 'grafik çiz');
      expect(notifier.state[3].text, 'gerçek cevap');
    });
  });
}
