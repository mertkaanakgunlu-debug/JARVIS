# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-19 (4. oturum) — REVIEWER'IN 8 MADDESİ UYGULANDI + MODEL-SELECTION BİTTİ

**Bağlam:** Dış reviewer, 3. oturumun A/B raporunu (thinking-off default) kabul etti ve
merge-öncesi 8 maddelik iş listesi verdi (kill-switch failure-mode testleri, G17b
hallucination guard, `[BLOCKED]` string bağımlılığını azaltma, latency raporuna p95+token,
qwen3.5:9b + ministral-3:8b challenger koşuları, tek karşılaştırmalı rapor). Bu oturum
listenin tamamını inline/sıralı uyguladı — 7 commit, her faz sonrası tam `pytest`+`ruff`.
**Sonuç: iki challenger de eşiği geçemedi, `qwen3:8b` + `LOCAL_REASONING_EFFORT=none`
varsayılan kalıyor.** 453 pytest, ruff temiz. Commit'ler **henüz push edilmedi**.

## Bu oturumda yapılanlar (özet — tam gerekçe `CHANGELOG.md`'de)

1. **Kill-switch fail-safe** (`1ac5641`) — state dosyası VAR ama okunamıyorsa artık
   FAIL-CLOSED (sentetik trip, cache'lenmez) + critical log + audit event. Eksik dosya
   davranışı değişmedi. 12 yeni failure-mode testi.
2. **G17b uydurma-yasağı** (`f1c6dac`) — `context_builder` degraded'de açık "unavailable"
   markeri, `05_memory_policy.md`'ye kalıcı kural, oracle'a `required_any`/`forbidden_response`.
   G17b artık şampiyonda 5/5 (öncesi dokümante-beklenen 0/5).
3. **`[BLOCKED:reason_code]`** (`ff24946`) — tool-seviyesi bloklar artık makine-okur kod
   taşıyor (`ssrf_private_address` vb.); oracle yapısal alana öncelik veriyor.
4. **Model-selection metrikleri** (`c179028`) — `/status` token/çağrı alanları,
   `ab_analyze.py`'de pooled p50/p90/p95/min/max + champion-baseline karar matrisi.
5. **Challenger plumbing** (`f49eff7`) — `ab_launch_server.py`/`ab_run_config.ps1`'e
   `LOCAL_MODEL` pass-through.
6. **Canlı yakalanan 2 bug, düzeltildi:**
   - `54575d0` — `jarvis/tools/files.py`'nin mutlak-yol sınırı `JARVIS_HOME`'u görmüyordu;
     smoke koşusunda gerçek OneDrive Desktop'a dosya sızdı (temizlendi, `_effective_home()`
     ile düzeltildi, 4 yeni test).
   - `9f7d720` — D12 driver prompt'u belirsizdi (gövde yok), model bazı çalıştırmalarda
     aracı hiç çağırmadan soru soruyor, güvenlik bloğuna hiç uğramıyordu. Prompt netleştirildi,
     re-baseline tekrar koşuldu: 65/65.

## Model-selection sonucu (16×5, OFF-only, owner kararı — tam tablo CHANGELOG'da)

| Model | Toplam | Güvenlik | Karar |
|---|---|---|---|
| **qwen3:8b (şampiyon)** | **65/65** | 5/5 hepsi | **Varsayılan kalıyor** |
| qwen3.5:9b | 55/65 | 5/5 hepsi | KALDI (B5b/B6 sabit FAIL, 2 timeout, ~2x yavaş) |
| ministral-3:8b | 45/65 | **D11 0/5** | KALDI (araçları büyük ölçüde çağırmıyor) |

Owner'a ayrıca sade/görsel bir özet sunuldu (Artifact — bu conversation'a özel, tekrar
istenirse yeniden üretilebilir).

## Reviewer paketi

`docs/review/2026-07-premerge-summary.md` — 7 commit'in tam haritası, reviewer'ın 8
maddesinin her birine karşılık ne yapıldığı, model-selection tablosu, kalan işler. Owner bunu
dış reviewer'a iletecek.

## SONRAKİ OTURUM — kalan iş

1. **Bu 7 commit push edilmeli** (owner onayıyla — henüz push edilmedi). Push sonrası
   GitHub Actions'ın `python` job'ının (ruff+453 pytest) yeşil olduğu doğrulanmalı
   (reviewer'ın 2. maddesi).
2. **Dış reviewer'ın bu paket üzerinden incelemesi** — merge kararı o onaydan sonra (owner
   süreci, değişmedi).
3. **Bilinçli ertelenenler:**
   - W4b (veto-turn'de critic LLM atlaması) — critic'in revise döngüsü çıktı-etkileyen,
     graph routing değişikliği ayrı oturum gerektiriyor.
   - `[BLOCKED]` sunum katmanından kod soyma — compose LLM şu an kodu transkriptte görüyor,
     kabul edilebilir, ayrı iyileştirme.
   - qwen3.5/ministral-3'ün thinking-on kolu koşulmadı (owner kararıyla OFF-only kapsam).
4. **Kalıcı hafıza (G17b'nin davranışsal yarısı):** deterministik katman + oracle sözleşmesi
   bu oturumda kapatıldı; `stoic-spence` worktree'sindeki rolling-summarization hâlâ ayrı bir
   iş kalemi olarak değerlendirilebilir.

## Ortam / komutlar
```powershell
.\.venv\Scripts\Activate.ps1
pytest                                   # 453 test, offline
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py
```
Env: `LOCAL_REASONING_EFFORT` default `none`, `LOCAL_MODEL` default `qwen3:8b` (her ikisi de
bu oturumda doğrulandı). Ollama'da artık `qwen3.5:9b` (Q4_K_M) ve `ministral-3:8b` (Q4_K_M) da
mevcut (indirilmiş, tekrar koşulabilir). Koşum artifact'leri: `C:\Temp\jarvis-ab\` (champ/
qwen35/ministral3 için results/*.jsonl ×5'er, logs/, ab_report.md).

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor.
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — açık.
