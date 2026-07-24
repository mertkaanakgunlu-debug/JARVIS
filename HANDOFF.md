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

## Son oturum: 2026-07-24/25 — METİN-ÖNCE SPRINT: 6/7 FAZ TAMAMLANDI (B0→B1→B2→D→F→E); FAZ C (GERÇEK ENTEGRASYON) BİLİNÇLİ OLARAK ERTELENDİ

**Durum tek cümlede:** Owner'ın "metin-first, PTT-first" stratejik yönlendirmesi tam bir sprint
planına dönüştürüldü (owner'ın 9 mimari revizyonuyla), yürütüldü ve büyük ölçüde canlı doğrulandı;
yalnızca en riskli faz (Faz C — gerçek Takvim/Gmail entegrasyonu) owner'ın açık isteğiyle bu
oturumda BAŞLATILMADI, taze bir oturuma bırakıldı.

### Plan dosyası
`C:\Users\mertk\.claude\plans\benim-karar-m-metin-first-partitioned-leaf.md` (v2 — owner'ın 9
revizyonu uygulandıktan sonra onaylandı). Sıra: A (owner-run, ayrı) → B0 → B1 → B2 → D → F → E → C.

### Faz B0 — Acceptance matrix + workflow feasibility spike (docs-only)
`docs/eval/acceptance_matrix.md` + `docs/eval/workflow_e2e_spike.md` yazıldı. **Gerçek bulgu:**
`WorkflowEngine._dispatch()`'in `execution_start`/`execution_end` audit kayıtları yalnız
`audit_log.jsonl`'e yazılıyor, `tool_trace.jsonl`'e değil (o yalnız blok/veto yolunda yazıyor) —
oracle'ın mevcut trace-tabanlı scoring'i workflow senaryolarını hep sahte-FAIL ederdi. Ayrıca "diğer
recovery sınıfları" tek satırdan 7 bağımsız yargılanabilir sınıfa bölündü.

### Faz B1 — Alpha gate'in iki `VERİ YOK` satırı kapatıldı
`eval_oracle.py`'ye workflow-farkında scoring (`Observed.workflow_status`/`audit_rows`,
`Expected.expected_workflow_status`/`expected_step_statuses`/`expected_audit_capabilities_ok`/
`workflow_forbidden_claims`). `jarvis/api.py`'ye **yeni `GET /tasks/{task_id}` endpoint'i**
eklendi — `task_executor.py`'nin kendi docstring'i bunu hep vaat ediyordu ama hiç var olmamıştı;
canlı test sırasında keşfedildi (gerçekçi çok-adımlı bir prompt `_should_async()`'in 40-kelime
eşiğini rutin olarak aşıyor ve async'e sapıyor, ve o zamana kadar sonucu almanın HİÇBİR HTTP yolu
yoktu). `alpha_gate.py`'nin "diğer recovery sınıfları" satırı artık 7 satır: 4'ü gerçek
driver/canlı-model senaryosu (W18, R20, R21, R23), 3'ü (invalid-args repair, timeout, compensation
failure) **mekanizma testi** — gerçek pytest'i her `evaluate()` çağrısında subprocess olarak yeniden
çalıştırıyor (`mechanism_row()`, testlerde injectable), çünkü bunlar canlı bir modelin sabit bir
zamanlamada zorlayamayacağı motor-içi özellikler ve zaten gerçek, deterministik pytest kapsamları
vardı (`test_bounded_repair.py`, `test_timeout_enforcement.py`, `test_workflow_compensation.py`).

### Faz B2 — Owner Extended Corpus, canlı doğrulandı
`docs/eval/owner_extended_corpus.md`: owner'ın 15 sınıflık tablosu için 40-60 senaryo tasarımı;
27 driver ID'si (`test` profilinde çalışabilenler) `manual_test_driver.py`'ye eklendi. **Gerçek yerel
sunucu + Ollama modeline karşı 2 tur canlı koşuldu:** 1. tur 13/18 geçti; 5 başarısızlığın 3'ü bu
corpus'un kendi tasarım hatasıydı (düzeltildi: R21'in aynı prompt'unu tekrarlayan bir kopya,
`required_any` regex'i çok dar, `OC28b`'de unutulan `decision="approve"`), 2. tur (düzeltmelerden
sonra) 6/7 geçti.

