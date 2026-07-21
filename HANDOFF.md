# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-22 (9. oturum) — FAZ 4 COMMIT'LENDİ, FAZ 5 (İZOLASYON & TEKRARLANABİLİRLİK) TAMAMEN KODLANDI VE TEST EDİLDİ

**Durum tek cümlede:** Faz 4 owner isteğiyle commit'lendi (`9c0ca15`, push'lanmadı — yalnız
commit istendi), ardından Faz 5'in plandaki YEDİ maddesinin HEPSİ bu oturumda kodlandı ve test
edildi (**743 pytest yeşil** = 704 (Faz 4 sonrası) + 39 yeni Faz 5 testi, ruff temiz). **Faz 5
henüz commit'lenmedi** — owner'ın açık isteği bekleniyor, aynı "built, not yet committed" deseni.

## Bu oturumda yapılanlar

### 1) Faz 4 commit'i
Owner "commit et sonra devam et" dedi. `CHANGELOG.md`'ye Faz 4 girdisi eklendi (Faz 2+3
`f4b7609`'un aynı deseni), `.claude/settings.local.json` HARİÇ (ilgisiz lokal izin değişikliği)
7 dosya commit'lendi: `9c0ca15`. Push edilmedi — yalnız commit istenmişti.

### 2) Faz 5 — Isolation & reproducibility (plan §Faz 5, tamamı)

**a) İki `Path.home()` izolasyon açığı kapatıldı** (`jarvis/voice/vad.py:35`,
`jarvis/voice/tts_piper.py:36`) — ikisi de modül-seviyesi, import-zamanında donmuş sabitlerdi,
`JARVIS_HOME` set olsa bile GERÇEK `~/.cache/jarvis/...`'a bakıyorlardı (test/eval izolasyonunu
bypass ediyordu). Yeni `jarvis.paths.cache_dir()` — `jarvis/tools/files.py`'nin
`_effective_home()`'uyla AYNI desen (production'da gerçek home — model cache'leri launch
dizininden bağımsız kalıcı olmalı — ama `JARVIS_HOME` set'liyken yönlendirilir), ama BAĞIMSIZ bir
implementasyon (paths.py'nin tools/files.py'a bağımlı olması ters katmanlama olurdu). Her ikisi de
artık çağrı-anında çözülen fonksiyonlar (`_default_cache_path()`/`_default_cache_dir()`), import-
zamanı sabit değil.

