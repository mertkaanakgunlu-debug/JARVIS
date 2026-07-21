# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-21 (8. oturum) — AGENT RUNTIME REV.2 FAZ 2+3 BİTTİ, PUSH BEKLİYOR

**Durum tek cümlede:** Faz 2 (`prepare_execution` + HMAC onay bağlama + idempotency journal) ve
Faz 3 (timeout enforcement + postcondition verification runner) ikisi de **kodlandı, test edildi,
673 pytest yeşil, ruff temiz — ama commit/push edilmedi**: bu oturum sahibin açık "commit et"
talebi almadı, talimatlar commit'i yalnızca açıkça istendiğinde yapmamı söylüyor. Çalışan ağaç şu
an her iki fazın değişiklikleriyle dolu (aşağıda liste). Sahip onaylarsa commit+push sıradaki adım.

## Bu oturumda yapılanlar (Faz 3 — Faz 2 önceki handoff'ta zaten yazılıydı)

Plan metninin (`C:\Users\mertk\.claude\plans\c-users-mertk-desktop-gpt-analysis-md-s-delegated-scone.md`
§Faz 3) iki parçası: **timeout semantiği** + **postcondition doğrulama runner'ı**.

1. **Timeout classification** (`jarvis/tool_registry.py`'nin yeni `_TIMEOUT_CLASSES`'ı) — 36
   tool'un HER biri gerçek bir sınıfa atandı (`ToolSpec.timeout_class` Faz 1'den beri her tool için
   varsayılan `"cooperative_async"` idi — bu HER tool için dürüst değildi). `hard_process_timeout`
   (shell_run/python_run/report_compile), `cooperative_async` (native async sub-agent'lar),
   `external_request_timeout` (12 ağ-bağımlı tool — gerçek client-seviyesi timeout BU FAZDA
   YAPILMADI, dürüstçe belgelendi), `soft_thread_timeout` (gerisi). `geo_math` bilinçli muhafazakâr:
   çoğu action'ı `async def` içinde await noktası OLMADAN ağır senkron iş yapıyor — event loop'un
   TAMAMINI blokluyor, thread'den de kötü — düzeltilmedi (executor-thread refactor kapsam dışı),
   yalnız belgelendi.
2. **Gerçek uygulama iki katmanda:** (a) `shell.py`/`python_exec.py`/`latex.py`'nin KENDİ
   `subprocess.run(timeout=...)` çağrılarına `ToolSpec.timeout_seconds` artık gerçekten ulaşıyor
   (öncesinde hardcoded, ToolSpec'ten bağımsız sabitler — shell_run'ın ToolSpec'i 120s diyordu,
   gerçek subprocess çağrısı hardcoded 30s kullanıyordu). `python_exec.py` artık
   `TimeoutExpired`'ı kendi yakalamıyor (eskiden yapılandırılmamış bir `[ERROR]` string'i
   dönüyordu) — paylaşılan sınıra propagate ediyor. (b) `safe_tools.py`'nin `_awrap_tool_call`'ı
   HER çağrıyı `asyncio.wait_for` ile sınırlıyor (`ToolSpec.timeout_seconds`, hard_process için
   +10s tampon). `format_tool_error()` category=="timeout" olduğunda iki dürüst alan ekliyor:
   `execution_may_still_be_running` (yalnız cooperative_async için false) ve `worker_terminated`
   (yalnız GERÇEK `subprocess.TimeoutExpired` için true). `ExecutionEnvelope.status` artık
   `"timed_out"` olabiliyor (düz "failed" değil).
3. **`jarvis/execution/postcondition_runner.py`** (yeni) — Faz 1'in yalnız şekil tanımladığı
   `PostconditionSpec`'in gerçek runner'ı, 8 kind'in hepsi (file_exists, path_within_workspace,
   file_openable, artifact_hash_matches, row_count_matches, series_matches, exit_code_matches,
   record_exists). Dürüstlük disiplini: çalıştırılabilir kontrol yoksa (parametre eksik, workspace
   yok, manifest yok) → `unverified`, asla sessizce "verified" değil. `series_matches` (B6'nın
   gerçek şekli — çizilen değerleri doğrulama) TAM uygulandı ve test edildi ama HİÇBİR canlı tool'a
   BAĞLANMADI — hiçbir tool henüz bir sidecar manifest üretmiyor (`plot_data`'nın PNG'si yanında
   veri dosyası yok) ve `expected_y`'yi dolduracak bir TaskContract extractor'ı yok; ikisi de bu
   fazın kapsamı dışında, dürüstçe belgelendi. Gerçek spec'ler bu faz yalnız **`file_write`**'e
   eklendi (file_exists + path_within_workspace, kendi `path` argümanına bağlı) — çıktı yolu
   doğrudan, belirsiz olmayan bir argüman olan TEK dosya-üreten tool (plot_data'nın `output`'u bir
   stem, report_write'ın yolu `title`'dan türetiliyor).
