# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).
>
> **KALICI KURAL (owner koydu, 2026-07-23):** bu dosya kendi kapanış commit'inin SHA'sını ve
> kendi push/CI sonucunu ASLA yazmaz. Kapanış commit'i oturumun commit sayısına her zaman
> İLİŞKİSEL dahil edilir ("N iş commit'i ve bu kapanış HANDOFF commit'i"); oturum sonu durumu
> "local == origin senkrondu" diye yazılır; test iddiaları çalıştırılan komut + tarih ile
> bağlanır ("tests pass" tek başına yazılmaz). Branch ucunun CI sonucuna her zaman
> `gh run list --branch langgraph-migration` ile canlı bakılır — bu dosyadan okunmaz.

## Son oturum: 2026-07-25 — DIŞ İNCELEME REMEDIATION: 2 CANLI RUNTIME BUG'I + TRANSPORT PARITY + GÖZLEMLENEBİLİRLİK + GATE BÜTÜNLÜĞÜ

**Durum tek cümlede:** Owner bir önceki sprint'in (B0→B1→B2→D→F→E) dış incelemesini getirdi;
inceleme kod üzerinde tek tek doğrulandı (**hepsi isabetliydi, biri hariç — aşağıya bakın**),
önerilen düzeltme sırasının **kodla kapatılabilir 1-4. maddeleri tamamlandı**; 5-7. maddeler
(W18/R24 redesign, corpus'u 40-60'a çıkarma, Faz C) canlı model koşusu veya owner kararı
gerektirdiği için AÇIK bırakıldı.

### İncelemenin doğrulanması — bir madde yanlıştı

İnceleme "GitHub tarafında bu branch için workflow run veya combined status göremedim; 'push
yapıldı, CI yeşil' denemez" diyordu. **Bu yanlış:** `gh run list --branch langgraph-migration`
önceki oturumun 6+1 commit'inin push edildiğini ve branch ucunun (`e5a46e3`) CI'ının
`completed success` olduğunu gösteriyor. Diğer tüm P1/P2 bulguları kodda birebir doğrulandı.

### 1. `--ptt` tek-tur sözleşmesi + ses state yaşam döngüsü (CANLI BUG)

`drive_voice_session()` `stop_after_first_turn`'ü YALNIZCA izlenen bir coroutine tamamlandığında
işletiyordu. `on_transcript` işi senkron bitirip `None` döndürdüğünde hiç turn_task oluşmadığı
için koşul hiç değerlendirilmiyordu. **Canlı erişilebilir yol:** `cli.py`'nin
`_detect_model_switch` dalı ("flash modeline geç") modeli değiştirir, kendi `speak_stream()`'ini
bekler ve `None` döner — yani `--ptt` modunda cevap konuşulur ama mikrofon açık kalır, Enter
kapısına HİÇ dönülmez. Artık senkron tur da turu bitiriyor.

