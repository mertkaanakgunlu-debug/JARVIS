# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-22 (9. oturum) — AGENT RUNTIME REV.2 FAZ 4 KODLANDI, TEST EDİLDİ, COMMIT'LENMEDİ

**Durum tek cümlede:** Faz 4 (verified response composition + claim audit) **kodlandı ve test
edildi (704 pytest yeşil = 673 + 31 yeni, ruff temiz)** — owner'dan açık bir commit isteği
gelmedi, Faz 2/3'ün `f4b7609`'dan önceki "built, not yet committed" durumuyla aynı. Çalışan
ağaçta yalnız `.claude/settings.local.json`'da bu oturumdan önce de duran, ilgisiz lokal izin
eklemeleri var; HEAD hâlâ `8b3cd1f`, `langgraph-migration` == `origin/langgraph-migration`.

## Bu oturumda yapılanlar (Faz 4 — plan §Faz 4, reviewer #4'ün "mimarinin en güçlü düzeltmesi" dediği faz)

Plan diyagramı: `ExecutionEnvelope[] → VerifiedExecutionSummary → constrained ResponseComposer →
claim audit`. Hedef, compose_node'un halihazırda var olan Faz 1.4 korumasının **yakalayamadığı**
sınıfı kapatmaktı: **kısmi başarı** — 2 tool'dan 1'i başarılı, model ikisini de başarılı iddia
ediyor. Faz 1.4 yalnız "hiçbiri başarılı olmadı" durumunu kontrol ediyor
(`completed_tool_fingerprints` boşsa uyarıyor); kısmi başarıda bu liste boş DEĞİL (bir şey
gerçekten başardı), o yüzden eski guard hiç tetiklenmiyordu — plan'ın kendi sözleriyle "bugün
geçiyor" (yakalanmıyor).