**b) Kalıcı AST guard testi** — `tests/test_no_host_path_leak.py`. `jarvis/` altındaki HER `.py`
dosyasını AST ile tarıyor (`expanduser`/`Path.home()`/`getcwd`/`"USERPROFILE"` literal'i);
`jarvis/tools/files.py` ve `jarvis/paths.py` (yukarıdaki (a)'nın kendisi) beyaz listedeki İKİ
yardımcı — plan'ın "iki yardımcı" ifadesi bu ikisiyle tam örtüşüyor, her biri ayrı ve gerekçeli bir
gerçek-home-fallback deseni implemente ediyor. Üçüncü bir sızıntı artık CI'da kırılır.

**c) 4 hardcoded temperature → Settings** — `finance_extractor_temperature` (0.0),
`session_summarizer_temperature` (0.2), `email_triage_temperature` (0.0),
`deep_research_temperature` (0.3, 2 çağrı noktası tek setting paylaşıyor). Değerler değişmedi,
yalnız artık `config.py`'de görünür/ayarlanabilir ve gelecekteki `run_manifest.json`'a
(aşağıya bakın) yansıyabilir.

**d) Sessiz session auto-resume TAMAMEN kaldırıldı** — `JarvisAgent.__init__`'in eskiden
koşulsuz çağırdığı `session_store.latest_session()` tahmini gitti (production'da da, plan'ın
kendi sözü). Yeni `resume_session_id` keyword-only parametresi + factored-out `_resolve_initial_
session()` (bu suit'in "gerçek JarvisAgent inşası ağır, mantığı ayrı test et" disipliniyle —
bkz. `test_reset_lifecycle.py`'nin docstring'i — direkt test edilebilir). `SessionStore` yeni
`session_exists()` metodu kazandı (bir resume id'sini körlemesine güvenmeden önce doğrulamak
için — geçersiz/yabancı bir id sessizce taze session'a düşer, hataya değil). **CLI kendi
sürekliliğini kendi korur**: `cli.py`'nin yeni `_read_last_session_id()`/`_write_last_session_id()`
(JARVIS_HOME farkında, `data/cli_last_session.txt`) — construction'dan önce okunur ve
`resume_session_id=` olarak geçirilir; `/session <id>` ve `/reset` sonrası yeniden yazılır. **API
hiçbir sürekliliği korumuyor** — bugün API'nin zaten hiçbir per-client conversation_id mekanizması
YOK (`_agent = JarvisAgent(settings)` çağrısı hiç değişmedi, varsayılan `resume_session_id=None`
zaten doğru davranışı veriyor), yani her API (yeniden) başlatması artık TEMİZ bir session ile
başlıyor — plan'ın kendi kelimeleriyle "production'da da" kaldırılan tam olarak bu davranıştı.
**Bilinçli, gerçek bir trade-off, owner'a not:** Electron HUD/mobil gibi bir API istemcisi artık
sunucu her yeniden başladığında konuşma geçmişini KAYBEDER (öncesinde en son aktif session'ı
sessizce devralıyordu) — gerçek, per-client `conversation_id` desteği inşa edilene kadar (bu
oturumun kapsamı dışında, ayrı ve daha büyük bir REST tasarım işi — bkz. aşağıdaki "sonraki
oturum").

**e) Cross-session prompt blokları eval profilinde kapalı** — `ContextBuilder.build()`'ın
`recall_summaries`/`recall_facts`/`recall_procedures` çağrıları artık `JARVIS_TEST_MODE=1`
altında (`--profile test`'in zaten set ettiği bayrak, yeni bir setting DEĞİL) tamamen
ATLANIYOR — yalnız sonuç boş değil, ÇAĞRI HİÇ YAPILMIYOR (yeni bir test bunu özellikle
kanıtlıyor: sahte memory çağrılırsa `AssertionError` fırlatıyor). Gerekçe: uzun ömürlü bir
`--profile test`/A/B harness süreci onlarca mantıksal olarak BAĞIMSIZ senaryoyu art arda
çalıştırıyor; `procedure_save` düz bir tool çağrısı (CLOUD_POLICY'den bağımsız, gerçek bir canlı
sızıntı vektörü) olduğu için önceki bir senaryonun yazdığı bir procedure SONRAKİ senaryonun
promptuna sızabiliyordu — muhtemelen zaten bilinen "şampiyon 62/65 kontamine" bulgusunun
mekanizması. Facts/summaries için risk CLOUD_POLICY=off altında zaten daha düşük (extraction'ları
da o bayrakla dejenere oluyor) ama gate hepsini tutarlı şekilde kapsıyor. Episodic `recall()`
(zaten Faz 2'den beri session-scoped) ETKİLENMEDİ, yalnız üç cross-session blok.

**f) `jarvis/run_context.py`** (yeni) — `RunContext(run_id, workspace)` + `artifact_dir`
property (`workspace/data/runs/<run_id>/`) + `write_run_manifest(ctx, **fields)`. İki
constructor: `for_turn(workspace, session_id, turn)` (LangGraph'ın kendi thread_id string'ini
AYNEN yeniden kullanır — `"{session_id}-t{turn}"` — paralel bir ID şeması DEĞİL) ve
`for_execution(workspace)` (per-turn state'e erişimi olmayan bir tool closure'ı için taze/tekil
bir run — bkz. (g)). **`run_manifest.json` `chat()` VE `chat_stream()`'in ikisine de kablolandı**
(her turn sonunda, `save_turn`'ün hemen ardından, hata fırlatmayan bir çağrı): model+provider
(canlı trace'ten), temperature, tool_subset, input_digest (sha256), execution_envelopes (mode≠off
ise), transport. **Dürüst eksik:** plan'ın istediği "prompt hash" ve "registry version" alanları
POPÜLE EDİLMEDİ — bu codebase'de ikisinin de karşılığı yok (composed system prompt'u agent.py'den
ucuzca hash'leyecek bir yol yok; `tool_registry.py`'de bir "versiyon" kavramı hiç yok) — icat
etmek yerine dürüstçe atlandı. **Wiring sırasında GERÇEK bir bug bulundu ve düzeltildi**
(hipotetik değil): `chat_stream()`'in checkpoint okuma `try/except`'i `checkpoint_tuple`'ı YALNIZ
başarılı atamada tanımlıyordu — `get_tuple()` fırlatırsa değişken hiç tanımlanmamış kalıyordu,
manifest kodu ona erişince `NameError` verecekti. `checkpoint_tuple = None` ön-ataması eklendi;
yeni bir test (`test_chat_stream_manifest_survives_a_checkpointer_error`) bu spesifik senaryoyu
kilitliyor.

**g) `plot_data` run-scoped'a taşındı** — plan'ın kendi motive edici örneği ("bugün her şey tek
ağaçta, plot.png çakışıyor") artık kapalı: `workspace/data/plots/` (paylaşılan, düz, çakışma
riski taşıyan) yerine her çağrı `RunContext.for_execution(workspace).artifact_dir`'e (taze,
çakışmasız) yazıyor. **Dürüst kapsam kararı:** `plot_data` düz bir `@tool` closure'ı — Faz1'den
beri `make_tools()` tarafından SÜREÇ başına bir kez inşa ediliyor, turn başına DEĞİL — gerçek
per-TURN run_id enjeksiyonu LangGraph'ın `InjectedState` mekanizmasını gerektirirdi (bu
codebase'de hiç kullanılmamış, kendi başına bir mimari değişiklik, bu oturumun kapsamı dışı).
Bunun yerine her ÇAĞRI kendi taze/tekil run_id'sini alıyor — somut çakışma bug'ını çözüyor ama AYNI
turn'den birden fazla artifact'ı (örn. bir rapor + gömülü grafikleri) TEK bir run klasöründe
GRUPLAMIYOR henüz. `report_write`/`csv`/`excel`/`index_doc`/`note_append` gibi diğer artifact-
üreten tool'lar HİÇ dokunulmadı (aynı Faz 3'ün postcondition kapsamı gibi: gerçek mekanizma inşa
edildi, dar ama gerçek bir tüketiciye kablolandı, gerisi dürüstçe "yapılmadı").

