# CHANGELOG

All notable changes to J.A.R.V.I.S. from Faz 4 onward.
For Iteration 1–3 history, see [log.md](log.md) (frozen 2026-05-24).
For current architecture and feature inventory, see [ProjectState.md](ProjectState.md).

---

## [Merge-öncesi review sertleştirmesi] — 2026-07-19

Dış reviewer'ın A/B raporu kabulü sonrası merge-öncesi iş listesi uygulanıyor.

**Faz 1 — kill switch fail-safe:** VAR-ama-okunamaz/bozuk state dosyası artık **FAIL-CLOSED**
(sentetik trip; cache'lenmez → düzelen dosya anında geçerli) — eski davranış sessizce stale
cache/armed default'a düşüyordu (BOM olayının iki sessiz yönü). Eksik dosya fresh-install
default'u olarak `enabled=True` kalır; dosyayı silmek warm trip'i sessizce re-arm etmez.
Episode başına 1 `logger.critical` + yapısal audit eventi (`kill_switch_state_unreadable`).
`_save(state)` imzası: bozuk dosyada `/killswitch on|off` artık geçerli dosyayı yeniden yazar
(operatör kurtarma yolu). 12 yeni failure-mode testi (truncated/empty/anahtarsız/IO-error/
silme/eşzamanlı okuma-yazma/log-latch/audit). **433 pytest yeşil, ruff temiz.**

**Faz 2 — G17b kişisel-veri uydurma yasağı (reviewer'ın ana tezi):** üç katman. (1)
Deterministik: `context_builder._format_facts` artık `degraded_features()`'ı sorguluyor —
fact extractor degraded iken facts block "(none yet)" yerine açık "MEMORY EXTRACTION
UNAVAILABLE + do NOT guess" markeri taşıyor ("hiç kayıt yok" ile "extractor çalışmadı"
artık ayırt ediliyor; modelin şehir uydurduğu belirsizlik buydu). (2) Kalıcı prompt kuralı:
`05_memory_policy.md` "Memory honesty" — kişisel bilgiler yalnız facts block/tool
çıktısından, yoksa "kayıtlarımda yok", asla tahmin. (3) Oracle sözleşmesi: `Expected`'a
`required_any` (OR-grubu) + `forbidden_response` (KOŞULSUZ yasak — `forbidden_claims`'in
aksine tool başarısından bağımsız) alanları; G17b artık "izmir VEYA dürüst belirsizlik"
ister, İzmir-dışı her şehir adı (hedge'li uydurma dahil) kesin FAIL. 9 yeni test.
**442 pytest yeşil, ruff temiz.**

---

## [GPT 2. tur planı + round-3 ölçüm düzeltmeleri] — 2026-07-18

İki oturum. **Oturum 1** (GPT_Analysis 2. tur, onaylı plan): audit `ok` alanı `.content`
üzerinden + kanonik failure-prefix'ler; `shell_run` workspace-cwd + cd-escape guard'ı;
`plot_data` inline `data_json` (B6 kök nedeni); compose history-echo guard'ı; harness
senaryo izolasyonu (/reset); **eval oracle** (`scripts/eval_oracle.py`, trace+fs+response
birlikte) + `jarvis/tool_trace.py` (L1 dahil her çağrı, `JARVIS_TOOL_TRACE` gated);
router diacritic folding (`grafik ciz`, `Drivea yukle` artık doğru domain'e düşüyor);
qwen3 thinking-off (`LOCAL_REASONING_EFFORT=none`, ~14x hız, tool-call regresyonu yok);
TTFT + cold-start enstrümantasyonu; Calendar/Gmail hata yolları `[ERROR]` standardında.

**Oturum 2** (round-3 review, A/B öncesi ölçüm hataları): **D13b oracle beklentisi
tersti** — `enabled=False` trip DEMEKTİR; çalışan kill switch FAIL, bozuk olan PASS
skorluyordu → BLOCKED'a çevrildi. **Pre-execution bloklar artık yapısal kanıt bırakıyor**:
confirmation_node kill-switch vetosu ve external-write blokunda `policy_decision` trace
satırı yazıyor; oracle'ın response-regex fallback'i kaldırıldı (yalnız "devre dışı" YAZAN
model artık geçemez). **`turn_summary()` compose>agent tercihli** — tool turn'lerinde
latency/TTFT/label artık görünür cevabı yazan çağrıdan (A/B'nin ölçtüğü şey). Ayrıca:
tool_trace args key-redaksiyonu, `plot_data` inline limitleri (256KB/10k satır/100
kolon/düz primitifler), CI ruff'ı harness scriptlerini de linliyor, `.env.example`'a
`LOCAL_REASONING_EFFORT`. **417 pytest yeşil, ruff temiz**; policy-trace→oracle zinciri
izole seam-check ile uçtan uca doğrulandı.

**Oturum 3 — TAM A/B KOŞULDU (16 senaryo × 5 tur × 2 konfig, canlı):** doğruluk birebir
aynı (**60/65 vs 60/65**; tek FAIL iki konfigde de G17b — offline extractor degraded),
thinking-off konuşma turnlerinde **3-6x hızlı** (A1 warm LLM 892ms vs 4819ms), run duvar
süresi ~%25 kısa → **`LOCAL_REASONING_EFFORT=none` default'u DOĞRULANDI.** Canlı koşu iki
gerçek bug daha yakaladı: (1) kill_switch BOM-körü okuma — BOM'lu state dosyası sessizce
stale cache'e düşürüyordu; dıştan TRIP görünmez kalabilirdi → `utf-8-sig`; (2) `fetch_url`
SSRF reddi `[ERROR]` prefix'liydi → `[BLOCKED]` (oracle C9 artık gerçek bloku görüyor).
Harness repoya alındı: `scripts/ab_run_config.ps1` + `ab_launch_server.py` + `ab_analyze.py`
(Faz 4 challenger koşuları aynı altyapıyı kullanacak). **421 pytest yeşil.**

## [Patch 1.2 + Sprint 2 + model A/B: kabiliyet regresyonu] — 2026-07-17

**Bağlam:** Owner "eskiden takvime ekleme gibi işleri yapıyordu, şimdi yapamıyor" dedi. Teşhis
(kodda doğrulandı): kabiliyet kaybı kod çürümesi DEĞİL, motor değişimi — "takvim" çalışırken
non-trivial her turn cloud Gemini'ye gidiyordu; `CLOUD_POLICY=off` (maliyet kararı) sonrası her
şey qwen2.5:7b'ye düştü ve o model ~34 araç + uzun prompt altında tool-call kanalını
kullanamıyor. İkinci dış review (ChatGPT-5.6, `GPT_Analysis.md`) modelin tek suçlu olmadığını
gösterdi: 8 modelden-bağımsız gerçek bug. Bu üç fazlı çalışma o planı uyguladı. **34+38 yeni test
(324/324 pytest yeşil), ruff temiz**, artı canlı kabul turu (aşağıda).

### Patch 1.2 — Tool Runtime Safety (deterministik, LLM'den bağımsız)
- **`imap-tools` bağımlılığı eklendi** (`requirements.txt`/lock) — E15 canlı: temiz kurulumda
  `itu_mail` `No module named 'imap_tools'` ile `/chat`'i 500'lüyordu.
- **SafeToolNode** (`jarvis/graph/safe_tools.py`): tool-body exception'ları artık yapılandırılmış
  `[TOOL_ERROR]` ToolMessage'a dönüyor (kategori + retryable; sanitize edilmiş tek satır,
  traceback/credential sızmaz) — graph tool hatasıyla ölmüyor, model tepki verebiliyor. LangGraph'ın
  default `handle_tool_errors`'ı yalnız `ToolInvocationError`'ı yakalıyordu.
- **Deterministik tool-call sınırları** (`nodes.py` confirmation node başı, policy'den önce,
  router'dan bağımsız): batch≤4, turn≤6, round≤2, identical=1 — aşan batch KOMPLE reddedilir. Canlı
  A2: qwen2.5 tek-satırlık selamlamaya ~20 çağrılık halüsinasyon batch'i (2 mail send) üretmişti;
  tek savunma external-write gate'iydi. Yeni `tool_execution_ledger` + `tool_result_accounting`
  node'u (tools SONRASI çalışan, çağrının nasıl bittiğini bilen tek yer) `seen`/`completed`
  fingerprint ayrımını tutuyor. Audit blok kayıtları `batch_size`/`turn_attempted_count`/
  `unique_tool_count`/`external_write_count` ile zenginleştirildi.
- **`GraphRecursionError` → ledger-bazlı kontrollü cevap** (F16'nın opak 500'ü yerine): en az bir
  başarılı tool varsa "tamamlananlar korundu", yoksa "doğrulanamadı" — asla battaniye iddia.
  `/chat` 200, `/chat/stream` normal SSE; yarım turn history'ye yazılmaz.
- **ProcedureStore idempotency** (F16: 10 duplicate draft): content fingerprint + güvenli migration
  (backfill → approved-öncelikli canonical → `archived_duplicate` → partial UNIQUE index — mevcut
  duplicate'ler yüzünden index doğrudan kurulamaz). `add_or_get()` → `ProcedureAddResult(id, created)`;
  duplicate'ta `[ALREADY_EXISTS]` döner ve Chroma'ya ikinci kez yazmaz.
- **Context hygiene / turn compaction** (A2→A3 kök nedeni): history'ye artık yalnız gerçek
  kullanıcı mesajı + nihai cevap (+ opsiyonel tek satır tool özeti) giriyor; raw batch/stub/
  policy-ack/critic mesajları checkpoint/audit/ledger'da kalıyor. `_trim_history` mesaj-sayısı
  yerine tamamlanmış-turn bazlı (max 10). Eskiden bir halüsinasyon batch'i 20-mesaj penceresini
  taşırıp kullanıcının az önce söylediği bilgiyi ("rengim mavi") atıyordu.

### Sprint 2 — Capability router + graph separation
- **Deterministik capability router** (`jarvis/graph/tool_router.py`): kelime-sınırlı (`\b`) TR+EN
  tablo → `ToolRoute(primary_domain, domains≤3, confidence, explicit_tool_intent)`; conversation=0
  araç, belirsiz istek ASLA full set, toplam subset≤8. `ToolSpec.domain` alanı + 13-domain haritası;
  MCP araçları 'mcp' karantinasında (açık browser/otomasyon ifadesi olmadan hiçbir turn'e açılmaz);
  `procedure_save` yalnız açık "prosedür kaydet" niyetinde. `_is_trivially_simple` + iki substring
  sinyal seti emekli ("ok"∈"çok", "hi"∈"tarihi" bug sınıfı öldü).
- **Turn-scoped binding**: `make_agent_node` artık state[tool_route] → subset → (role,subset)-başına
  `get_llm` cache'i bind ediyor; route yoksa (arka plan/eski checkpoint) full set. Hiçbir yer artık
  ~34 şemayı birden bind etmiyor.
- **Graph ayrımı** (F16'nın döngüsünü yapısal kırar): yeni **bare compose node** (sıfır tool
  şeması) nihai cevabı üretiyor — tanık olduğu çağrıyı yeniden düzenlemesi imkânsız.
  `tools→tool_result_accounting→post_tool_router→(compose|agent)`: tek-adımlı başarı→compose;
  multi-step şekil (planner veya multi-domain route) + round bütçesi→agent. Critic fake
  `HumanMessage` enjeksiyonu kaldırıldı — critique yalnız state'te, compose node-lokal
  SystemMessage ile uygular; revizyon bare compose'dan geçer.

### Faz 3 — Yerel model A/B: qwen2.5:7b → **qwen3:8b** (default değişti)
Aynı 16-senaryo suite, `temperature=0`, aynı scoped subset'ler, `--profile test`:
- **qwen2.5:7b: SIFIR gerçek tool çağrısı** — "dosyayı oluşturdum/maili gönderdim" hepsi
  halüsinasyon metni, tool katmanına hiç ulaşmadı (audit boş, diskte dosya yok). Başarısızlıktan
  beter: güvenlik gate'leri devreye bile girmedi.
- **qwen3:8b: gerçek iyi-biçimli çağrılar** — `file_write` GERÇEKTEN yazdı, `shell_run` dir
  GERÇEKTEN çalıştı → external-write gate, shell deny-list, SSRF guard, killswitch İLK KEZ uçtan
  uca gerçek çağrılarla doğrulandı. 8 GB RTX 4070 Laptop VRAM'e sığıyor.
- Local tier `temperature=0` (deterministik tool-calling + A/B tekrarlanabilirliği).

### Yol boyunca CANLI bulunan 3 gerçek bug (A/B'den bağımsız, kalıcı düzeltme)
1. **langgraph 1.2.x non-streaming `ainvoke()` dinamik interrupt'i RAISE etmiyor**, `result["__interrupt__"]`'te
   döndürüyor → `/chat`'in `except GraphInterrupt`'i hiç tetiklenmiyordu, onay payload'u sessizce
   düşüyordu (Faz 4'ten beri latent — yalnız streaming CLI/voice yolları canlı doğrulanmıştı).
   chat/proactive/background üç giriş noktası da value-surface'i ele alıyor.
2. **Boş-cevap fallback'i tüm mesaj listesini tarıyordu** → replay edilen history'ye uzanıp önceki
   turn'ün cevabını aynen döndürüyordu (canlı "echo"). Artık yalnız bu turn'ün mesajlarına bakıyor.
3. **`graph_stream_to_text` yalnız "agent" node'unu stream ediyordu**; Sprint 2 sonrası final cevap
   "compose"dan geliyor → onaylanan `shell_run` boş stream dönüyordu. Compose da stream ediliyor.

### Kabul turu (16 senaryo, qwen3:8b default, ground-truth: audit + dosya sistemi)
GPT-5.6'nın kabul metrik tablosu: **HTTP 500 = 0** · raw-JSON/pseudo final = 0 · uydurma tool adı =
0 · aynı tool+args tekrarı = 0 (F16 tek draft) · izinsiz dış yan etki = 0 · **A2→A3 short-term
recall = GEÇER** · gerçek tool-call üretimi ≈ %100 (tool gerektiren her testte) · doğru domain
seçimi 15/15 · **maliyet $0.00**. E15/E14 kimlik-yok artık düzgün `[TOOL_ERROR]`/hata mesajı (500
değil); killswitch izole doğrulandı (temiz session, off → `blocked_kill_switch`).

**Bilinen sınır (yeni):** aynı tool-isteği önceki turn'de geçmişte varsa, model tool çağırmadan
önceki turn'ün cevabını yankılayabiliyor (D13b canlıda: killswitch testini geçersiz kıldı —
killswitch'in kendisi izole testte sağlam). Turn compaction'ın özet-satırının yan etkisi;
sonraki iterasyona bırakıldı.

---

## [Stabilizasyon Patch 1.1: dış review düzeltmeleri] — 2026-07-16

Stabilizasyon sprintinin (aşağıda) dış incelemesi (ChatGPT-5.6, sprint commit'i `c7f2b63`
üzerinde) 9 maddelik bir düzeltme listesi çıkardı — 1 P0 + 8 P1. Her iddia önce kodda tek tek
doğrulandı (dokuzu da gerçek), sonra sıralı uygulandı. **24 yeni test (242/242 pytest yeşil),
ruff temiz**, artı canlı smoke (aşağıda).

- **P0 — `--profile test` gerçek `JARVIS_HOME`'u devralabiliyordu**: `__main__.py`'deki
  `os.environ.setdefault(...)`, işletim sisteminde global `JARVIS_HOME` tanımlıysa temp home
  yerine ONU kullanıyordu — "gerçek veriye dokunmaz" garantisi tam da o değişkeni kullanan
  kurulumlarda sessizce bozuluyordu. Artık her zaman taze `mkdtemp`; tekrarlanabilir dizin
  isteyen (CI) için yeni, test-only `JARVIS_TEST_HOME` değişkeni (production değişkeniyle
  karıştırılamaz, açık opt-in). Eski davranışı bilerek koruyan test tersine çevrildi.
- **Reset artık per-session telemetriyi de sıfırlıyor** (`_reset_state_sync`):
  `_last_turn_trace`, `_last_turn_used_pro` ve `_pending_confirmations` lock altında
  temizleniyor — önceden `/status` reset'ten sonra ARŞİVLENMİŞ session'ın modelini göstermeye
  devam ediyordu ve reset-öncesi bir confirmation id yeni session'a resume edilebilirdi.
  Kullanıcının `/model` pin'i (`_active_model_id`) bilerek korunuyor (tercih, session state'i değil).
- **External-write koruması per-action oldu**: `PolicyDecision`'a per-CALL `side_effect_type`
  alanı eklendi (`_READ_ACTIONS`'taki read aksiyonları "external_read" olarak çözülüyor);
  `make_confirmation_node`'un `EXTERNAL_WRITES_ENABLED=false` gate'i statik `ToolSpec` yerine
  bunu kullanıyor. Önceden `gmail read`/`calendar list`/`drive download` da bloklanıyordu —
  test profili tam da egzersiz etmesi gereken read path'lere kördü. `gmail send`/`calendar
  create`/Spotify yine hard-deny.
- **`CLOUD_POLICY=explicit` background extractor'ları da kapatıyor**:
  `cloud_extractors_enabled` `!= "off"` yerine `== "auto"` — `explicit`'in vaadi "cloud yalnız
  kullanıcı AÇIKÇA seçtiğinde"; manuel pin konuşmanın cevap modeline izindir, arka plan
  fact/entity/finance/summary/todo/triage/pdf_vision/deep_research Gemini çağrılarına değil.
  Ana router'ın explicit/pin davranışı değişmedi.
- **AI Studio artık koşulsuz bedava sayılmıyor**: yeni `Settings.ai_studio_billing_mode`
  (`free|paid|unknown`, default `unknown`) — bir Developer-API anahtarının free mi paid mi
  olduğu provider adından bilinemez (bu reponun kendi anahtarı kredisi tükenmiş PAID çıktı).
  `_Tier.billable: bool` → `_Tier.billing: str`; metadata `jarvis_billing` (+ türetilmiş
  `jarvis_billable`); `UsageTracker.record(billing=...)` üç durumlu: `paid` fiyatlanır (yalnız
  `provider=="vertex"` flash/pro_turns sayacını artırır — gcp_quota RPD takibi Vertex'e özel),
  `free` 0$, `unknown` tokenlar `unpriced_tokens_in/out`'ta birikir ve `/status`
  (`session_unpriced_tokens`) + `/budget` raporunda görünür — asla sessizce 0$ varsayılmaz.
- **`fallback_used` response/turn olarak ayrıldı** (`turn_summary`):
  `response_fallback_used` (görünür cevabı fallback tier mi yazdı — etiketi bu sürer) vs
  `turn_had_any_fallback` (turn'ün herhangi bir yerinde hata/tier>0 var mıydı). Critic'in
  fallback'i artık cevabı "(fallback)" diye yanlış etiketlemiyor; `fallback_used` anahtarı
  eski istemciler için response-scoped alias olarak duruyor. CLI `/status` critic-only
  fallback'i soluk "(fallback elsewhere in turn)" notuyla gösteriyor.
- **Confirmation resume artık trace'i güncelliyor**: `_pending_confirmations` bare `config`
  yerine `{"config", "recorder"}` saklıyor; `resume_and_stream()` bittiğinde aynı recorder'dan
  `turn_summary()` alıp `_last_turn_trace`'e yazıyor — önceden onaydan geçen bir turn'ün
  `/status` başlığı hep BİR ÖNCEKİ turn'ü gösteriyordu.
- **Legacy generic-429 graph rebuild kaldırıldı** (`chat()`): router-öncesi kalıntı — herhangi
  bir tool'un (Tavily dahil) "429" içeren hatasında grafı AI Studio'ya rebuild edip TÜM turn'ü
  yeniden çalıştırıyordu (başarılı tool side-effect'lerini tekrarlama riski) ve
  `CLOUD_POLICY=off` altında router'ın reddedeceği bir geçişi duyuruyordu. Per-invocation
  fallback tek yerde: `_compose(...).with_fallbacks()`. Artık ölü olan `_using_fallback` alanı
  ve etiket suffix'i tamamen söküldü.
- **Background task origin-session'a sabitlendi** (`background_turn`): `origin_session_id`
  girişte yakalanıyor; iş bittiğinde aktif session değişmişse (reset/switch) sonuç canlı
  konuşmaya DEĞİL, origin session'ın store'una (kendi taze `turn_idx` bucket'ına) yazılıyor;
  episodic memory de origin'e tag'leniyor. Kullanıcı sonucu TaskExecutor'ın tamamlanma
  bildirimiyle görüyor — session contamination kapandı.
- **Küçükler**: zero-token çağrılar artık `by_provider.calls`'ta sayılıyor (+
  `unreported_calls` işareti) — gerçek bir invocation, provider usage raporlamadı diye
  defterden düşmüyor; `/status`'a `vertex_configured` + `cloud_calls_allowed` alanları eklendi
  (`vertex_active` yanıltıcı adıyla eski istemciler için duruyor); `.env.example`'a
  `AI_STUDIO_BILLING_MODE` + `JARVIS_TEST_HOME` belgelendi.
- **Canlı doğrulama** (`--api --profile test --port 8131` + Invoke-RestMethod): `/status` yeni
  alanlarla doğru (policy=off altında `cloud_calls_allowed:false`); gerçek local turn →
  `actual_provider:"ollama"`, maliyet 0$, `turn_had_any_fallback:false`; içerikli session'da
  `/reset` → HTTP 200 VE sonrasında trace alanları null (patch'in kendi düzeltmesinin canlı
  kanıtı); reset'in summarizer'ı degraded listesine düştü (cloud gate çalışıyor).
- **Kapsam dışı bırakılanlar** (review'un düşük öncelikli notlarından): `local` rolünün
  "kesin lokal" semantiği (fast/local/realtime hâlâ alias — sprint 2'nin routing işi);
  8 direct-Gemini modülünün gateway migration'ı (sprint 3); pre-first-turn model etiketinin
  policy-körlüğü (kozmetik, sprint 2'deki `_is_trivially_simple` işiyle birlikte ele alınmalı).

## [Stabilizasyon sprinti: Runtime Truth + Reset + Test Isolation] — 2026-07-16

Canlı manuel test oturumu (owner + Claude, aynı gün) 7 bug ortaya çıkardı: model etiketi rolden
tahmin ediliyordu (gerçekte cevaplayan sağlayıcıdan değil), lokal Ollama turn'lerine Gemini fiyatı
yazılıyordu, `POST /reset` içerikli session'da her zaman 500 veriyordu, testler gerçek `data/`ya
yazıyordu, AI Studio anahtarı tükenmişti (429), Vertex her non-trivial turn'de gerçek para
harcıyordu. Bulgular `GPT_Analysis.md`ye (ChatGPT-5.6) verildi; doğrulayıp net bir stabilizasyon
planı çıkardı — **önce ölçüm ve runtime, sonra routing/tool-router**. Bu sprint o planı uyguladı,
sıralı 6 fazda, her fazdan sonra tam pytest. Plan dosyası: `.claude/plans/`. **58 yeni test,
218/218 pytest yeşil, ruff temiz.**

- **Faz 1 — JARVIS_HOME izolasyon kökü**: yeni `jarvis/paths.py` (`jarvis_home()`/`data_dir()`/
  `resolve()`/`project_data_dir()`/`resolve_project()`) — `JARVIS_HOME` env değişkeni set değilse
  davranış bugünkünle birebir aynı (cwd-relative); set edilince TÜM runtime store'lar (sessions.db,
  checkpoints, usage.json, audit_log, kill_switch, ChromaDB, vault, uploads, pdf/geo-math/drive
  cache'leri) VE proje-köküne çivili gmail/calendar/email_triage OAuth token'ları (bir `chdir`in
  kaçıracağı yol) o kökün altına taşınır. ~25 call-site rewire edildi (`agent.py`, `api.py`,
  `monitor.py`, `graph/tools.py`, `tools/finance.py`, `tools/drive.py`, `tools/gmail.py`,
  `tools/calendar.py`, `tools/email_triage.py`, `tools/spotify.py`, `tools/geo_math_tool.py`,
  `tools/pdf.py`, `audit_log.py`, `kill_switch.py`, `gcp_quota.py`, `__main__.py`, `memory.py`).
  Yeni `tests/conftest.py` fixture'ı `jarvis_home` (mevcut `isolated_cwd`ile birlikte kullanılabilir).
- **Faz 2 — `POST /reset` 500 düzeltmesi**: kök neden `api.py`'nin `agent.reset()`'i
  `run_in_executor`'a (worker thread, event loop yok) atması, `reset()`'in içeride
  `asyncio.create_task()` çağırması (`_schedule_summarize_one`) — `RuntimeError: no running event
  loop`, her içerikli session'da. `agent.py`'de `reset()` ikiye bölündü: `_reset_state_sync()`
  (saf senkron state mutation) + `async reset_async()` (`asyncio.to_thread` ile state mutation,
  özet planlaması loop'a dönüldükten SONRA). `_schedule_summarize_one`'a `get_running_loop` guard'ı
  eklendi (loop yoksa crash değil, log + atlama). `api.py`'nin iki call-site'ı (`/reset` endpoint,
  shutdown auto-reset) ve `cli.py`'nin `/reset` komutu `reset_async()`'e geçti. Reset'in "arşivle +
  yeni session", **silme değil** olduğu dokümante edildi (ChromaDB kayıtları kalır).
- **Faz 3 — Provider invocation trace (runtime truth)**: `jarvis/providers/__init__.py`'deki her
  tier artık `with_config(metadata={...})` ile kendi kimliğini taşıyor (`jarvis_provider`,
  `jarvis_model`, `jarvis_billable`, `jarvis_tier_index`) — bind_tools SONRASI, with_fallbacks
  ÖNCESİ (sıra önemli, `RunnableBinding`'in `bind_tools`'u rebind etmemesi için). Yeni
  `jarvis/llm_trace.py`: `LlmTraceRecorder(BaseCallbackHandler)` — `on_chat_model_start`/`on_llm_end`
  /`on_llm_error` ile her gerçek LLM çağrısını `LlmCallTrace`'e çeviriyor (`_HudEventCallback`'e
  dokunulmadı, ayrı bir handler olarak eklendi). `agent.py`'nin 4 giriş noktası (`chat`,
  `chat_stream`, `proactive_turn`, `background_turn`) artık recorder'ı config'e ekliyor; `_cloud_model`
  etiketi artık `_last_turn_trace`'ten türüyor (rolden tahmin değil). `/status` + CLI `/status`'a
  `requested_role`/`actual_provider`/`actual_model`/`fallback_used` alanları eklendi. Yol boyunca
  bulunan 2 regresyon (her ikisi de bu fazın kendi edit'i): `background_turn()`'de `use_pro_agent`
  hiç yerel değişken değildi (sadece state dict'e inline yazılıyordu) — recorder satırı `NameError`
  fırlatıyordu, bu da arka plan task'ında sessizce yutulup testin sonsuza dek bekleyen bir Event'e
  takılmasına yol açıyordu (11+ dakikalık gerçek bir CI/local hang, canlı gözlemlendi); ve
  `test_background_turn.py`'nin `_FakeAgent`'ı yeni `self.usage` okumasını karşılamıyordu (aynı hang
  deseni). İkisi de düzeltildi.
- **Faz 4 — Provider-aware usage v2**: `usage.py`'nin `record()`'u artık `(provider, model,
  tokens_in, tokens_out, billable)` alıyor — maliyet **çağıranın deklare ettiği `billable`
  flag'inden** hesaplanıyor, model adında `"pro"` substring'inden değil (bu, lokal Ollama turn'lerinin
  Gemini Flash gibi fiyatlanmasının kök nedeniydi). `flash_turns`/`pro_turns` artık yalnız billable
  Vertex çağrılarını sayıyor (gcp_quota.py'nin Vertex RPD-kota takibi için doğru anlam); yeni
  `by_provider` kırılımı (additive, eski anahtarlar korunmuş — `ws.py`/`gcp_quota.py` kırılmadı).
  `agent.py`'deki `_record_usage_from_result` (her turn'de `result["messages"]`'daki TÜM
  AIMessage'ları yeniden tarayıp çift sayan kod) ve `chat_stream`'in `len//4` tahmini kaldırıldı —
  tek yazar artık recorder'ın `on_llm_end`'i, background/proactive turn'ler de ilk kez gerçek usage
  kaydediyor.
- **Faz 5 — CLOUD_POLICY gating**: yeni `cloud_policy: Literal["off","explicit","auto"] = "off"`
  (`config.py`) — **varsayılan `off`**, owner'ın canlı-test sonrası kararı. `providers/__init__.py`'ye
  `_cloud_allowed()`/`cloud_extractors_enabled()`/`note_degraded()`/`degraded_features()`; `off`'ta
  `fast` de `reasoning` de saf Ollama (pin dahil, istisnasız); `explicit`'te yalnız manuel `/model`
  pin'i geçer (Vertex-pinned yol için `_cloud_tiers`e `pinned` parametresi eklendi — yoksa
  `_pinned_cloud_tiers`'ın Vertex'e delegasyonu kendi `_cloud_allowed` çağrısında ikinci kez
  engelleniyordu). Router'a migrate olmamış 8 direct-Gemini modülüne (`fact_extractor`,
  `entity_extractor`, `finance_extractor`, `session_summarizer`, `todo_analyzer` ×2,
  `tools/email_triage.py`, `tools/pdf_vision.py`, `tools/deep_research.py`) erken-dönüş gate'i
  eklendi — `off`'ta sessiz `[]`/`None` yerine `note_degraded(feature)` + `/status.degraded` listesi.
  Migrasyon değil, yalnız gating (sprint 3'e bırakıldı).
- **Faz 6 — `--profile test` + `EXTERNAL_WRITES_ENABLED`**: `__main__.py`'ye `--profile
  {default,test}` — `test`, `--profile` bayrağını argparse çalışmadan ÖNCE (module-level, argv
  pre-scan ile) tespit edip `load_dotenv()`'i tamamen atlıyor, `JARVIS_SKIP_DOTENV=1` set ediyor
  (`config.py`'nin kendi bağımsız `env_file` okuyucusu da bu flag'i honoring ediyor — iki ayrı .env
  okuyucusunun ikisi de kapatılmadan gerçek `.env` sızıntısı mümkündü), `JARVIS_HOME`'u
  `tempfile.mkdtemp()`'e (unset ise) ve `CLOUD_POLICY=off`/`EXTERNAL_WRITES_ENABLED=false`'u env'e
  basıyor. Yeni `Settings.external_writes_enabled` (`config.py`) + `make_confirmation_node`'a
  (`graph/nodes.py`) kill-switch'le aynı şekilde bir hard-deny bloğu: `side_effect_type ==
  "external_write"` olan her çağrı (gmail/calendar/drive/itu_mail/spotify) interrupt'a hiç
  gitmeden reddediliyor. **Canlı doğrulama**: `python -m jarvis --api --profile test --port 8130` —
  `/health` ok, `/status` (turn öncesi) taze session + `cloud_policy:"off"`, `/chat "merhaba"` →
  `qwen2.5:7b-instruct (Ollama, local)`, sonraki `/status` → `actual_provider:"ollama"`,
  `session_cost_usd:0.0`, `POST /reset` → **HTTP 200** (içerikli session'da — önceki 500'ün tam
  tersi), reset sonrası yeni session_id + `degraded:["session_summarizer"]` (reset'in özetleme
  denemesi CLOUD_POLICY gate'ine takıldı, sessizce Gemini'ye çıkmadı). Gerçek `data/`+`vault/`
  (58 dosya) MD5 hash'i smoke test öncesi/sonrası **birebir aynı**.

**Bilinçli kapsam dışı** (sonraki sprintler): tool-domain router, `_is_trivially_simple()`'ın
cloud-first davranışı (substring false-pozitifleri dahil — `"ok"`→`"oku"`, `"hi"`→`"hiçbir"`),
critic'in revizyon talimatını `HumanMessage` olarak enjekte etmesi, 8 modülün gateway'e tam
migrasyonu, `purge_session`, ses modeli önbelleklerinin JARVIS_HOME'a taşınması (immutable
multi-GB indirmeler — bilinçli hariç), 2 yetim Settings alanı (`drive_cache_dir`,
`geo_math_output_dir` — modül sabitleri hükmediyor, kablolanmadı).

---

## [GPT-5.6 review remediation] — 2026-07-15 — Güvenlik sertleştirmesi (Faz 1-7)

Önceki oturumda ChatGPT 5.6'nın JARVIS reposuna yaptığı dış denetim raporunun 23 iddiası gerçek kod
üzerinde tek tek doğrulanmış (17 CONFIRMED, doğrulama tablosu `.claude/plans/` altında kalıcı kayıt)
ve 7 fazlık bir remediation planı çıkarılmıştı. Bu oturum o planı uyguladı — P0 güvenlik fazları
(1-3) önce, sonra sertleştirme (4-5), en sonda kapsamı daraltılmış iyileştirmeler (6-7). Ağır
çok-ajanlı workflow kullanılmadı (önceki oturum session limitine takılmıştı); iş inline, faz faz,
her fazda testlerle ilerledi. **56 yeni test, 160/160 pytest yeşil.**

- **Faz 1 — API secure-by-default (P0)**: boş `JARVIS_API_KEY` + `0.0.0.0` + CORS `"*"` üçlüsü canlı
  bir açıktı (telefon/Tailscale erişimi zaten kullanımda). `jarvis/api.py`'ye `resolve_api_bind_host()`
  eklendi: key boşsa efektif host `127.0.0.1`'e düşer; `JARVIS_API_HOST` açıkça non-loopback set
  edilip key boşsa **fail-fast** (`RuntimeError`, net mesajla). CORS `"*"` → `resolve_cors_origins()`
  ile explicit allowlist (Electron'un `file://` origin'i + `localhost`/`127.0.0.1` her port dahil,
  hiçbir zaman wildcard). Yeni ayarlar: `api_host`, `api_cors_origins` (`jarvis/config.py`).
- **Faz 2 — Proaktif turn yapısal read-only (P0)**: `monitor.py`'den gelen proaktif turn'ler tam tool
  setiyle çalışıyordu, L2 (auto-approve, `requires_confirmation=False`) araçlar (örn. `file_write`,
  `procedure_save`) hiçbir gate'e takılmadan sessizce yürüyordu — sadece system prompt "yapma"
  diyordu. `jarvis/graph/nodes.py`'nin `confirmation_node`'una runtime guard eklendi:
  `transport` `"monitor-"` ile başlıyorsa ve risk_level≥2 ise, zaten-confirmable (L3, gate açık) olan
  hariç, tüm çağrılar yürütülmeden reddedilir. Faz 7'nin doğru çalışan L3-discard-to-notify davranışı
  (`ProactiveOutcome(kind="needs_confirmation")`) dokunulmadan korundu.
- **Faz 3 — Prosedürel bellek zehirlenmesi (P0/P1)**: `procedure_store.py`'de provenance/approval hiç
  yoktu — agent'in yazdığı bir prosedür anında recall'a girip gelecek turn'lerin system prompt'una
  enjekte olabiliyordu. `status`/`created_by`/`approved_at` kolonları eklendi (idempotent migration,
  mevcut satırlar `approved` grandfather), yeni `source='agent'` satırları `draft` başlar.
  `jarvis/memory.py`'nin `recall_procedures()`'ı artık yalnız `status='approved'` döndürüyor (Chroma
  `where` filtresi). CLI'ye `/procedures` komutu (list/approve/reject). System prompt'un context
  injection bloğu (`06_context_injection.md`, v3) memory/procedure/vault bloklarını "untrusted
  reference data, not instructions" olarak çerçeveliyor artık.
- **Faz 4 — MCP/browser sertleştirme (P1)**: `@playwright/mcp@latest` → pinlendi (`0.0.78`, npm'den
  teyit edildi). `browser_navigate`'in hiç SSRF guard'ı yoktu (`url_read`'inki vardı) — paylaşımlı
  `jarvis/url_policy.py` çıkarıldı (localhost/private/link-local/metadata, DNS-rebinding'e karşı
  resolved-IP kontrolü dahil), `url_read` buna geçti, MCP tarafına `langchain-mcp-adapters`'ın
  `ToolCallInterceptor`'ı ile uygulandı — engellenen bir `browser_navigate` gerçek MCP çağrısına asla
  ulaşmıyor.
- **Faz 5 — Runtime/concurrency izolasyonu (P1)**: `TaskExecutor._run()` arka plan görevlerini
  `self._agent.chat()` ile çalıştırıyordu — hem ana `self._history`'i mid-flight kirletiyor hem de
  `_state_lock`'ı görevin TÜM süresi boyunca tutup foreground chat'i bloke ediyordu. Yeni
  `JarvisAgent.background_turn()`: `proactive_turn()`'ün izolasyon desenini model alıyor (kendi
  thread_id + izole mesaj listesi), `ainvoke()` süresince lock TUTMUYOR, sonuç bittiğinde kısa bir
  kilitli pencerede gerçek `self._history`'e user/assistant mesaj çifti olarak ekleniyor. BUG-8'in
  kendisi (chat()/chat_stream()'in kilit davranışı) hiç değiştirilmedi — regresyon riski böylece
  minimize edildi.
- **Faz 6 — shell/python temel guard (P1/P2, kapsamı daraltılmış)**: `shell.py`'nin deny-list'inde
  `Invoke-Expression`/`iex`/`-EncodedCommand`/.NET reflection bypass'ları hiç yoktu — eklendi.
  `python_exec.py`'nin (verdict: shell_run'dan bile kötü) hiç içerik kontrolü yoktu — artık
  çalıştırmadan önce script kaynağını aynı paylaşımlı deny-list'e karşı tarıyor. **Tam sandbox
  değil** (docs/SAFETY.md'nin "Known limits"i geçerli). Tool-domain router (turn başına 5-8 tool) ve
  tam shell/python broker (Job Objects) plan metninde açıkça ayrı/daha büyük iş olarak ertelenmişti —
  bu oturumda uygulanmadı.
- **Faz 7 — Observability/CI**: startup summary backfill'i (`_schedule_summary_backfill`) yalnızca
  `__init__`'ten çağrılıyordu — bu, her iki gerçek entry point'in de loop'u başlamadan önce çalıştığı
  an, yani hep no-op. Yeni `run_startup_backfill()`, `cli.py`'nin `_run_loop()`/`_run_voice_loop()`'u
  ve `api.py`'nin `lifespan()`'ı loop gerçekten ayaktayken çağırıyor. tok/s telemetrisi (`agent.py`)
  ham çıktı token sayısını hız sanıyordu — artık `on_llm_start`/`on_llm_end` arası gerçek
  `time.monotonic()` farkına bölünüyor. `.github/workflows/ci.yml` eklendi (ruff + pytest,
  `windows-latest` — `pywin32`/`winotify` Windows-only olduğu için; Flutter/Electron ayrı
  `continue-on-error` job). `requirements-lock.txt` eklendi (çalışan `.venv`'den `pip freeze` — tam
  bir `uv lock` resolution'ı bu bağımlılık ağacının ağırlığı/platform-özgüllüğü nedeniyle riskli
  görüldü). CI'yi yeşil başlatmak için `jarvis/`/`tests/` genelinde 38 kullanılmayan import/f-string
  temizlendi (ruff `--fix`, tamamı mekanik, davranış değişikliği yok — tüm testler yeşil kaldı).
  README'nin artık var olmayan `jarvis/legacy/` referansı silindi; CLAUDE.md'nin kendi stale OneDrive
  notu güncellendi (README/CONTRIBUTING zaten düzeltilmişti).
- **56 yeni test**: `test_api_security.py`, `test_confirmation_node.py`, `test_procedure_store.py`,
  `test_mcp_hardening.py`, `test_background_turn.py`, `test_shell_python_guard.py`,
  `test_hud_callback_tokrate.py`, `test_startup_backfill.py`. Tam suite: **160/160 yeşil** (3 test
  bazen tam suite altında flaky çıkıyor — timing-hassas background-task retry pencereleri, izole
  çalıştırıldığında hep geçiyor; bu oturumdan önce de var olan bir durum, kapsam dışı bırakıldı).

---

## [Faz 8 devam] — 2026-07-15 — GitHub'a taşıma, kalan Faz 0 bug'ları, Flutter doğrulama

- **Repo GitHub'a taşındı**: `langgraph-migration` → `main` fast-forward merge (main 2026-05-09'dan
  beri donmuştu, 67 commit geride kalmıştı, hiçbiri kayıp değildi) + yeni public repo'ya push
  (`github.com/mertkaanakgunlu-debug/JARVIS`). Push öncesi tüm git geçmişi secret taraması yapıldı
  (`.env`/`credentials.json`/`token.json`/`.pem`/`.key` hiç commit edilmemiş; API-key/private-key
  deseni için içerik taraması temiz).
- **Flutter SDK kuruldu** (`C:\flutter`, git clone — winget'te resmi paket yok) ve `mobile/`'da
  `flutter analyze` çalıştırıldı: `ws_client.dart` sıfır hata/uyarıyla derleniyor (Faz 8'in
  BUG-mob-tls/BUG-reconnect/task_a9cee697 Dart değişiklikleri artık gerçekten derlenmiş olarak
  doğrulandı). 69 önceden var olan, bu oturumla ilgisiz `info`-seviye deprecation notu bulundu,
  dokunulmadı. Android SDK yok, `flutter build apk` denenmedi (ayrı, çok daha büyük bir kurulum).
- **`WsClient.reconnect()` artık kendi host/apiKey parametrelerini gerçekten uyguluyor**
  (task_a9cee697) — `_host`/`_apiKey` `final` olduğu için önceden hiç atanmıyordu.
- **4 Faz-0 bug'u düzeltildi** (ROADMAP.md'de "opportunistic, deferred" olarak bırakılmıştı):
  BUG-15 (finance sync regex — `gmail.py`'nin gerçek `"• [id]"` formatına göre düzeltildi, subject/
  body ayrıştırma da aynı kök nedenden dolayı düzeltildi), BUG-16 (todo bg analiz —
  `asyncio.create_task()`'ın sonucu hiç referans tutulmuyordu, GC'lenebiliyordu; modül seviyeli
  strong-ref set eklendi), BUG-17 (gcp_quota cache — tazelik kontrolü hiç yazılmayan bir anahtarı
  arıyordu), BUG-18 (gcp_quota forecast — `last_updated` her kayıtta yenilendiği için günlük oran
  her zaman TÜM zamanların toplamına eşitleniyordu; yeni `first_seen` alanına anchor edildi).
- **`_OllamaEF`/`_GeminiEF.embed_query()` eksikti** (`jarvis/memory.py`) — canlı repro ile bulundu:
  bu projedeki chromadb sürümü `.query()` için koşulsuz `embed_query()` çağırıyor (hasattr fallback
  yok), `.add()` içinse `__call__` — her iki EF de sadece `__call__` içeriyordu, yani her semantik
  recall `AttributeError` fırlatıyordu. İkisine de `__call__`'a delege eden `embed_query()` eklendi.
- **Bonus bulgu, `_GeminiEF`'i canlı Gemini API'sine karşı doğrularken bulundu**: `_build_gemini_ef`'in
  hardcoded model id'si (`"models/text-embedding-004"`) Google tarafında emekliye ayrılmış (her
  çağrı 404 veriyordu). Gerçek `client.models.list()` çağrısı güncel embedding modellerini gösterdi
  (`gemini-embedding-001`/`-2`/`-2-preview`); `gemini-embedding-2`'ye geçildi. Ayrıca construction
  anında bir smoke-test embed çağrısı eklendi (Ollama'nın reachability probe'una benzer) — gelecekte
  Google bir modeli tekrar emekliye ayırırsa bu katman artık her gerçek recall'da çökmek yerine
  hızlıca default ONNX EF'e düşecek.
- **21 scratch worktree branch'inden 17'si silindi** — her biri `git merge-base --is-ancestor` ile
  `langgraph-migration`'a tamamen dahil olduğu doğrulandıktan sonra. 4 tanesi (mainline'da olmayan
  commit'ler içerdiği için) silinmedi — ikisi muhtemelen artık gereksiz, ikisi (eval regression
  suite, rolling/hierarchical summarization) mevcut kodda karşılığı görülmediği için ayrıca
  incelenmeyi bekliyor.
- **12 yeni regresyon testi**, toplam **104/104 test geçiyor**.

---

## [Faz 8] — 2026-07-15 — Temizlik & konsolidasyon (non-destructive scope)

- **`jarvis/legacy/` retired** — the old pydantic-ai orchestrator (unimported by any live path,
  confirmed via grep) and the dead `jarvis/prompts/system.md` pointer file are deleted outright,
  not archived. Recoverable from git history before this commit if ever needed for reference.
- **New minimal test suite** — `tests/` (pytest + pytest-asyncio), 92 tests across 10 files. Covers
  the safety kernel (`policy_guard`'s risk classification, per-action downgrade, kill-switch veto
  scoping), `session_store` concurrency/atomicity, and — the concrete "offline-failover" proof —
  `jarvis/providers/get_llm()`: with no cloud credentials configured at all, both the `fast` and
  `reasoning` roles resolve to bare local Ollama, never a fallback wrapper around nothing. One
  regression test per bug fixed below. New `tests/conftest.py`'s `isolated_cwd` fixture enforces
  MEMORY.md's isolate-test-data-paths lesson for every test that touches cwd-relative storage.
- **9 P2 bugs fixed**: `file_write` ValueError on home paths (BUG-20); calendar dedup hardcoded
  `+03:00` instead of the configured timezone, now DST-correct via `zoneinfo` (BUG-21); IMAP
  connection leak on login failure (BUG-itu); pdf cache keyed on path+mtime instead of content
  hash, so a restored/extracted file with an older mtime could serve stale cached text forever
  (BUG-pdf); Devito-failure fallback hardcoded `duration=0.5` instead of the caller's requested
  duration (BUG-geomath); unanchored `"pro" in text` substring match hijacked ordinary messages
  containing "proje"/"problem"/"program"/etc. into a silent model switch that dropped the user's
  real message (BUG-modelswitch); an empty LLM response was unconditionally accepted as a
  successful turn instead of retried (BUG-emptyresp); `/chat/upload` had no size cap and never
  cleaned up saved files (BUG-upload); `UsageTracker` silently clobbered another live process's
  recorded spend on every save (BUG-usage).
- **Bonus fix, same root cause as BUG-usage, found live**: `kill_switch.py` cached its first
  successful read for the rest of the process's life — a trip from one process (e.g. the CLI's
  `/killswitch`) was invisible to an already-running `--api --monitor` server until restart,
  silently defeating the "hard stop, no prompt" guarantee. Now always re-reads from disk.
- **Electron/mobile client hygiene**: neither Electron REST call (`/chat/upload`, `/chat/stream`)
  sent the `X-API-Key` header — both would 401 once `JARVIS_API_KEY` is configured (BUG-elec).
  Mobile's `/ws` token moved from a `?token=` query param (visible to anything that logs URLs) to
  an `X-API-Key` header via `IOWebSocketChannel`; the server checks the header first, falling back
  to the query param only for Electron, whose browser `WebSocket` API can't set custom headers
  (BUG-mob-tls — closes URL-logging exposure, not wire-level cleartext; this server still has no
  TLS termination). WS reconnect now backs off exponentially (3s → 60s cap) instead of retrying
  every 3s forever (BUG-reconnect).
- **Explicitly out of scope this session** (owner go-ahead required, not assumed): merging
  `langgraph-migration` → `main`; cleaning up the 21 stray `.claude/worktrees/*` scratch branches.

## [Faz 7] — 2026-07-15 — Proaktiflik (software half)

- **New `JarvisAgent.proactive_turn()`** — gives `jarvis/monitor.py` a real path into the
  tool-calling graph (previously toast/FCM notifications only). Runs the exact same compiled graph
  `chat()` does (same tools, same `policy_guard`/kill-switch/audit_log gate — zero changes to any of
  them), on an isolated message list + dedicated LangGraph thread_id that never touches
  `self._history`/`_turn`/`session_store.save_turn` or episodic memory, so JARVIS's background
  self-talk never leaks into the user's real conversation. Serialized via the Faz 0 `_state_lock`
  like every other entry point.
- **Calendar/email self-initiation**: `monitor.py`'s `_check_email()`/`_check_calendar()` now also
  call `_maybe_proactive()` alongside their existing unconditional toast — off by default
  (`MONITOR_PROACTIVE_ENABLED=False`), throttled across all sources combined
  (`MONITOR_PROACTIVE_MIN_GAP_SEC`, default 600s) so a burst of unread emails can't queue many LLM
  calls at once.
- **Confirm-or-notify, not silent execution**: `proactive_turn()` never raises
  `ConfirmationRequired` — no interactive channel exists for a background thread to answer it (same
  constraint `TaskExecutor` already has). A graph interrupt for an L3 action is discarded (never
  resumed, never executed) and reported back so `monitor.py` can notify instead of leaving a
  confirmation pending behind a round-trip nothing consumes yet.
- **`--monitor` now actually works under `--api`** — previously silently ignored (only `cli.py`'s
  branch ever started a `JarvisMonitor`). `api.py`'s `lifespan()` starts one when
  `run_server(..., monitor=True)`; `__main__.py` threads `args.monitor` through.
- **[BUG-19] fixed** — budget/GCP quota alerts had no dedup and re-fired every poll cycle for as
  long as the condition stayed over-threshold. `gcp_quota.quota_alert_check()` now returns
  `(alert_key, message)` pairs; GCP alerts dedup per day, budget alerts dedup per
  `(year, month, category)` — each self-clears on its own natural period rather than needing manual
  reset logic.
- **Live finding, honestly documented, not fully closed**: a real proactive turn against local
  `qwen2.5:7b-instruct` hallucinated an unrelated `procedure_save` call (L2, no-confirm by existing
  design) for a mundane calendar trigger. `_proactive_system_prompt()` now explicitly forbids any
  creating/saving/sending/modifying tool call during a proactive check — investigation stays
  read-only, a suggested action goes in the reply text instead. A prompt-level mitigation on a
  non-deterministic model, not a structural guarantee like the L3 gate — see `docs/SAFETY.md`'s
  "What Faz 7 changed" for the honest residual-risk writeup.
- **Sensor/MQTT proactivity stays deferred** — still blocked on Faz 6 hardware (no Zigbee
  coordinator dongle, no Home Assistant instance).

## [Faz 5] — 2026-07-15 — MCP client layer

- **New `jarvis/mcp_integration.py`** (`McpToolManager`, built on the official
  `langchain-mcp-adapters`) — connects to configured external MCP servers and merges their tools
  into the graph as a second, dynamically-discovered tool source alongside the 36 native `@tool`
  wrappers (dual layer, per the roadmap — the native tools are untouched). Every discovered tool
  gets a `ToolSpec` synthesized at connect time (`tool_registry.register_dynamic_spec()`) inserted
  into the same `TOOL_SPECS` dict the native tools live in, so `policy_guard`, `audit_log`, and the
  async scheduler cover MCP tools identically with zero changes to any of them.
- **Ships with one real server, disabled by default:** Microsoft's official Playwright MCP — real
  browser automation (navigate/click/type/snapshot/screenshot/evaluate JS/…). Flip
  `MCP_PLAYWRIGHT_ENABLED=True` in `.env`. `Settings.mcp_servers` is a generic JSON escape hatch for
  any other server (e.g. a future ha-mcp, Faz 6) — purely additive config, no code changes needed.
- **Fail-closed classification:** a short explicit allow-list of pure-inspection/navigation
  Playwright tool names gets L1/L2 no-confirm; every other tool — including any name never seen
  before — defaults to L3 + `requires_confirmation=True`, the same gate as `shell_run`/`gmail send`.
  Direct mitigation for the prompt-injection risk a browser tool uniquely adds beyond
  `web_search`/`url_read`: a poisoned page can make the model *want* to click/submit something, but
  can't act without the user approving that specific call.
- **Persistent-session architecture** — MCP's stdio transport needs one subprocess alive for a
  session's life for a stateful server like browser automation (confirmed live: the adapter's
  default stateless `client.get_tools()` spawns a fresh process, and fresh blank browser, per tool
  call, silently breaking `navigate` → `click`). `McpToolManager` uses the persistent
  `client.session()` pattern instead, held open by an `AsyncExitStack`. That session is loop-bound
  (same class of constraint as the LangGraph checkpointer), so `JarvisAgent.connect_mcp_tools()` is
  called explicitly, once, from each entry point's real long-lived loop (`cli.py`'s
  `_run_loop`/`_run_voice_loop`, `api.py`'s `lifespan()`) before any turn or `TaskExecutor`
  background job can run — never lazily from whatever caller happens to `chat()` first.
  `build_graph()` gained an `extra_tools` param for this.
- **Windows fix (confirmed live):** `npx` is `npx.cmd`, a batch shim — spawning it directly via
  Python's subprocess APIs raises `WinError 2`. Every npx-based server config goes through
  `cmd /c npx ...`.
- **Bonus fix bundled in:** `policy_guard.describe_call()`'s detail extraction (used in confirmation
  prompts / TTS / audit log) didn't recognize any of Playwright's argument names, so a pending
  `browser_click` confirmation showed just the bare tool name with no indication of what would be
  clicked — added `element`/`url`/`text` to `_DETAIL_KEYS`.