4. **`tool_result_accounting`** artık opsiyonel bir `workspace` parametresi alıyor (`graph.py`'den
   geçiriliyor) — envelope'lara timeout bayraklarını (`parse_timeout_flags`) ve postcondition
   sonuçlarını ekliyor, hepsi ZATEN VAR OLAN `mode != "off"` bloğunun İÇİNDE (off modu sıfır yeni
   kod çalıştırıyor, Faz 1'in rollback sözleşmesi korunuyor).

**Doğrulama sırası (checkpoint'li, tek seferde değil):** classification → 57 mevcut test yeniden
koşuldu → subprocess timeout wiring → mevcut shell/python_exec/latex/safe_tools testleri →
safe_tools enforcement → 25 yeni timeout testi (GERÇEK asyncio cancel + GERÇEK subprocess
timeout'ları, mock değil) → tam suite+ruff checkpoint (634 yeşil) → envelope genişletmesi →
postcondition_runner.py + 29 test → tool_accounting wiring + file_write'e gerçek spec + 10
entegrasyon testi → tam suite+ruff (673 yeşil). Hiçbir noktada `test_shadow_replay_equivalence.py`
bozulmadı — postcondition'lar/timeout bayrakları yalnız `execution_envelopes`'in İÇİNDE yaşıyor,
o alan zaten off/shadow karşılaştırmasından hariç.

## SONRAKİ OTURUM — kalan iş

1. **Bu oturumun (Faz 2 + Faz 3) değişikliklerini commit+push etmek — sahip onayı gerekiyor.**
   Talimatlar commit'i yalnızca açıkça istendiğinde yapmamı söylüyor. Sahip onaylarsa: iki fazı ayrı
   commit'ler olarak (plan dosyasında da ayrı fazlar), Faz 1'in commit zincirindeki üslupla, push.
2. **Faz 4 — Verified response composition + claim audit.** Plan §Faz 4, reviewer'ın en güçlü
   düzeltmesi. `compose_node` (`nodes.py:232`) artık ham `ToolMessage`'lardan değil,
   `ExecutionEnvelope[] → VerifiedExecutionSummary`'den beslenecek. Deterministik "operation
   status" bloğu koda gömülecek, modele bırakılmayacak. Claim extractor (TR/EN) kalır ama ikincil.
   **Kabul:** doğrulanmamış işlem iddiası kullanıcıya ulaşmıyor; kısmi başarı senaryosu (2
   tool'dan 1'i başarılı, model ikisini de iddia ediyor — bugün geçiyor) yakalanıyor. **Dürüst
   risk:** yerel 8B modele yalnız özet verildiğinde nesir kalitesi düşebilir — Faz 1'in shadow modu
   burada da geçerli (`annotate` ile ölç, sonra `enforce`).
3. **Canlı A/B'nin B6 sorusu hâlâ açık** (değişmedi) — deterministik replay + Faz 2/3 runtime'ı
   temizledi, geriye model nondeterminizmi kaldı; interleaved deney önerisi hâlâ ertelenmiş.
4. **`gh` CLI yetkilendirmesi — owner aksiyonu bekliyor** (değişmedi, 7. oturumdan).
5. **Şampiyon 62/65 referansı kontamine** (değişmedi, 7. oturumdan).
6. **Dış reviewer paketi** — `docs/review/2026-07-premerge-summary.md`, bu oturumda dokunulmadı.
7. **Bilinçli ertelenenler (değişmedi + Faz 3'ün kendi yeni kalemleri):**
   - W4b, `[BLOCKED]` sunum katmanı, qwen3.5/ministral-3 thinking-on, `stoic-spence` rolling
     summarization, `docs/ARCHITECTURE.md` orchestrator bölümü — hepsi önceki oturumlardan.
   - Faz 2'nin kapsam dışları: `target_resource` best-effort, TaskContract match hep
     `"no_contract"`, semantik cross-request idempotency sınıflandırılmadı.
   - Faz 3'ün yeni kapsam dışları: `external_request_timeout`'un 12 tool'u için gerçek
     client-seviyesi (per-kütüphane) timeout yok — yalnız genel dış asyncio.wait_for sınırı;
     `geo_math`'ın event-loop-bloklayan action'ları düzeltilmedi, yalnız belgelendi;
     `series_matches` hiçbir canlı tool'a bağlı değil (manifest üreticisi yok); diğer
     dosya-üreten tool'lar (plot_data/report_*/index_doc/note_append) postcondition almadı
     (çıktı yolları doğrudan argüman değil, türetilmiş/dönen).

## Ortam / komutlar
```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q                      # 673 test, offline, ~2.5 dk (pytest-timeout KURULU DEĞİL)
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py
```
Env: `LOCAL_MODEL=qwen3:8b`, `LOCAL_REASONING_EFFORT=none` — **CONFIRMED** (provisional değil).
`EXECUTION_CONTRACT_MODE` default `off`. Faz 2'nin approval binding/idempotency'si VE Faz 3'ün
timeout enforcement/postcondition runner'ı bu flag'e bağlı DEĞİL — kill switch'le aynı kategoride,
her zaman açık (bkz. ilgili modüllerin docstring'leri). Yalnız envelope'a postcondition/timeout
bayrağı EKLENMESİ `mode != "off"`'a bağlı (Faz 1'in rollback sözleşmesi).
Koşum artifact'leri: `C:\Temp\jarvis-ab\`. **`ab_analyze.py`'yi PowerShell'den çalıştırın, Git
Bash'ten değil.**

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — 6. oturumun bu başlık altında raporladığı gözlem
  `5941f63`'te yanlış-sunucu hatası olarak açıklandı; gerçek bir kozmetik-label sorunu kaldıysa
  yeniden gözlemlenmesi gerekiyor.