1. **`jarvis/execution/summary.py`** (yeni) — `VerifiedExecutionSummary`/`VerifiedOperation`
   (pydantic, `jarvis/execution/*`'ın geri kalanıyla aynı stil). Her `ExecutionEnvelope`'u iki
   sinyalin birleşimine indirger: `tool_status` (envelope'un kendi `status`'ü — tool'un KENDİ
   iddiası) ve `postcondition_verdict` (yalnız `severity="required"` postcondition'lardan
   türetilen BAĞIMSIZ doğrulama — `warning` severity hiçbir zaman bir başarıyı düşürmez).
   `_display_status` bunları 5 değere indirger: `confirmed` (tool ok + bağımsız doğrulama ok),
   `reported_success_unverified` (tool ok, bağımsız kontrol yok/uygulanamaz — bugün 36 tool'un
   neredeyse tamamı, yalnız `file_write` postcondition taşıyor), `reported_success_verification_
   failed` (**B6'nın gerçek sınıfı** — tool "ok" diyor ama bağımsız kontrol AYNI FİKİRDE DEĞİL),
   `failed` (tool `failed`/`blocked`/`invalid_args`/`timed_out` diyor — postcondition'dan
   bağımsız, tool'un kendi başarısızlık iddiası her zaman kazanır), `partial`.
   `VerifiedExecutionSummary.any_failed` (`failed`/`partial`/`reported_success_verification_
   failed`'in HERHANGİ biri) — Faz 4'ün asıl mekanizmasının tetikleyicisi budur.
2. **İki renderer, iki farklı hedef kitle:** `render_operation_status_for_model` (enforce modda
   composer LLM'e ham ToolMessage'lar YERİNE beslenen SystemMessage — plan'ın kendi sözleri:
   "deterministik operation status bloğu koda gömülür, modele bırakılmaz") ve
   `render_operation_status_for_user` (final yanıta EKLENEN, kullanıcıya yönelik, model
   talimatı İÇERMEYEN aynı gerçekler).
3. **`audit_claims`** — TR/EN, bilinçli olarak muhafazakâr regex tabanlı İKİNCİL sinyal. Plan'ın
   kendi sözleri: "claim extractor kalır ama ikincil — ana güvenlik sınırı değil, audit ve
   gözlem." Yalnız (a) en az bir operasyon gerçekten başarısızken VE (b) yanıt niteliksiz/toptan
   bir başarı ifadesi içeriyorken VE (c) yanıtta HİÇBİR olumsuzlama/başarısızlık kelimesi
   yokken tetikleniyor — "dosyayı oluşturdum ama e-postayı gönderemedim" gibi doğru bir yanıtı
   YANLIŞLIKLA işaretlemiyor. İhlaller artık execution_id taşıyor (plan: "her işlem iddiası
   mümkün olduğunca bir execution_id ile ilişkilendirilir") — aynı capability bir turda birden
   fazla kez geçebilir (retry, iki farklı dosya), yalnız isimle belirsiz kalırdı.
4. **`compose_node` (`jarvis/graph/nodes.py`) yeniden kablolandı** — Faz 1'in AYNI
   `execution_contract_mode` merdiveni yeniden kullanıldı (yeni bir Settings alanı YOK):
   - **`off`** (varsayılan) veya envelope yok → sıfır davranış değişikliği, Faz 1.4 guard'ı
     olduğu gibi duruyor. `summary` bile inşa edilmiyor — mode=="off" kontrolü, elle kurulmuş
     bir state dict'in (direct-node testi, eski checkpoint) bile bu sözleşmeyi bozamayacağı
     şekilde, Faz 1'in "off modda hiçbir yeni kod çalışmaz" disipliniyle bire bir.
   - **`shadow`** → invocation/response'a DOKUNULMUYOR (raw ToolMessage'lar kalıyor,
     ground-truth SystemMessage'ı enjekte edilmiyor) — yalnız `audit_claims` hesaplanıp bulgu
     varsa `audit_log.record("claim_audit", mode=, enforced=False, ...)` ile loglanıyor. Bu,
     plan'ın "annotate ile ölç, sonra enforce" adımı — gerçek trafik üzerinde modelin ne
     sıklıkla aşırı iddia ettiğini davranışı DEĞİŞTİRMEDEN ölçmek. **`test_shadow_replay_
     equivalence.py` bozulmadı** — `audit_log.jsonl` `paths.data_dir()`'a (cwd/JARVIS_HOME'a
     bağlı) yazılıyor, o testin `_side_effects()`'i yalnız izole `workspace` ağacını tarıyor,
     ikisi asla kesişmiyor; ayrıca shadow modda `response`/`messages` zaten hiç değişmediği
     için bit-eşitlik kendiliğinden korunuyor (doğrulandı: 45/45 hedef test + tam suite yeşil).
   - **`enforce_read_only`/`enforce_reversible`/`enforce_all`** (Faz 2/3'teki gibi üçü de bugün
     AYNI davranıyor, aralarında henüz fark yok — önceki hiçbir fazın da yapmadığı ayrım,
     bilinçli tutarlılık) → (a) invocation'daki ham `ToolMessage`'lar ÇIKARILIYOR, yerine
     `render_operation_status_for_model`'ın SystemMessage'ı ekleniyor — model artık "başarılı
     mı" kararını kendi veremiyor, kod söylüyor. (b) LLM'den yanıt geldikten SONRA, **`any_
     failed` doğruysa** — modelin ne dediğinden TAMAMEN BAĞIMSIZ, yalnız envelope verisine
     dayanan yapısal bir kural — `render_operation_status_for_user`'ın bloğu yanıta KOŞULSUZ
     ekleniyor (hem `response` alanına hem döndürülen `AIMessage`'ın kendisine — sonraki bir
     turun geçmişi yanlış iddiayı tekrar edemesin diye, Faz 1.4'ün kendi endişesiyle aynı). Bu,
     kabul testinin asıl mekanizması — `audit_claims`'in metin deseni YAKALAYAMASA bile (yeni
     bir test bunu özellikle kanıtlıyor: `test_enforce_mode_appends_even_when_the_text_pattern_
     audit_stays_silent`) gerçek durum HER ZAMAN kullanıcıya ulaşıyor.
5. **31 yeni test** — `tests/test_execution_summary.py` (20, saf fonksiyonlar: postcondition
   agregasyonu, display_status türetimi, B6 sınıfı, malformed envelope'un fatal olmaması, iki
   renderer, audit_claims'in pozitif/negatif/hedge senaryoları) + `tests/test_verified_response_
   composition.py` (11, compose_node'un kablolanması: off/shadow/enforce mode gate'leri,
   ToolMessage stripping, koşulsuz ekleme, audit_log çağrısı). **704 pytest yeşil (673+31), ruff
   temiz.**

**Bilinçli kapsam kararları (plan'ın "dürüst risk" bölümüyle uyumlu, sonraki oturuma not):**
- Ham `ToolMessage`'lar enforce modda TAMAMEN çıkarılıyor, yalnız `envelope.normalized_output`
  (zaten Faz 1'den beri `redact_preview` ile 200 karaktere kırpılmış) kalıyor — plan'ın "yerine
  yapılandırılmış özet girer" ifadesinin en dürüst okunuşu, ama nesir kalitesi riski gerçek:
  bilgi-döndüren tool'lar (web_search, calendar list...) için model artık yalnızca 200
  karakterlik bir özetle çalışıyor. Bu, "annotate ile ölç" adımının TAM OLARAK ölçmesi gereken
  şey — canlı shadow trafiği toplanmadan enforce'a geçilmemeli.
- `audit_claims`'in TR/EN desen listesi kasıtlı olarak dar/muhafazakâr — tam NLU değil, yanlış
  pozitifi düşürmek için. İkincil sinyal olduğu için bu kabul edilebilir (asıl güvence yapısal
  `any_failed` kuralı, yukarıya bakın).
- `any_unverified` (bağımsız doğrulaması olmayan ama tool'un "ok" dediği çağrılar — bugün 36
  tool'un neredeyse tamamı) koşulsuz ekleme kuralını TETİKLEMİYOR, yalnız `any_failed`
  tetikliyor — aksi halde hemen her turda ekstra blok eklenir, "sınırlı nesir maliyeti" hedefini
  bozardı. Bilinçli seçim, plan metninde açıkça zorunlu kılınmamış.
- Canlı A/B (plan §D: "Faz 1/3/4/6 sonunda") **bu oturumda YAPILMADI** — mode hâlâ varsayılan
  "off", yani bu faz production'da tamamen inert; Faz 2/3'te de aynı gerekçeyle ertelendi, yalnız
  owner'ın commit isteğiyle birlikte gündeme geldi. Owner isterse sıradaki adım olabilir.

## SONRAKİ OTURUM — kalan iş

1. **Owner'ın commit kararı bekliyor** (Faz 2/3 ile aynı desen) — istenirse CHANGELOG.md'ye Faz 4
   girdisi eklenip tek commit'te push'lanabilir.
2. **Faz 5 — Isolation & reproducibility** (plan §Faz 5, sıradaki P0 faz): `jarvis/run_context.py`
   (`RunContext(run_id, ...)`, run-scoped `data/runs/<run_id>/` — bugün her şey tek ağaçta,
   `plot.png` çakışıyor), scope ayrımı (user/conversation/workflow/run/execution/artifact),
   sessiz session auto-resume'un TAMAMEN kaldırılması (`agent.py:550-562`'nin `latest_session()`
   tahmini — CLI kendi state'inde son session id'yi açıkça taşıyarak sürekliliği korur, sihir
   kalkar), iki `Path.home()` bypass'ının kapatılması (`vad.py:35`, `tts_piper.py:36`), AST
   tabanlı kalıcı guard testi (`tests/test_no_host_path_leak.py` — `expanduser`/`Path.home()`/
   `USERPROFILE`/`os.getcwd` taraması, beyaz listedeki iki yardımcı hariç), `run_manifest.json`
   (model+sürüm, prompt hash, registry version, temperature, tool subset, input digest, envelope
   listesi → replay), cross-session prompt bloklarının eval profilinde kapatılması, 4 hardcoded
   temperature'ın Settings'e taşınması.
3. Canlı A/B'nin B6 sorusu hâlâ açık (değişmedi).
4. `gh` CLI yetkilendirmesi — owner aksiyonu bekliyor (değişmedi).
5. Şampiyon 62/65 referansı kontamine (değişmedi).
6. Bilinçli ertelenenler (değişmedi + Faz 4'ün kendi kalemleri, yukarıdaki "Bilinçli kapsam
   kararları" bölümü): W4b, `[BLOCKED]` sunum katmanı, qwen3.5/ministral-3 thinking-on,
   `stoic-spence` rolling summarization, `docs/ARCHITECTURE.md` orchestrator bölümü, Faz 2'nin
   `target_resource` best-effort + hep `"no_contract"` TaskContract match, Faz 3'ün
   `external_request_timeout`/`geo_math`/`series_matches` kalemleri.

## Ortam / komutlar
```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q                      # 704 test, offline, ~2.5 dk (pytest-timeout KURULU DEĞİL)
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py
```
Env: `LOCAL_MODEL=qwen3:8b`, `LOCAL_REASONING_EFFORT=none` — CONFIRMED. `EXECUTION_CONTRACT_MODE`
default `off`. Faz 4'ün `compose_node` değişiklikleri TAMAMEN bu flag'e bağlı (Faz 2/3'ün onay/
timeout/postcondition mekanizmalarının aksine — onlar her zaman açık, kill switch'le aynı
kategoride). Faz 4 Faz 1'in ladder'ını genişletiyor: `off` = sıfır yeni davranış, `shadow` =
yalnız gözlem, `enforce_*` = gerçek gating.
Koşum artifact'leri: `C:\Temp\jarvis-ab\`. **`ab_analyze.py`'yi PowerShell'den çalıştırın, Git
Bash'ten değil.**

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — 6. oturumun bu başlık altında raporladığı gözlem
  `5941f63`'te yanlış-sunucu hatası olarak açıklandı; gerçek bir kozmetik-label sorunu kaldıysa
  yeniden gözlemlenmesi gerekiyor.