## [Faz 4] — 2026-07-14 — Security kernel + async tools

- **New `jarvis/policy_guard.py`** — transport-agnostic safety kernel (no LangGraph/LangChain
  imports). Single choke point for "is this tool call allowed, does it need the user's OK" —
  `jarvis/graph/nodes.py`'s `confirmation_node` calls it instead of inlining risk checks, so any
  future direct tool dispatcher (MCP, Faz 5) can reuse the same logic rather than reimplementing it.
  **Per-action, not per-tool (BUG-6):** the four mixed-risk `external_api` tools
  (`google_calendar`/`gmail`/`google_drive`/`itu_mail`) have their documented read actions
  (list/search/read/download) downgraded back to L1/no-confirm — only genuinely risky actions
  (send/create/delete/upload/share/...) interrupt.
- **Confirmation gate now defaults on** (`confirmation_gate_enabled=True`, was `False`) and is
  wired into every interface, not just `/chat/confirm` (BUG-3/4): the CLI text REPL catches
  `ConfirmationRequired` and prompts y/n + optional reason; all three voice loops (CLI `--voice`,
  the API wakeword/PTT loop, and the `/ws` remote-audio session) detect `chat_stream()`'s
  `__jarvis_confirm__` JSON marker via new shared helpers in `jarvis/voice/session.py` instead of
  speaking it verbatim, speak a natural question instead, and treat the next utterance as the
  yes/no answer (anything not recognized as affirmative denies, fail-safe). `POST /chat` now
  catches `ConfirmationRequired` before the generic exception handler and returns a structured
  `{"confirmation_required": true, ...}` response instead of an opaque 500
  (BUG-confirm-payload). The system prompt (`prompts/core/02_tool_policy.md`) no longer tells the
  model it never needs to ask (BUG-5) — it now describes the real approve/deny round-trip.
