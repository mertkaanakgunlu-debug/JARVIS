# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-18 — P0 tamam + P1 latency fix (GPT_Analysis 2. tur planı)

**Bağlam:** Owner GPT-5.6'nın 2. review'unu (`GPT_Analysis.md`) verdi; birlikte analiz edip
onaylı plan (`.claude/plans/...optimized-tiger.md`, kapsam: **Hepsi P0+P1+P2, tüm 13 domain**)
çıkardı. Bu oturum sıralı+faz-sonu-commit ile P0'ı bitirdi ve P1'in çekirdeğini kanıtladı.

**7 commit, `langgraph-migration`. 391/391 pytest yeşil, ruff temiz.** Sırayla:
`4b1eec6` P0-A mekanik · `fe557a3` P0-A davranışsal · `54fc533` diacritic+closure ·
`417e205` oracle harness+trace · `2f77439` P1 thinking-off.

## Bu oturumda ne yapıldı

### Faz 1 — P0-A (doğruluk & izolasyon), hepsi testli
1. **Audit `ok`** — `_record_execution_end` `str(ToolMessage)` yerine `.content`'e bakıyor;
   kanonik `content_is_failure()` (tool_accounting) yeniden kullanıldı; `⚠` kanonik
   `_FAILURE_PREFIXES`'e eklendi. B6/Calendar/Gmail artık doğru `ok:false` logluyor.
2. **`shell_run` workspace** — `shell.run(cwd=workspace)` + cd/Set-Location escape guard.
3. **`plot_data` inline** — `data_json` (JSON array/obj), `path` opsiyonel; B6 kök nedeni kapandı.
4. **History-echo guard** — compose_node: route tool bekliyor + bu turn `completed_tool_fingerprints`
   boş → node-local SystemMessage "tamamlanma iddia etme". B6 + D13b sınıfını kapatıyor.
5. **Harness senaryo izolasyonu** — driver her senaryo öncesi `/reset` (A3/B5b hariç); D13b artık
   D10'dan izole.

### Faz 2 — P0-B (oracle'lı ölçüm altyapısı)
- **`scripts/eval_oracle.py`** — `Expected`/`Observed`/`score`: trace + dosya sistemi + yanıtı
  BİRLİKTE denetliyor (yanıta asla tek başına güvenmiyor). B6-sınıfı yanlış-pozitif otomatik
  yakalanıyor. Saf + unit-testli (12 test).
- **`jarvis/tool_trace.py`** — L1 dahil HER tool çağrısı `data/tool_trace.jsonl`'e; `JARVIS_TOOL_TRACE`
  ile gated (test profilinde otomatik). Audit'in L1-körlüğünü kapatıyor.
- **Driver** — senaryo-başına trace temizleme + oracle skorlama + özet. B6/D11/C9/D12 auto-score;
  cred/key gerektiren (calendar/mail/web_search) senaryolar **skorlanmıyor**, yeşile boyanmıyor.
- **G17a/G17b** — restart-persistent-memory senaryosu (fresh session'da recall). **Offline'da
  extractor degraded olduğu için FAIL beklenir** — sınırı gizlemeden raporlar.
- **13-domain closure (2.4)** — hepsi membership-closure geçiyor. **YENİ BULGU + FIX:** router
  diacritic'e duyarlıydı; ASR/gündelik yazım ç/ğ/ı/ö/ş/ü veya `'` düşürünce yanlış yönlendiriyordu
  (`grafik ciz`→conversation, `Drivea yukle`→files/google_drive görünmez). `_fold()` ile
  sorgu+pattern ASCII'ye foldlanıyor; Türkçe İ/ı casing tuzağını da kapatıyor. **Bu owner'ın
  "artık takvime ekleyemiyor" şikayetiyle aynı sınıfta olabilir (sesle konuşuluyorsa).**

### Faz 3 — P1 çekirdeği (qwen3 no-thinking) — CANLI DOĞRULANDI
- **Planın varsaydığı yöntemler `/v1`'de ÇALIŞMIYOR** (canlı test, Ollama 0.32): `/no_think`
  token, top-level `think:false`, `chat_template_kwargs{enable_thinking:false}` — üçü de yok
  sayıldı (~160 reasoning token).
- **Çalışan yöntem:** OpenAI-standard `reasoning_effort="none"` — Ollama 0.32 `/v1`'de onurlandırılıyor.
  Client değişikliği/yeni bağımlılık YOK (langchain-ollama kurulu değil). `ChatOpenAI(reasoning_effort=
  "none")`: **172→2 token, 7.15s→0.52s (~14x)**; temsili Türkçe `file_write` tool call **birebir aynı**
  (8.2s→1.5s), tool-calling regresyon YOK.
- **Uygulama:** `config.local_reasoning_effort` (default `"none"`); `_make_local(reasoning_effort=...)`;
  yalnız fast/local/realtime rolüne uygulanıyor, reasoning-rol fallback'i tam thinking'te kalıyor.
  `LOCAL_REASONING_EFFORT=""` ile geri alınır. 7 test.

## SONRAKİ OTURUM — kalan iş (çoğu CANLI, senin makinende)

1. **Faz 3.3 — thinking on/off A/B (EN ÖNCELİKLİ).** Artık oracle otomatik pass/fail veriyor.
   Default `"none"` (thinking off) **tek senaryo spot-check'iyle** shipped — 16 senaryo ×5'te
   doğruluğun korunduğu DOĞRULANMALI. Koşum:
   ```powershell
   ollama serve            # ayrı pencere (zaten çalışıyor olabilir)
   $env:JARVIS_TEST_HOME = "C:\...\stable-test-home"
   $env:JARVIS_TEST_RESULTS = "C:\...\ab_off.jsonl"
   python -m jarvis --api --profile test --port 8132     # terminal 1
   python scripts/manual_test_driver.py --all             # terminal 2 → sonunda ORACLE x/y özeti
   # thinking-ON turu için: server'ı LOCAL_REASONING_EFFORT="" ile başlat, ab_on.jsonl'e yaz
   ```
   Doğruluk düşerse tek env var (`LOCAL_REASONING_EFFORT=""`) ile thinking geri gelir.
2. **Faz 3.2 — latency enstrümantasyonu** (KURULMADI): cold/warm ayrımı + TTFT + reasoning-token
   sayısı. Şimdilik driver'ın `elapsed_s`'i + tool_trace yeterli sinyal veriyor; isteğe bağlı.
3. **Faz 4 — challenger'lar** (CANLI, model pull + owner judgment): `qwen3.5:9b` Q4_K_M,
   `ministral-3:8b` Q4_K_M. Aynı oracle harness. 8K ctx başla; Türkçe + (qwen3.5) multimodal ayrı
   ölç. `config.local_model` sadece kazanan default'a alınırsa değişir.
4. **G17b restart-memory** offline'da FAIL edecek (extractor degraded) — beklenen; kalıcı hafıza
   ayrı iş.
5. **`main` merge YAPMA** — bu A/B doğrulaması bitmeden değil.

## Ortam / komutlar
```powershell
.\.venv\Scripts\Activate.ps1
pytest                                   # 391 test, offline
ruff check jarvis/ tests/                # temiz (scripts/auth_setup.py'de pre-existing F541 var, alakasız)
```
Yeni env vars: `JARVIS_TOOL_TRACE` (test profilinde otomatik 1), `LOCAL_REASONING_EFFORT`
(default `none`; `""` → thinking on). Ollama 0.32.1, qwen3:8b (thinking cap'li) + qwen2.5:7b mevcut.

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor.
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — açık.