İkinci yarısı: `finally` bloğu yalnız task'ları dispose ediyordu, capture ekseni girişte
`listening` yapılıp hiç temizlenmiyordu — her çağıran hemen ardından `engine.stop()` çağırdığı
için reducer fiziksel olarak kapalı bir mikrofonu "dinliyor" diye raporluyordu (en görünür yeri:
Enter'ı bekleyen PTT kapısı). Artık iki eksen de baseline'a dönüyor, `awaiting_confirmation`
korunarak (o, oturumu meşru olarak aşan tek durum).

**Bu iki davranışı SABİTLEYEN 3 test vardı** (`state.capture == "listening"` / `response ==
"thinking"` oturum bittikten sonra) — gerçek sözleşmeye çekildiler. Yeni
`tests/test_ptt_single_turn.py`: planın Faz E'de isteyip yazılmamış olan deterministik
press-to-arm → tek utterance → VAD-stop testi, `WavAudioIO` + gerçek engine + gerçek
`drive_voice_session` ile. **Düzeltme öncesi kodda 3'ü kırmızı olduğu ölçülerek doğrulandı.**

Ayrıca bir sıralama kusuru: transcript geldiğinde capture-önce/response-sonra sırası bir anlık
`(listening, idle)` çiftinden geçiyor ve bu wire'a "STT bitti → listening → thinking" diye tek
karelik bir orb titremesi olarak yansıyordu. Response-önce yapıldı (test:
`test_transcription_to_thinking_does_not_flicker_through_listening`, ters sırada kırmızı olduğu
ölçüldü).

### 2. Transport parity — reducer üç taşıyıcıya da bağlandı

Faz D'nin `VoiceState`'i yalnız `cli.py`'ye ulaşmıştı; `voice_api.py` (yerel wakeword/PTT loop) ve
`api.py`'nin uzak `/ws` audio session'ı kendi `event_bus.state()` literal'lerini elle
serpiştiriyordu — aynı state makinesi için iki ayrı sözleşme, reducer'ın öncelik kuralları
(iki eksenin aynı anda canlı olması, `awaiting_confirmation`'ın rutin ilerlemeyi ezmesi) HUD'a hiç
ulaşmıyordu.

Üçü de artık oturum başına TEK bir `VoiceState` kuruyor ve onu `drive_voice_session` /
`run_one_response` / `arm_and_speak_confirmation` / `resolve_confirmation`'a geçiriyor. HUD'un
yayınlanmış wire sözlüğü (`listening|speaking|thinking|working|idle`, kaynak:
`electron/src/renderer/src/hooks/useJarvisSocket.js`) dar olduğu için collapse TEK bir tabloda
yapılıyor (`state.py`'nin `_HUD_STATE`'i) + `hud_state_emitter()` tekrarları bastırıyor.
`awaiting_confirmation` bilinçli olarak `listening`'e eşleniyor (mikrofon gerçekten yes/no için
açık; soruyu HUD zaten kendi confirmation overlay'inde gösteriyor) — wire sözlüğünü genişletmek
canlı HUD E2E'si hiç yapılmamış bir Electron değişikliği gerektirirdi.

### 3. `/voice-status` — telemetri artık gerçekten gözlemlenebilir

Faz D sayaçları ÖLÇÜLEBİLİR yapmıştı ama hiçbir şey onları birleştirmiyordu; tek okuyucu, hiçbir
şey olmadan önce bir kez çalışan 4 statik alanlık bir startup print'iydi. Yeni
`jarvis/voice/diagnostics.py`: tek `VoiceDiagnosticsSnapshot` (cihazlar, örnekleme hızı, gerçek STT
cihazı, mic RMS, queue depth, input status/overflow, output underrun, son turun VAD max/mean +
turn-end reason + captured audio + stt_s, capture/response/display/hud_state) + süreç-içi canlı
oturum kayıt defteri. İki yüzey aynı snapshot'ı okuyor, drift edemezler:

- CLI: `--ptt` kapısında `/voice-status` yazın (sesli modun tek yazılı giriş noktası orası);
  mikrofon açılmadan tablo basılır. Startup print'i de aynı snapshot'ın üstüne indi.
- API: authenticated `GET /voice/status`. Oturum yoksa 200 + `session_active: false` (hata değil —
  "ses çalışmıyor" başlı başına teşhis cevabıdır).

Bunun için `RealtimeVoiceEngine` son tur metriklerini artık SAKLIYOR (`last_turn_metrics`) —
eskiden yalnızca `TurnEnded`/`FinalTranscript` ile yayınlanıp kayboluyorlardı.

**`input_overflow_count` semantiği düzeltildi** (incelemenin P1 sayaç bulgusu): PortAudio'nun
`CallbackFlags`'i HERHANGİ bir durum bildirdiğinde truthy, dolayısıyla her truthy `status`'u
overflow saymak ismin verdiği sözü abartıyordu. Artık iki sayaç: `input_status_count` (dürüst
"PortAudio bir şey bildirdi") ve yalnız gerçek `status.input_overflow` sayan
`input_overflow_count`.

### 4. Alpha gate bütünlüğü — corpus artık pinlenmiş

- **`tool_ok` eksikse artık `VERİ YOK`.** Eskiden `iso.get("tool_ok", iso_runs)` ile "eski format,
  temiz varsay" deniyordu; `{"runs": 20, "leaks": []}` dosyası `file_list`'in hiç çalışıp
  çalışmadığını gösteremez ve aracı hiç çağırmayan bir ajan zaten sızdıramaz — yani bayat bir dosya
  hiç içermediği kanıtla yeşile dönüyordu.
- **Yeni `docs/eval/gate_core_manifest.json`** (14 senaryo): `schema_version`, `corpus_version`,
  `required_runs`/`required_isolation_runs`, `driver_commit` + her Gate Core senaryosu için hem
  driver prompt'unun (lambda'nın kendi kaynağı) hem oracle `Expected`'ının SHA-256'sı, artı iki
  toplam digest. Senaryo kümesi `alpha_gate.py`'nin satır sabitlerinden TÜRETİLİYOR, yeniden
  listelenmiyor — gate'e sınıf eklemek senaryoyu pinlemeyi unutamaz.
  `manifest --write` üretir, `manifest` doğrular, **`evaluate` bunu birinci sınıf gate satırı
  olarak kontrol eder**: drift = `KALDI`, manifest yok/eski şema = `VERİ YOK`.
- Yeni isolation özetleri `schema_version` + `driver_commit` damgası taşıyor.

### 5. İki P2 daha kapatıldı

- **`_latest_workflow_id()` determinizmi:** alt sınır saniye çözünürlüklü `time.strftime()`
  karşılaştırmasıydı; aynı saniyede oluşan iki workflow ayırt edilemiyordu. Artık istekten ÖNCE
  alınan audit log SATIR SAYISI (append-only dosyada saat çözünürlüğünden bağımsız olarak kesin).
  Yeni `tests/test_driver_workflow_id.py`.
- **Çıplak "Evet." xfail'i `strict=True` yapıldı:** yorumu "gelecekte düzelirse fark edilsin
  (XPASS)" diyordu ama `strict=False` bunu ZORLAMIYORDU — non-strict bir XPASS koşuyu
  düşürmez. **Bu, `voice_e2e` katmanında çalıştırılmamış bir sözleşme değişikliğidir** (gerçek
  Whisper/Piper gerektirir, owner koşmalı).

Yan etki olarak: `manual_test_driver.py`'nin import anında `sys.stdout`'u sarmalaması, pytest'in
capture buffer'ının sahipliğini alıp GC'de kapattığı için onu import eden HER testi teardown'da
"I/O operation on closed file" ile öldürüyordu. Rewrap artık koşullu (zaten UTF-8 ise atlanır) +
`alpha_gate._load_driver()` ayrıca throwaway bir stdout gösteriyor.

### 6. Self-review'de bulunan 2 kusur (owner talebiyle diff baştan gözden geçirildi)

Yukarıdaki iş bittikten sonra owner review istedi; kendi diff'imde iki gerçek kusur çıktı, ikisi de
düzeltildi ve regresyon testleri **düzeltme kaldırılarak kırmızı olduğu ölçülerek** doğrulandı:

- **`None` dalı uçuşta olan önceki turu eziyordu (1. maddenin kendi regresyonu).** `result is None`
  geldiğinde tur-sonu işlemi KOŞULSUZ uygulanıyordu; daha önceki bir `turn_task` hâlâ
  çalışıyorsa response ekseni `idle` yapılıyordu (duyulabilir TTS'in üstüne "sessizlik") ve
  `stop_after_first_turn` ile `turn_complete` dönülüp o tur `finally`'de İPTAL ediliyordu. Artık
  `turn_task is None` guard'ı var; çalışan tur her ikisine de kendisi sahip.
  Test: `test_a_synchronous_turn_does_not_cancel_an_in_flight_earlier_turn` (guard kaldırılınca
  `finished == []` ile turun iptal edildiği ölçüldü).
- **Kayıt yapmadan düşen remote `/ws` oturumu, yerel loop'un diagnostics kaydını siliyordu.**
  `engine.load()` patlarsa `audio_io` set ama registration yapılmamış olur; `_stop_audio_session`
  o durumda `restore_session(None)` çağırıp registry'yi temizliyordu — yerel loop kendini yalnız
  bir kez kaydettiği için `/voice/status` süreç ömrü boyunca "oturum yok" derdi. Ayrı bir sentinel
  ile "hiç kaydedilmedi" durumu `None`'dan ayrıldı.

**Bu revizyonda kabul edilen, bilinçli sınırlar:** CLI `/voice-status` yalnız `--ptt` kapısında
erişilebilir (sesli modda yazılı girişin tek noktası orası; `--wakeword` `ww_detector.listen`'de
bloklu, düz `--voice`'ta hiç kapı yok) — sunucu tarafını `GET /voice/status` kapsıyor.
`scenario_digests` `inspect.getsource(lambda)` kullandığı için çok satırlı bir lambda'da
beklenenden fazla satır yakalayıp yanlış-pozitif drift üretebilir; anti-gaming kontrolü için
güvenli yön bu.

### Test/lint (2026-07-25, self-review düzeltmeleri dahil, bu oturumun sonunda)
```powershell
python -m pytest -q                      # 1454 passed, 5 deselected (voice_e2e), ~3.5 dk
python -m ruff check jarvis scripts tests # All checks passed!
```
Önceki oturuma göre +65 test (bu oturumda 4 yeni test dosyası + mevcutlara eklemeler).
`voice_e2e` katmanı (5 test) koşulmadı — gerçek Whisper/Piper model cache'i gerektiriyor.

### Commit durumu

Bu oturumda **2 iş commit'i** (ses altsistemi remediation'ı; eval/gate bütünlüğü) ve bu kapanış
HANDOFF commit'i. Kalıcı kural gereği bu kapanış commit'inin kendi SHA'sı/push/CI sonucu burada
yazılmaz. Oturum sonunda **local == origin senkrondu**. Branch ucunun CI durumuna
`gh run list --branch langgraph-migration` ile canlı bakın.

`.claude/settings.local.json` bilinçli olarak commit EDİLMEDİ — oturum-yerel izin listesi birikimi,
bu işin parçası değil.

### Kapsam dışı bırakılanlar (bilinçli, gizlenmedi)

- **W18/R24 redesign (incelemenin 5. maddesi):** approval-pause/bağımlılık gerektiren, doğrudan
  tool çağrılarıyla eşdeğer biçimde yapılamayacak bir workflow tasarımı — canlı model koşusu
  gerektirir, bu ajan tetikleyemez. Alpha gate bu yüzden HÂLÂ `EKSİK VERİ`; bu oturum gate'in
  ölçüm bütünlüğünü düzeltti, gate'i GEÇTİ'ye taşımadı.
- **Corpus'u gerçek 40-60'a çıkarma (6. madde):** 31 tasarım ID'si / 27 wired turn olduğu gibi.
- **Faz C (7. madde):** owner'ın önceki kararıyla erteli.

## SONRAKİ OTURUM — kalan iş (öncelik sırası)

1. **Owner'ın canlı doğrulaması** (bu ajan tetikleyemez):
   - `--ptt` gerçek Enter + gerçek mikrofonla: bir tur → Enter kapısına dönüş; ayrıca
     "flash modeline geç" deyip kapının GERÇEKTEN geri geldiğini görmek (bu oturumun 1. bug'ı).
   - `--ptt` kapısında `/voice-status` yazıp tablonun canlı sayaçları gösterdiği.
   - Faz A: sesli onay dumanı (audit'te `user_approved` + `execution_end` + gerçek takvim etkinliği).
2. **Alpha gate'i gerçekten GEÇTİ'ye taşımak** — `ab_run_config.ps1 -Runs 10` +
   `alpha_gate.py isolation --runs 20` + `evaluate --runs 10`. **Ön koşul:** W18/R24'ün
   `workflow_start` güvenilirlik boşluğu (`docs/eval/acceptance_matrix.md`'nin disposition notu).
   Gate Core manifest'i corpus değişirse `manifest --write` ile yeniden mintlenmeli.
3. **Faz C** — gerçek Takvim/Gmail entegrasyon runner'ı. 3 parametre netleşti (mevcut token'lar,
   runner-oluşturur takvim, self-send adresi `mertkaanakgunlu@gmail.com`); plan dosyasının Faz C
   bölümü hâlâ geçerli tasarım. **En riskli faz — gerçek yan etki üretir.**
4. **İki gerçek model-yeteneği bulgusu (değişmedi):** `workflow_start` canlı modelle güvenilir
   tetiklenmiyor (4/4 denemede hiç); `plot_data` bazen çağrılmak yerine Python kodu gösteriyor
   (2/2, B6'da güvenilir çalışırken).
5. **Owner Extended Corpus'u 40-60'a çıkarma.**
6. Eski kalanlar (değişmedi): 4 worktree branch read-through (ayrı go-ahead bekliyor); Electron
   `npm audit` 8 advisory; mobile flutter-analyze; canlı HUD E2E (onay approve/deny tıklaması);
   Alt+Space'in `/voice/ptt/start`'a Electron'da kablolanması; wake-word modelinin neden
   yüklenmediği (openwakeword, düşük öncelik).

## Değişmeyen taşınan işler

- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