**"Scope'lar açıkça ayrılır" (reviewer #8) — durum:** user (JARVIS_HOME) · conversation
(session_id) · run/turn (yeni RunContext) · execution (Faz 2'nin execution_id) · artifact (yeni
artifact_dir) — hepsinin artık kod-seviyesi ayrı bir kimliği var. **workflow** scope'unun HİÇBİR
karşılığı yok — bu orijinal planın Faz 7'si ("Workflow runtime"), henüz başlamadı, plan'ın kendi
"paralel evren kurmaz" notuna göre Faz 5'in şimdi icat etmesi beklenmiyor zaten.

**Test:** 17 yeni dosya/blok — `tests/test_no_host_path_leak.py` (3), `tests/test_jarvis_home.py`
+4 (cache_dir), `tests/test_session_store.py` +3 (`session_exists`), `tests/test_session_resume.py`
(9, yeni dosya), `tests/test_context_builder.py` +3, `tests/test_run_context.py` (11, yeni dosya),
`tests/test_run_manifest_integration.py` (4, yeni dosya, GERÇEK `chat()`/`chat_stream()` gövdesini
`JarvisAgent.__new__` + minimal sahte collaborator'larla sürüyor — `test_interrupt_surface.py`'nin
zaten kurduğu desen), `tests/test_plot_inline.py` +2, `jarvis/finance_extractor.py`/
`session_summarizer.py`/`tools/deep_research.py`/`tools/email_triage.py` (mevcut testler regresyon
yok). **Tam suite + ruff her checkpoint'te koşuldu, hiçbir noktada kırmızı kalmadı.**

## SONRAKİ OTURUM — kalan iş

1. **Owner'ın Faz 5 commit kararı bekliyor.**
2. **Faz 6 — Typed schemas + bounded repair** (plan §Faz 6, sıradaki P1 faz): alpha allowlist'teki
   her capability için `args_schema` (öncelik `plot_data`'nın path XOR data_json discriminated
   union'ı, action-dispatch tool'larının `action: str` → `Literal[...]`), unknown-field rejection,
   bounded repair (normalize → validate → tek repair → alternatif capability → açık hata, repair
   sonrası Faz 2'nin approval binding'i yeniden onay ister), `[INVALID_ARGS:<field>]` reason code.
3. **API'nin gerçek per-client conversation_id desteği yok** (bu oturumun kendi, yukarıda (d)'de
   detaylandırılan trade-off'u) — bir Electron/mobil istemcinin kendi conversation_id'sini
   gönderip sunucu tarafında bir `switch_session()`/`new_session()` kararına dönüştüren gerçek bir
   REST tasarımı hâlâ yapılmadı. CLI'nin kendi çözümü (dosya tabanlı, tek kullanıcı) bu ihtiyacı
   karşılamıyor.
4. **`run_manifest.json`'ın prompt hash / registry version alanları boş** — ikisinin de bu
   codebase'de bir karşılığı yok, icat edilmedi (yukarıya (f) bakın).
5. **`plot_data` dışındaki artifact-üreten tool'lar run-scoped değil** (report_write, csv/excel
   çıktıları, index_doc, note_append) — hepsi hâlâ eski paylaşılan konumlarına yazıyor.
