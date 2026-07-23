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

## Son oturum: 2026-07-23 (19. oturum) — 18. OTURUMUN UNCOMMITTED İŞİ İKİNCİ BİR BAĞIMSIZ DEĞERLENDİRMEYLE DOĞRULANDI, EKSİK TEST KAPANDI, ELECTRON CI BLOCKING YAPILDI, 2 COMMIT PUSH'LANDI VE CANLI CI'DA YEŞİL DOĞRULANDI

**Durum tek cümlede:** Owner, 18. oturumun uncommitted diff'ine (atomik `claim_pending_
confirmation()` + üçüncü, korumasız `/ws` çağrı sitesi + 2 küçük bulgu) karşı yapılan İKİNCİ,
bağımsız bir değerlendirmeyi yapıştırdı — bu değerlendirme 18. oturumun 5 teknik iddiasının
hepsini gerçek koddan doğrulamış ve "her iki soruya da evet: Electron CI blocking yapılsın,
düzeltmeler commit+push'lansın" kararını vermiş, ayrıca commit öncesi 2 ek doğrulama (cross-thread
safety, 5 belirli test senaryosu) ve tam validation seti istemişti. Owner'ın kendi "dış review'ı
ampirik doğrula, körü körüne uygulama" disiplini uygulandı: her istenen şey kör kabul edilmek
yerine gerçek koddan tek tek doğrulandı.

**Thread-safety (yeni doğrulama, kod okunarak — sonuç: ek lock GEREKMİYOR).**
`_pending_confirmations`'a erişebilecek her arka-plan thread'i tek tek okundu:
- `JarvisMonitor` gerçek bir `threading.Thread` kullanıyor (`jarvis/monitor.py`) ve
  `agent.proactive_turn()`'ü çağırıyor — ama `proactive_turn()`'ün kendi kodu (docstring değil,
  gerçek gövde) bir L3 interrupt'ı ASLA `_pending_confirmations`'a register etmiyor, doğrudan
  discard edip bildirim olarak dönüyor.
- `TaskExecutor` gerçek bir `ThreadPoolExecutor` kullanıyor (`jarvis/task_executor.py`) ve
  `agent.background_turn()`'ü çağırıyor — o da aynı şekilde, kendi kodunda, bir interrupt'ı
  register ETMEDEN `ConfirmationRequired` fırlatıyor ("there is no resumption path for a
  background thread_id, so the interrupt is not registered").
- Wakeword dedektörünün `listen()`'ı `run_in_executor` ile ayrı bir thread'de çalışıyor
  (`jarvis/voice_api.py`) ama saf blocking ses tespiti — agent/confirmation koduna hiç dokunmuyor.

Sonuç: bu dict'e TÜM gerçek erişim (register/has/claim/pop), desteklenen her modda (CLI, CLI+voice,
`--api` [+voice] [+wakeword] [+monitor]) tek bir asyncio event loop'una hapsedilmiş — senkron
`dict.pop()` bu yüzden mimari olarak yeterli, ek bir `threading.Lock` gerekmiyor.

**5 istenen test senaryosu (doğrulandı/tamamlandı):**
1. İki claim denemesinden tam biri başarılı — zaten vardı (`test_second_claim_of_the_same_id_
   returns_none`, `tests/test_pending_confirmations_ttl.py`).
2. Süresi dolmuş bir onay, opportunistic cleanup tetiklenmese bile reddedilir — dict'in kendi TTL
   süpürmesi bir sonraki registration'a kadar çalışmayabilir, AMA asıl güvenlik sınırı farklı
   (daha temel) bir katmanda zaten test ediliyor: grafiğin `confirmation_node`'undaki HMAC
   imza/expiry doğrulaması (`jarvis/execution/approval.py`), `test_expired_approval_is_denied`
   (`tests/test_prepare_execution_node.py`) ile. Dict'in TTL süpürmesi sadece bellek sızıntısını
   önleyen best-effort bir mekanizma (kendi docstring'i bunu söylüyor), asıl güvenlik sınırı
   değil — iki katman ayrı ayrı test edilmiş durumda ve aralarında dallanan bir mantık yok, o
   yüzden ek bir entegrasyon testi orantısız görüldü.
3. Remote `/ws` ses yolu, dışarıdan çözülmüş bir onayı yeni bir cümle olarak işler — GERÇEK
   BOŞLUKTU, hiç testi yoktu. Yeni `tests/test_ws_remote_confirmation_claim.py` (2 test): gerçek
   `_handle_transcript` closure'ını (`jarvis/api.py`'nin `/ws` endpoint'i)
   `starlette.testclient.TestClient` ile gerçek bir WebSocket bağlantısı üzerinden, sahte
   `drive_voice_session`/`RealtimeVoiceEngine` ile (gerçek ses/STT/TTS'e hiç dokunmadan) uçtan
   uca çalıştırıyor — reimplementasyon değil, gerçek kod.
4. Bir `pre_claimed` girdisi iki kez tüketilemez — zaten vardı (`test_claim_removes_and_returns_
   the_entry` + `test_second_claim_of_the_same_id_returns_none` + yeni `test_resume_and_stream_
   uses_pre_claimed_without_touching_the_dict`).
5. Normal HTTP `/chat/confirm` yolu `pre_claimed` olmadan çalışmaya devam ediyor — zaten vardı
   (`test_resume_and_stream_without_a_second_interrupt_completes_normally` vb., `pre_claimed=None`
   default'unu tetikliyor); `chat_confirm()` endpoint'inin kendi kodu bu diff'te hiç değişmedi.

**Tam validation (2026-07-23, bu değişikliklerle koşuldu):**
```powershell
python -m pytest -q       # 1271 passed, 0 failed (206s) — FULL suite (1269 + yeni 2 /ws testi)
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py scripts/alpha_gate.py   # All checks passed!
cd electron; rm -rf node_modules; npm ci; npm test; npm run build   # npm ci temiz (8 önceden-var advisory, değişmedi); vitest 13/13; build temiz
```

**Electron CI blocking (owner kararı, bu değerlendirme üzerinden).** `.github/workflows/ci.yml`'nin
`electron` job'ından `continue-on-error: true` kaldırıldı — job artık `python` job'ı gibi
blocking. Gerekçe: Electron artık sadece "best-effort companion client" değil, L3 confirmation
prompt'unu render eden ve approve/deny round-trip'i tamamlayan güvenlik-kritik yol (`52d0b72`) VE
manual alpha kabul sürecinin birincil arayüzü — parser/test/build kırılması artık sessizce
geçilemez. `mobile` job'ı bilinçli olarak best-effort kaldı (Dart/CI-altyapı gürültüsü, bu
projenin kapsamı dışı, değişmedi).

**Commit ve push durumu:** 2 iş commit'i + bu kapanış docs commit'i.
- `ff357fc` — `fix(confirmations): atomically claim pending confirmations across transports`
  (18. oturumun `jarvis/agent.py`/`jarvis/voice/session.py`/`jarvis/api.py`/`jarvis/cli.py`/
  `jarvis/voice_api.py` düzeltmeleri + 3 güncellenen test dosyası + madde 3'ün yeni
  `tests/test_ws_remote_confirmation_claim.py` dosyası).
- `1faa2c5` — `fix(ci): bind isolation traces and gate Electron regressions`
  (`scripts/alpha_gate.py`'nin isolation trace tur-bağlama düzeltmesi + `jarvis/memory.py`'nin
  `Memory.close()` log'u + `tests/test_memory_lifecycle.py` + `.github/workflows/ci.yml`'nin
  Electron blocking değişikliği).

Push öncesi `git fetch` + `git rev-list --left-right --count origin/langgraph-migration...HEAD`
ile temiz bir fast-forward doğrulandı (origin 0 commit ileride, local 2 commit ileride,
beklenmedik remote commit yok). `git push origin langgraph-migration` → `174f8c2..1faa2c5`
fast-forward, force yok.

**`1faa2c5`'in push'unun CI run'ı (30033048953) canlı izlendi ve `gh run view --json conclusion`
ile doğrulandı:** genel `conclusion: "success"`. Job bazında: `python` → `success` (8m46s, ruff +
1271 pytest), `electron` → `success` (40s, `npm ci`/`npm test`/`npm run build` üçü de gerçekten
geçti — artık blocking olarak ilk kez yeşil), `mobile` → `failure` (bilinen kozmetik `flutter
analyze`, `continue-on-error`, genel `conclusion`'ı etkilemiyor — tasarım gereği).

Kalıcı kural gereği bu kapanış docs commit'inin kendi SHA'sı/CI'ı burada yok — uç CI'ına
`gh run list --branch langgraph-migration` ile bakın. Oturum sonunda local == origin senkrondu.

### Önceki oturumların özeti (17-18, detay: CHANGELOG.md)

17. oturum Faz 8'i (registry sweep, per-capability contract testleri, hypothesis property-fuzz,
13 sınıflı hata taksonomisi, `alpha_gate.py`) ve Electron'un L3 confirmation UI'ını (paylaşılan SSE
reader, amber `ConfirmationOverlay`, WS dinleme, `conversation_id` persistence) inşa etti, sonra bu
diff'e karşı gelen bağımsız bir review'ın 4 bulgusunu doğrulayıp düzeltti (cross-transport
confirmation race, `alpha_gate.py`'nin exit-code/isolation açığı, chromadb flake kök nedeni,
Electron `chatStream.js` sağlamlığı) — hepsi commit'lendi ve push'landı (`7d6a7af`..`0687772`
arası, docs `174f8c2` ile kapandı), CI'da canlı yeşil doğrulandı. 18. oturum bu review'ın kendi
diff'ini bağımsız olarak tekrar doğrularken cross-transport fix'in atomik olmadığını + 3. korumasız
çağrı sitesini buldu, düzeltti, uncommitted bıraktı — bu oturum (19.) o işi commit'leyip push'ladı.
Tam detay: `CHANGELOG.md`'nin "Electron: L3 confirmation..." ve "Independent review response..."
girdileri.

## SONRAKİ OTURUM — kalan iş

1. **CANLI HUD E2E'si (değişmedi):** server + Electron + gerçek APPROVE/DENY tıklaması — `52d0b72`,
   17. oturumun cross-transport düzeltmesi (`599066d`) VE 19. oturumun atomik claim düzeltmesinin
   (`ff357fc`) gerçek kabul testi. Test etmek için: sesle bir L3 aksiyon başlat, HUD'dan onayla,
   SONRA sesle alakasız bir şey söyle — düzeltmeden önce bu ikinci komut yutulurdu; şimdi ayrıca
   eşzamanlı bir ikinci onay denemesinin (HUD + ses aynı anda) yalnız BİRİNİN kazandığını da
   doğrulamak gerekir (atomik claim'in asıl iddiası).
2. **Model tool-calling güvenilirliği** (14. oturumdan; taksonomiyle ölçülebilir):
   `false_success_claim`'i 0'a indirme — alpha kapısının asıl kilidi.
3. Alpha gate'in owner-koşusu ölçümleri (10-run `ab_run_config.ps1` + `isolation`) + 2 kapsama
   boşluğu senaryosu (uzun-workflow E2E, block/veto-dışı recovery sınıfları).
4. Branch ucunun CI'ı: `gh run list --branch langgraph-migration -L 3` ile kontrol et (bu
   dosyanın kendi kapanış commit'i dahil).
5. **Yeni, küçük, bilinçli kapsam dışı bırakılan:** `Memory()` inşa eden DİĞER test dosyaları
   (bugüne dek yalnız en açık ilişkili ikisi — shadow-replay + procedure-store — düzeltildi) hâlâ
   `close()` çağırmıyor; her biri kendi System'ini süresiz sızdırıyor (zararsız ama gereksiz —
   process pytest'in kendisi bittiğinde zaten temizleniyor). Repo-geneli bir "her Memory()
   testi close() etsin" taraması yapılmadı — orantısız kapsam genişlemesi olurdu, ayrı bir
   oturumun işi olabilir.
   `electron/`'da `npm audit` 8 önceden-var advisory gösteriyor (electron/vite/esbuild/babel'ın
   kendi CVE'leri, bu oturumdan önce de vardı) — düzeltmeleri kırıcı sürüm atlamaları (electron
   43, vite 8) gerektiriyor, bilinçli olarak ertelendi.
6. Eski kalanlar (değişmedi): `workflow_start` JSON `steps` güvenilirliği; gerçek CLI REPL E2E;
   Faz 6 Kısım 3 Literal-terfi; Faz 5 kalanları; canlı A/B B6 sorusu; 4 worktree branch; mobile
   flutter-analyze info/warning.

## Değişmeyen taşınan işler

- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Pre-first-turn kozmetik model label — değişmedi.