### Faz D — Ses gözlemlenebilirlik: iki-eksenli state reducer + telemetri
Yeni `jarvis/voice/state.py` (`VoiceState` — orchestration-owned, engine-owned DEĞİL, owner'ın
mimari düzeltmesi gereği: full-duplex ses LISTENING+SPEAKING'in aynı anda doğru olmasını
gerektiriyor). Yeni `TurnEnded` event'i (`jarvis/voice/events.py`), engine VAD turn-end'i
STT'den ÖNCE yayınlıyor (önceden FinalTranscript'e kadar hiçbir sinyal yoktu, boş bir tur bile
sessizce `continue` ediyordu). `cli.py`'de statik "Thinking..." yerine canlı state satırı +
başlangıç diagnostics (`_print_voice_diagnostics`: cihaz, örnekleme hızı, gerçek STT cihazı).
`io_duplex.py`'de **2 gerçek ölü telemetri düzeltildi**: `underrun_count` tanımlıydı ama hiç
artırılmıyordu (düzeltildi + test edildi); input overflow yalnız `logger.debug`'a gidiyordu, yeni
`input_overflow_count` + `queue_depth()` eklendi.

### Faz F — WAV replay harness, iki katman
Yeni `jarvis/voice/io_wav.py` (`WavAudioIO`) — `AudioIO` Protocol'ünün gerçekten saf-ekleme
olduğunu kanıtladı (engine hiç değişmedi). `WhisperSTT.transcribe()` artık `TranscriptionResult`
dataclass'ı (`stt_s` dahil) döndürüyor; `TurnEnded` `vad_prob_max`/`mean` kazandı — üçü de
"hesaplanıp atılan" alanlardı. İki test katmanı: `test_wav_replay.py` (normal CI, sahte VAD+STT,
ağ/model bağımlılığı yok, 13 test) ve `test_wav_replay_e2e.py` (`voice_e2e` marker'ı,
`pyproject.toml`'un `addopts`'u ile normal koşudan HARİÇ — gerçek Silero VAD + gerçek Whisper +
gerçek Piper-sentezlenmiş `tests/audio/*.wav`). **Canlı bulgu:** tek kelimelik çıplak "Evet."
Piper→Whisper'da GÜVENİLİR DEĞİL (gözlenen: "Devleti."/"Rövlet." — ayrı çalıştırmalarda farklı),
aynı ifade doğal cümle bağlamıyla ("Evet, onaylıyorum.") güvenilir. Silinmedi — dürüst bir
`xfail` olarak kaydedildi (gerçek gözlenen transkriptlerle) ki gelecekte düzelirse fark edilsin.