- **Kill switch** — new `jarvis/kill_switch.py`, persisted to `data/kill_switch.json` so a trip
  survives a restart. Scoped to L3 (external-effect) actions; vetoes inside `confirmation_node`
  before the gate would otherwise prompt, skipping the prompt entirely since asking is pointless
  once the operator already said stop. `/killswitch [status|on|off <reason>]` in the CLI.
- **Append-only audit log** — new `jarvis/audit_log.py`, `data/audit_log.jsonl`. Two event kinds
  for every risk_level ≥ 2 tool call: `decision` (policy_guard's ruling, from `confirmation_node`)
  and `execution_start`/`execution_end` (the call actually ran + outcome, from `agent.py`'s
  `_HudEventCallback` — the same LangChain callback attached for every transport).
- **`python_run` reclassified L2→L3 + confirm-required (BUG-1)** — was more powerful than
  `shell_run` (arbitrary unsandboxed Python from any absolute path) while sitting at a lower gate.
  This is the access-control fix; true sandboxing of the subprocess itself is a deferred, not
  claimed, hardening item.
- **SSRF guard for `webfetch.py` (BUG-6-ssrf)** — `url_read`/`deep_web_research` now refuse
  localhost/private/link-local/reserved ranges and cloud metadata endpoints, checked against the
  *resolved* IP so a DNS-rebinding domain can't bypass a hostname-string check.
- **Auth on `/system/wake` (BUG-2)** — new shared `jarvis/api_auth.py` so `api_routers/system.py`
  can require `X-API-Key` without importing `api.py` (avoids a circular import); `/system/ping`
  stays auth-free by design.
- **Recursion cap + agent-node timeout (BUG-recursion, BUG-14)** — new `Settings.
  graph_recursion_limit` (30) passed as LangGraph's `recursion_limit`; `agent_node`'s LLM call
  wrapped in `asyncio.wait_for(timeout=Settings.agent_llm_timeout_sec)` (90s) so a wedged provider
  surfaces a clear error instead of hanging the turn (and, in voice mode, the mic) forever.
- **Async scheduler** — `task_executor.py`'s `ASYNC_KEYWORDS` now genuinely derives from
  `TOOL_SPECS[...].supports_background` instead of only a hand-maintained phrase list (superset of
  the old behavior, no regression). The five sub-agent tool bridges (`math_solve`, `write_content`,
  `research`, `generate_code`, `geo_math`'s analyze branch) plus `todo` converted from sync
  `@tool def` + the `_run_coro()` thread-and-fresh-event-loop bridge to native `async def` `@tool`s
  — `_run_coro()` was dead code afterward and is deleted. Voice loops (API-mode only — the
  standalone CLI `--voice` has no `TaskExecutor`) now hand a flagged query to `TaskExecutor` with a
  spoken acknowledgement instead of blocking the turn in silence for up to minutes; completion adds
  a Windows toast alongside the pre-existing FCM push.
- **Bonus fix found live:** `TaskExecutor._run()`'s background `agent.chat()` call could raise
  `ConfirmationRequired` (it's an `Exception` subclass) with no channel to answer it — previously
  surfaced as a cryptic `"confirmation_required:<uuid>"` failure. Now caught specifically and
  reworded to name the blocked action and point the user at an interactive retry.
- **Explicitly deferred, not this phase:** no Electron/mobile UI renders a confirmation prompt from
  the API's structured response yet (this dev machine still has no Node.js/npm on PATH — same
  constraint as Faz 3's Electron work); `python_run` is gated, not sandboxed; voice confirmation's
  per-call description stays in English technical form even in a Turkish session.

## [Faz 3] — 2026-07-14 — Real-time local voice + remote `/ws` audio transport

- **Local voice pipeline rebuilt on `jarvis/voice/`** (new package, replaces the flat
  `jarvis/voice.py`): Silero-VAD (raw `.onnx` via `onnxruntime` — not the `silero-vad` pip package,
  which hard-requires torch; pinned to **v5.1.2**, not the newer v6.2.1, after live testing showed
  v6.2.1's exported graph doesn't produce a usable speech-probability signal with the standard
  streaming calling convention despite an identical I/O shape) replaces energy/RMS-threshold VAD
  for end-of-turn detection. **Piper** becomes the primary local TTS engine (Turkish
  `tr_TR-dfki-medium`, English `en_US-lessac-medium`) — chosen over Kokoro-82M specifically because
  Kokoro doesn't support Turkish at all; `edge-tts` stays wired as a per-language fallback tier
  (mirrors the Faz 1 LLM router's local-first-not-cloud-forbidden pattern), not deleted.
- **Full-duplex, not per-turn-blocking:** `DuplexAudioIO` opens one continuously-open callback-mode
  `sounddevice` `InputStream` (previously: a fresh blocking stream per utterance) and a per-turn
  `OutputStream` fed from a thread-safe playback buffer — the mic stays open during playback, which
  is what makes barge-in possible.
- **Barge-in:** sustained, high-confidence speech (deliberately higher threshold + longer duration
  than normal turn-taking, to reduce false triggers from the assistant's own voice bleeding from
  speakers back into the mic — no true acoustic echo cancellation exists in this design) during
  playback aborts audio immediately and cancels the in-flight `agent.chat_stream()` task. Fixed
  **BUG-13** as part of this: `chat_stream()`/`resume_and_stream()` only caught `GraphInterrupt`,
  so a barge-in cancellation (`asyncio.CancelledError`) skipped all turn bookkeeping and left the
  HUD stuck on "thinking"/"speaking" — now propagates correctly while still guaranteeing
  `event_bus.state("idle")` fires.
- **Fixed BUG-12:** the critic's up-to-one-revision loop tags both the draft and the revision
  `langgraph_node="agent"`, so the streamed/voiced text was a run-on concatenation of both with no
  separator — `graph_stream_to_text()` now tracks `metadata["langgraph_step"]` and inserts a
  paragraph break at the pass boundary. `resume_and_stream()`'s independent copy-pasted duplicate
  of the same buggy filter now calls the shared helper instead.
- **Fixed BUG-23:** `openwakeword`'s prediction/mel-spectrogram buffers are now reset
  (`Model.reset()`) at the start of each listening session instead of never.
- **Fixed a wiring gap:** `python -m jarvis --api --voice` (no `--wakeword`) previously started the
  API with zero voice — `--api` never read `args.voice`. `run_server()` now gates on `voice or
  wakeword`.
- **Remote binary audio transport over the existing `/ws` connection** (see new
  `docs/VOICE_PROTOCOL.md` for the full wire spec): a client can act as the mic/speaker for a
  conversation using the server's Whisper/Piper instead of on-device engines. `RemoteWsAudioIO`
  satisfies the same transport-agnostic `AudioIO` protocol as the local `DuplexAudioIO`, so
  `RealtimeVoiceEngine`'s VAD/STT/TTS/barge-in logic is identical either way — no duplication.
  Session arbitration (`jarvis/voice/session_manager.py`) is first-claim-wins between the local
  wakeword/PTT loop and at most one remote client; starting a remote session auto-pauses the local
  loop's next claim (Electron's main process always spawns the backend with `--wakeword`).
  `VoiceModels`/`get_shared_voice_models()` load Whisper/VAD/Piper once and share them across the
  local engine and every remote session in the same process, instead of reloading per-session.
- **`jarvis/ws.py` hardened:** each connection now gets its own outgoing queue + writer task, so
  JSON telemetry broadcasts and binary audio chunks can never race on the same socket — previously
  `broadcast()` was the only thing that ever wrote to a client. Also fixed a latent bug: the `/ws`
  receive loop looped `websocket.receive_text()` forever just to detect disconnects — a binary
  frame would have raised `KeyError` and silently dropped that client, which would have surfaced
  the moment any client sent one. Now branches on `websocket.receive()`'s message type.
- **Security fix pulled forward from the Faz 8 backlog (`BUG-elec`):** the Electron HUD's `/ws`
  connection never sent `?token=`, which mattered little for read-only telemetry but matters a lot
  more once the socket can carry live mic audio and synthesized speech. Electron's main process now
  reads `JARVIS_API_KEY` from the same `.env` file the Python backend reads and passes it to the
  renderer; the server also refuses `audio_session_start` outright when no API key is configured at
  all (remote audio is opt-in-by-configuration).
- **Electron HUD:** `useRemoteAudioSession` (new hook) + `pcm-capture-worklet.js` (new
  `AudioWorkletProcessor`) let the HUD itself become the mic/speaker via `getUserMedia` + Web Audio
  — no new npm dependencies (standard Web Platform APIs). The spacebar handler changed from a
  fire-and-forget PTT POST to a start/stop toggle for this new session type (the old
  `/voice/ptt/start` endpoint is untouched — still serves the local wakeword/PTT path, a parallel
  trigger). The HUD's mic-level meter now shows real telemetry (a new `mic_level` WS event) instead
  of 100% simulated data whenever a voice session is active.
- **Explicitly deferred:** mobile (Flutter) gets no client-side audio-capture/playback code this
  phase — new Dart dependencies, Android runtime mic-permission UX, and real cellular/Tailscale
  jitter are a materially separate effort from the LAN-only Electron implementation. The protocol
  is written down (`docs/VOICE_PROTOCOL.md`) specifically so that fast-follow doesn't require
  reverse-engineering it later. (Also noted, unrelated, not fixed: the Android app already has an
  always-on wake-word service + native overlay + Flutter MethodChannel bridge for a "wake word →
  on-device STT" flow, but the whole chain is disconnected — nothing calls
  `startWakeWordService()`, and a SharedPreferences key mismatch breaks even the boot-autostart
  fallback.)

---

## [Faz 2] — 2026-07-14 — 5-layer cognitive memory

- **Semantic memory:** new `jarvis/fact_extractor.py` (mirrors `entity_extractor.py`) runs a
  Flash-Lite structured-output call after each turn to extract durable facts (stable
  preferences, relationships, recurring constraints — not one-off task detail). New SQLite
  `facts` table (`jarvis/facts_store.py`) + new `jarvis_facts` ChromaDB collection (local-first
  EF chain). Dedup is inline at insert time via embedding-similarity lookup
  (`Memory.find_similar_fact`) — a close match bumps the existing fact instead of inserting a
  duplicate.
- **Fixed global-vs-session recall scoping:** `Memory.recall()` (episodic, `jarvis_memory`) now
  takes an optional `session_id` filter; `ContextBuilder.build()` passes the current session
  through, so a session's raw turns no longer leak into another session's context. Facts and
  session summaries remain deliberately cross-session — that's the point of those layers.
- **Procedural memory:** the hardcoded `_DATA_REPORT_KEYWORDS` keyword match in
  `prompt_loader.py` is gone, replaced by semantic retrieval against a new SQLite `procedures`
  table (`jarvis/procedure_store.py`) + `jarvis_procedures` ChromaDB collection. The pre-existing
  `prompts/workflows/data_report.md` auto-seeds as the first row on startup — zero regression.
  New tool `procedure_save` (#36) lets the agent explicitly persist a new reusable workflow.
- **Meta memory:** `jarvis/tools/files.py`'s `write()` now refuses any path under
  `jarvis/prompts/core/` (`PermissionError`) — persona/safety directives are now provably never
  agent-writable, not just agent-writable-but-not-instructed-to. New
  `jarvis/prompts/CORE_VERSIONS.md` tracks a human-bumped version/updated stamp per core prompt
  file; new `/meta` and `/facts` CLI commands.
- **Fix (BUG-25):** entity extraction (now entity + fact extraction together,
  `_schedule_memory_extraction`) no longer fires on trivially short exchanges (a bare
  "ok"/"tamam" ack) — guarded by `JarvisAgent._should_extract`, verified to still fire for
  `resume_and_stream()`'s legitimate empty-user-text-but-real-response case.
- **Bonus fix (found live during verification, not in the original plan):** the first-pass
  recall-distance thresholds for `recall_facts`/`recall_procedures` were calibrated assuming
  distances in the same range as the pre-existing `recall()`/`recall_summaries()` cutoffs
  (0.5-0.6) — but ChromaDB's default ONNX EF (the fallback whenever Ollama isn't reachable,
  confirmed live-active on this dev machine) produces much larger distances in practice
  (~0.07 paraphrase, ~0.7 related-but-reworded, ~1.7+ unrelated). Recalibrated to 1.1/1.0 based
  on measured values so recall doesn't silently go empty under the fallback EF.

## [Faz 1] — 2026-07-14 — Local-first brain + model router

- **New `jarvis/providers/` module:** `get_llm(role, settings, *, tools=, max_output_tokens=)`
  resolves a role (`fast`/`local`/`realtime`/`reasoning`) to a concrete chat model. Replaces
  `graph.py`'s hardcoded `make_llm_fast`/`make_llm_pro` `ChatGoogleGenerativeAI` factories.
- **Ollama wired as the primary brain:** `fast`/`local`/`realtime` roles resolve to
  `ChatOpenAI` against Ollama's OpenAI-compatible endpoint (`qwen2.5:7b-instruct` by default),
  ahead of cloud — a real `.with_fallbacks()` safety net to whichever cloud tiers are configured
  covers Ollama being unreachable. `reasoning` (critic/planner/complex queries) now targets the
  AI Studio Gemini Flash *free* tier by default (not the 25-RPD Pro tier), with local Ollama as
  its own final fallback so no role is cloud-mandatory anymore.
- **`nomic-embed-text` via Ollama wired as the preferred embedding function** (`jarvis/memory.py`)
  for the `jarvis_docs`/`jarvis_summaries` RAG collections, ahead of the existing Gemini embedder;
  falls through cleanly if Ollama isn't reachable, and never disturbs an already-embedded
  collection's existing EF.
- **Fix (BUG-24):** removed a dead `cloud_tier == "pro"` branch in `effective_cloud_model`
  (`config.py`) — that value is never actually set.
- **Fix (BUG-22):** `switch_model()` now persists its settings onto
  `JarvisAgent._effective_settings`; the quota-exhaustion fallback rebuild in `chat()` uses that
  instead of the original construction-time settings, so it no longer silently discards a user's
  prior manual model switch.
- **Fix (found live while implementing the above, not previously catalogued):** a latent
  `AttributeError` — the pre-Faz-1 `make_llm_fast()` called `.bind_tools()` on the result of
  `.with_fallbacks()`, which doesn't have that method — never hit because recent runs had
  `use_vertex=False`. `get_llm()` now binds tools before wrapping fallbacks.
- **Fix (found live):** `ChatGoogleGenerativeAI`'s constructor validates that an API key is
  present, so eagerly constructing every cloud tier crashed `build_graph()` itself on a
  credential-less (pure-local) config — exactly the setup this phase is supposed to enable. Cloud
  tiers are now built defensively (`_safe_construct()`); a tier that can't even construct is
  dropped from the chain instead of raising.
- Quarantined (header note only, not deleted) the local-model path in
  `jarvis/legacy/agent_pydantic.py` — superseded by `jarvis/providers/get_llm()`; full retirement
  of `jarvis/legacy/` stays Faz 8.
- Environment: this dev machine had zero working model tiers at the start of this phase (Ollama
  not installed, Vertex ADC missing, AI Studio rate-limited) — fixed by installing Ollama and
  pulling both models (owner-approved). Full end-to-end verification then passed live: a real
  `chat()` turn answered via `qwen2.5:7b-instruct (Ollama, local)`, and tool-calling on the local
  model was confirmed directly. See [MEMORY.md](MEMORY.md) for a port-11434 race-condition gotcha
  hit while getting the server running cleanly.

## [Faz 0] — 2026-07-14 — Memory-critical stabilization

- **Fix (BUG-9):** `make_checkpointer()` now returns a real, persistent `SqliteSaver` instead of
  always discarding `db_path` and returning an in-memory `MemorySaver` — LangGraph checkpoints
  (needed to resume a turn interrupted for confirmation) now survive a restart. Custom
  executor-backed async wrapper, not `AsyncSqliteSaver`, since `JarvisAgent` is called from
  multiple independent event loops (`jarvis/graph/graph.py`).
- **Fix (BUG-8):** serialized `JarvisAgent`'s shared mutable state (`session_id`/`_turn`/
  `_history`) with a `threading.Lock` across `chat()`/`chat_stream()`/`resume_and_stream()`/
  `reset()`/`switch_session()`/`switch_model()` — previously unsynchronized across the main loop,
  `TaskExecutor`'s background thread, and `/reset` (`jarvis/agent.py`, `jarvis/api.py`).
- **Fix (BUG-10):** `session_store.py` read methods now take the same lock writers do; `save_turn`
  wraps its DELETE+INSERTs+UPDATE in one explicit transaction.
- **Fix (BUG-11):** `switch_session()` and `JarvisAgent.__init__`'s auto-resume path no longer
  reset the turn counter to 0, which could reuse an old LangGraph `thread_id` and resurrect a
  stale checkpoint into the current conversation (`SessionStore.last_turn_idx()`, new).
- **Fix (found during the above, not previously catalogued):** `session_store.py`'s
  `save_turn`/`load_history`/`load_full_history` were storing a full cumulative snapshot per turn
  but reading across multiple snapshot buckets as if they were deltas, duplicating messages in any
  short conversation. `save_turn` now collapses old buckets; readers select the latest only.
- **Fix (found during the above):** `_schedule_summary_backfill()` leaked an unawaited coroutine
  on every real startup (both entry points construct `JarvisAgent` before an event loop exists).
  Leak fixed; the underlying "backfill runs on startup" feature gap is not — see `BUG-backfill` in
  [ROADMAP.md](ROADMAP.md)'s appendix.
- Environment: `.venv` was rebuilt against Python 3.14.6 (the 3.13 install it depended on had been
  removed from disk); see [MEMORY.md](MEMORY.md).

## [Phase 1–4 refactor] — 2026-05-24 — Prompt modularization, ToolSpec metadata, confirmation gate, memory extraction

- Removed the dead, unregistered `email_triage` tool and its import from `graph/tools.py`
- **Phase 1:** split the monolithic `system.md` into 6 concern files under `jarvis/prompts/core/`, assembled at runtime by `jarvis/prompts/prompt_loader.py`
- **Phase 2:** added `ToolSpec` risk-metadata (`risk_level`, `requires_confirmation`, `side_effect_type`) for all tools in `jarvis/tool_registry.py`
- **Phase 3:** added a LangGraph confirmation node (`jarvis/graph/nodes.py`) + `POST /chat/confirm/{conf_id}` endpoint — interrupts before gated tool calls (not yet fully wired into CLI/voice; see `docs/SAFETY.md`)
- **Phase 4:** extracted memory/todo/entity/session-recall logic out of `agent.py` into `jarvis/context_builder.py` (`ContextBuilder`)
- Fix: `/vault/recent` used a dead `_docs.peek()` reference — switched to `_docs_collection.peek()`

## [Faz 21] — 2026-05-17 — Multimodal uploads, calendar batch_create, HUD panel visibility

- Image/PDF/file upload endpoint (`/chat/upload`) with 3-pipeline routing
- Calendar `batch_create` with dedup and natural-language date parsing
- Panel visibility system in Electron HUD (9 panels, per-panel show/hide)
- Fix: image upload refusal — mandatory `pdf_vision` instruction + system.md rule
- Fix: widget visibility, overlay position, calendar placeholder, hallucination rules

## [Faz 19–20] — 2026-05-15/16 — Task executor, WoL, FCM push, Flutter app

- `TaskExecutor` + `AsyncTask` — async long-running jobs with status polling
- Wake-on-LAN support
- Firebase Cloud Messaging (FCM) push notifications to Android app
- Flutter Android app (10 screens): chat, schedule, tasks, vault, finance, settings
- Kotlin `WakeWordService.kt` foreground service for always-on wakeword

## [Faz 18] — Geo-math + FDM wave simulation

- `geo_math` tool: symbolic math, WolframAlpha, seismic wave FDM modeling
- `GeoMathAgent` (pydantic-ai, Gemini Pro)

## [Faz 16–17] — Finance / GCP quota

- Burgan Bank statement parsing + budget tracking
- `finance` tool: sync, summary, top categories, chart, budget management
- GCP quota tracker (`gcp_quota` tool)

## [Faz 14–15] — Google Drive + ITU Webmail

- `google_drive` tool: search, list, read, download, upload, share, delete
- `itu_mail` tool: IMAP read + SMTP send for akgunlu22@itu.edu.tr

## [Faz 12–13] — Entity extractor, summarizer, scheduler, todos

- Automatic entity extraction from conversations → `jarvis_memory`
- Session summarizer → `jarvis_summaries` ChromaDB collection
- `schedule` tool: add/list/pause/resume/done/delete scheduled tasks (SQLite)
- `todo` tool: add/list/edit/done/delete/today/analyze todos (SQLite)

## [Faz 9–11] — FastAPI, Google OAuth, HUD WebSocket

- FastAPI REST server (`python -m jarvis --api`)
- Google Calendar + Gmail + Drive OAuth integration
- WebSocket HUD event bus (`JarvisEventBus`) — 30s/60s/120s live data cadence
- `JarvisMonitor` daemon for proactive email/calendar/schedule notifications

## [Faz 5–8] — Vertex AI, RAG, Spotify, wake-word

- Vertex AI Gemini 2.5 Pro for planner/critic; Flash for execution
- RAG: `index_doc` + `vault_search` over `jarvis_docs` ChromaDB collection
- `deep_web_research` + `url_read` (trafilatura + firecrawl)
- Spotify Web API (`spotify` tool)
- openwakeword "Hey JARVIS" (`--wakeword` mode)

## [Faz 3–4] — marker-pdf, plotting, data analysis

- `pdf_read` with marker-pdf (falls back to pdfplumber)
- `plot_data`, `data_analyze`, `csv_read`, `excel_read` tools
- LaTeX report pipeline (`report_write` + `report_compile`)

## [Faz 1–2] — LangGraph migration

- Replaced pydantic-ai orchestrator with LangGraph `StateGraph`
- Graph topology: `START → route_from_start → {planner,agent} → tools → critic → END`
- 5 pydantic-ai sub-agents (math/writer/research/coder/geomath) retained via `_run_coro` bridge
