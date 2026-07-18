# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-18 (3. oturum) — TAM A/B KOŞULDU: thinking-off default DOĞRULANDI

**Bağlam:** Round-3 ölçüm düzeltmeleri sonrası owner "testleri yap" dedi; 16 senaryo × 5 tur ×
2 konfig (thinking off `LOCAL_REASONING_EFFORT=none` vs on `""`) canlı A/B bu oturumda koşuldu.
**Sonuç: doğruluk BİREBİR AYNI (60/65 vs 60/65), thinking-off konuşma turnlerinde 3-6x hızlı,
run başına duvar süresi ~%25 kısa → default `none` KALIYOR.** 421/421 pytest, ruff temiz.

## A/B sonuçları (özet — tam rapor `C:\Temp\jarvis-ab\results\ab_report.md`)

- **Oracle:** 13 skorlu senaryonun 12'si her iki konfigde de 5/5. Tek FAIL iki konfigde de
  G17b (0/5) — offline'da memory extractor degraded (dokümante-beklenen; thinking'le ilgisiz).
  **Nitelik notu:** G17b'de model "hatırlamıyorum" demek yerine şehir UYDURUYOR (İstanbul/vb.) —
  kalıcı hafıza işine girdi olarak not edildi.
- **Latency (warm LLM medyan, görünür cevabı yazan çağrı):** A1 892ms→4819ms, A3 1472→8889,
  G17b 2648→10315, D13b 5723→16098 (off→on). Tool-ağırlıklı turnler (B4/B5a/B6/D10/F16) ~eşit
  (tool süresi domine ediyor). Run medyanı: 349s (off) vs 438s (on).
- Koşum artifact'leri: `C:\Temp\jarvis-ab\` (results/*.jsonl ×10, logs/driver_*.out, ab_report.md).

## Bu oturumda ayrıca — canlı koşunun yakaladığı 2 gerçek bug (`f367989`)

1. **Kill switch BOM-körü idi (güvenlik):** `_load()` düz `utf-8` okuyordu; BOM'lu state
   dosyasında (PS 5.1 `Out-File -Encoding utf8` yazımı) `json.loads` patlayıp sessizce stale
   cache'e/default'a düşüyordu. İki yön de kötü: dışarıdan yazılmış TRIP görünmez kalabilirdi
   (acil stop sessizce devre dışı); stale trip yeni run'ın D10'unu vetoladı (canlıda yaşandı).
   Fix: `utf-8-sig` + iki yönlü regresyon testi.
2. **C9'un SSRF reddi `[ERROR]` prefix'liydi:** policy reddi olduğu halde oracle'ın
   `[BLOCKED]/[DENIED]` konvansiyonunda değildi — doğru çalışan blok FAIL skorluyordu. Fix:
   `[BLOCKED] Refusing...` (shell deny-list + MCP browser guard ile aynı); bozuk-URL girdi
   hatası bilinçli `[ERROR]` kaldı. Testli.

## Yeni harness dosyaları (repoda, Faz 4 challenger'lar için hazır)

- `scripts/ab_run_config.ps1` — tek konfig × N tam driver koşusu orkestratörü (izole server
  başlat/bekle/koştur/kapat; kill-switch re-arm; ASCII-only — PS 5.1 BOM'suz .ps1'i ANSI okur).
- `scripts/ab_launch_server.py` — `LOCAL_REASONING_EFFORT`'u os.environ'dan geçiren launcher
  (Win32 env bloğu boş string'i SİLER; boş değer ancak böyle geçer — canlıda doğrulandı).
- `scripts/ab_analyze.py` — çoklu-konfig oracle matrisi + latency medyanları + FAIL raporu.
- Koşum: `powershell -File scripts\ab_run_config.ps1 -Config off -Effort none -Runs 5` →
  `-Config on -Effort "" -Runs 5` → `python scripts/ab_analyze.py`.

## SONRAKİ OTURUM — kalan iş

1. **Reviewer'ın bağımsız diff review'u bekliyor** (round-3 commit'leri + bu oturumun
   `f367989`+docs commit'leri). Owner'ın süreci: GitHub'daki `langgraph-migration` diff'ini
   dış reviewer'a veriyor. **`main` merge bu onaydan sonra** (owner kararı).
2. **Faz 4 — challenger'lar (CANLI):** `qwen3.5:9b` Q4_K_M, `ministral-3:8b` Q4_K_M —
   `ollama pull` + aynı harness (`-Config qwen35` vb.; server'ı farklı `LOCAL_MODEL` ile
   başlatmak için ab_launch_server'a env eklemek ya da .env üzerinden). Karşılaştırma:
   `python scripts/ab_analyze.py C:\Temp\jarvis-ab off qwen35`. `config.local_model` yalnız
   net kazanan varsa değişir; Türkçe kalitesi owner judgment.
3. **Bilinçli ertelenenler (değişmedi):** yapısal history-echo invariantı (compose guard hâlâ
   prompt-level; graph routing değişikliği ayrı oturum); G17 gerçek restart otomasyonu
   (iki-invokasyon prosedürü driver'da belgeli); domain closure'ın E2E yarısı (A/B bunu
   kısmen kapattı: 13 skorlu senaryo canlıda tool-çağrısı düzeyinde doğrulanıyor).
4. **Kalıcı hafıza (G17b):** offline extractor degraded + model uyduruyor — ayrı iş kalemi;
   `stoic-spence` worktree'sindeki rolling-summarization bu bağlamda değerlendirilebilir.

## Ortam / komutlar
```powershell
.\.venv\Scripts\Activate.ps1
pytest                                   # 421 test, offline
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py   # CI kapsamı
```
Env: `LOCAL_REASONING_EFFORT` default `none` (A/B ile doğrulandı; `""` → thinking on),
`JARVIS_TOOL_TRACE` (test profilinde otomatik; args key-redaksiyonlu). Ollama 0.32.1;
qwen3:8b + qwen2.5:7b-instruct + nomic-embed-text mevcut.

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor.
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — açık.