### Faz E — CLI push-to-talk
Yeni `--ptt` bayrağı (`--voice`'u ima eder, `--wakeword` gibi). Press-to-ARM, gerçek
hold-to-talk DEĞİL (owner'ın düzeltmesi gereği): Enter'a bas → VAD sessizlikle bitirsin.
`--voice`'un mevcut sürekli-dinleme davranışı tamamen değişmedi. **Canlı interaktif doğrulanmadı**
(gerçek bir Enter tuşuna basmayı bu ajan tetikleyemez) — owner'ın canlı geçişi bekliyor, Faz A'nın
sesli-onay dumanı gibi. Electron HUD'un düz-Space handler'ının `/voice/ptt/start` değil
remote-audio session'ı tetiklediği yeniden doğrulandı (B0'daki bulguyla aynı, regresyon değil) —
iki meşru, farklı kullanım durumu (uzak-akış mikrofon vs. yerel backend mikrofonu), Alt+Space
kablolaması hâlâ yapılmadı, plan'ın kendi koşullu ifadesine göre ("gerekirse") ertelendi.

### Faz C — BİLİNÇLİ OLARAK BAŞLATILMADI (owner kararı, bu oturumun sonunda)

Owner'a Faz C'ye başlamadan önce 3 soru soruldu, cevaplar kaydedildi (whoever bu fazı alırsa):
- **Kimlik bilgisi:** mevcut OAuth token'ları (`data/.calendar_token.json`, Gmail) yeniden
  kullanılacak — `paths.py`'nin `JARVIS_HOME` yönlendirmesiyle integration home kendi kopyalarını
  bunlardan türetsin, ayrı bir consent akışı GEREKMİYOR.
- **Takvim:** owner önceden oluşturmayacak — runner ilk çalıştırmada `calendars().insert()` ile
  "JARVIS Integration Tests" takvimini kendisi oluştursun (idempotent, zaten varsa yeniden kullansın).
- **Gmail self-send adresi:** `mertkaanakgunlu@gmail.com` (CLAUDE.md'nin userEmail'i).
- **Zamanlama:** owner bu oturumda DURDURALIM dedi — Faz C (en riskli, gerçek yan etkili) taze bir
  oturumda ele alınacak.

Plan dosyasının Faz C bölümü (armed-guardrail checklist, allowlist policy_guard'da, dedicated
`calendar_target_id` parametrizasyonu, structured event/message ID, domain-specific postcondition
kind'ları) hâlâ geçerli — hiçbir kod yazılmadı, yalnızca yukarıdaki 3 parametre netleşti.

### Test/lint (2026-07-24/25, bu oturumdaki her fazın SONUNDA ayrı ayrı koşuldu)
```powershell
python -m pytest -q         # her faz sonrası: 1338 → 1338 → 1338 → 1375 → 1389+5 deselected (voice_e2e)
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/alpha_gate.py scripts/ab_launch_server.py
# her fazda: All checks passed!
```
Bir ara full-suite koşusunda 5 test (test_procedure_store/test_shell_workspace/test_todo_bg_analysis
— hiçbiri bu oturumda dokunulan dosyalarla ilgili değil) geçici olarak başarısız oldu; izole
çalıştırıldıklarında ve full-suite'in hemen sonraki tekrar koşusunda (aynı kod, 1375/1375) hepsi
geçti — pre-existing full-suite-sırası kararsızlığı, bu oturumun değişiklikleriyle ilgisiz bir
regresyon değil.

### Owner'ın canlı doğrulanmasını bekleyen (bu ajan tetikleyemez)
- Faz A: sesli onay dumanı ("Yarın 15.00'e test etkinliği ekle" → "Evet." → audit'te ilk
  `user_approved` + `execution_end` + gerçek takvimde etkinlik + "Güle güle" çıkışı).
- Faz E: `--ptt` modunun gerçek Enter-tuşu + gerçek mikrofonla interaktif geçişi.
- Faz D/F'nin canlı mikrofon üzerinde gözlemlenmesi (state satırı, `/voice-status` benzeri
  diagnostics, WAV-replay'in ima ettiği "sorun VAD/STT'de mi yoksa cihaz/PortAudio'da mı" ayrımı).

### Commit ve push durumu

Bu oturumda **6 iş commit'i** (B0→B1→B2→D→F→E, her biri kendi pytest+ruff doğrulamasıyla) + bu
kapanış docs commit'i. Kalıcı kural gereği bu kapanış commit'inin kendi SHA'sı/CI'ı burada yok.
Oturum sonunda **local, origin'in 6 iş commit'i + bu docs commit'i kadar ilerisindeydi — push
EDİLMEDİ** (owner'dan push için ayrı bir istek gelmedi; git safety protokolü gereği push açık
istek olmadan yapılmaz). Branch ucunun CI durumuna `gh run list --branch langgraph-migration`
ile canlı bakın — bu commit'ler henüz push edilmediği için CI'a hiç girmediler.

## SONRAKİ OTURUM — kalan iş (öncelik sırası)

1. **Push kararı** — owner isterse bu 6+1 commit'i `origin/langgraph-migration`'a push edin,
   `gh run list` ile CI'ı canlı doğrulayın.
2. **Faz C** — gerçek Takvim/Gmail entegrasyon runner'ı. Yukarıdaki 3 parametre netleşti (mevcut
   token'lar, runner-oluşturur takvim, self-send adresi); plan dosyasının Faz C bölümü (armed
   guardrail, allowlist, structured ID, domain postcondition, teardown) hâlâ geçerli tasarım.
   **En riskli faz — gerçek yan etki üretir, dikkatli ilerleyin.**
3. **Alpha gate'i gerçekten GEÇTİ'ye taşımak** — B1 mekanizmayı kurdu ama owner'ın kendi
   `ab_run_config.ps1 -Runs 10` + `alpha_gate.py isolation --runs 20` + `evaluate --runs 10`
   koşusu hâlâ yapılmadı. Ayrıca W18/R24'ün workflow_start güvenilirlik boşluğu (aşağıya bakın)
   kapanmadan bu iki satır de facto VERİ YOK kalır.
4. **İki gerçek model-yeteneği bulgusu, owner'ın bilmesi gereken:**
   - `workflow_start` canlı modelle güvenilir tetiklenmiyor (4 denemede hiç, açıkça isimlendirerek
     bile) — `docs/eval/acceptance_matrix.md`'nin disposition notuna bakın.
   - `plot_data` bazen çağrılmak yerine Python kodu gösteriyor (2/2 tekrarlandı, B6'da güvenilir
     çalışırken) — `docs/eval/owner_extended_corpus.md`'ye bakın.
5. Faz A + Faz E'nin owner tarafından canlı doğrulanması (yukarıya bakın).
6. Eski kalanlar (değişmedi): 4 worktree branch read-through (ayrı go-ahead bekliyor); Electron
   `npm audit` 8 advisory; mobile flutter-analyze; canlı HUD E2E (onay approve/deny tıklaması);
   Alt+Space'in `/voice/ptt/start`'a Electron'da kablolanması (Faz E'de "gerekirse" olarak
   ertelendi); wake-word modelinin neden yüklenmediği (openwakeword, düşük öncelik).

## Değişmeyen taşınan işler

- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