6. Canlı A/B'nin B6 sorusu hâlâ açık (değişmedi). `gh` CLI yetkilendirmesi — owner aksiyonu
   bekliyor (değişmedi). Şampiyon 62/65 referansı kontamine (değişmedi — (e)'nin bu oturumdaki
   fix'i gelecekteki run'lar için önler, GEÇMİŞ referansı düzeltmez).
7. Bilinçli ertelenenler (değişmedi + Faz 4/5'in kendi kalemleri, ilgili bölümlere bakın): W4b,
   `[BLOCKED]` sunum katmanı, qwen3.5/ministral-3 thinking-on, `stoic-spence` rolling
   summarization, `docs/ARCHITECTURE.md` orchestrator bölümü.

## Ortam / komutlar
```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q                      # 743 test, offline, ~2.5-3 dk (pytest-timeout KURULU DEĞİL)
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py
```
Env: `LOCAL_MODEL=qwen3:8b`, `LOCAL_REASONING_EFFORT=none` — CONFIRMED. `EXECUTION_CONTRACT_MODE`
default `off`. Faz 5'in YENİ davranış değişiklikleri (session auto-resume kaldırma, eval-profile
cross-session gate, run_manifest.json, plot_data'nın yeni konumu) **execution_contract_mode'a
BAĞLI DEĞİL** — Faz 2/3'ün onay/timeout mekanizmaları gibi her zaman aktif (yalnız Faz 4'ün
compose_node değişiklikleri o merdivene bağlıydı). `JARVIS_TEST_MODE` yalnız `--profile test`
tarafından set edilir, elle set edilmemeli.
Koşum artifact'leri: `C:\Temp\jarvis-ab\`. **`ab_analyze.py`'yi PowerShell'den çalıştırın, Git
Bash'ten değil.**

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — değişmedi.
