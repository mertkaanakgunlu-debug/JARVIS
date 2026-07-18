# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-18 (2. oturum) — round-3 ölçüm düzeltmeleri; A/B artık güvenilir

**Bağlam:** Owner, son push'un üçüncü tur dış review'unu verdi. Üç P0 iddiası da kodda
doğrulandı ve düzeltildi; A/B koşusunu yanıltacak ölçüm hatası kalmadı. **3 commit,
`langgraph-migration`. 417/417 pytest yeşil (405→417), ruff temiz (harness scriptleri dahil).**
`7a980d8` yapısal blok kanıtı + D13b · `6755c66` turn_summary compose · `b1a6959` hardening.

## Bu oturumda ne yapıldı

1. **P0 — D13b oracle beklentisi TERSTİ** (`scripts/manual_test_driver.py`): eski beklenti
   "kill-switch OFF → runs" diyerek `enabled=False`'u "özellik kapalı" okumuştu. Kill-switch
   semantiği tam tersi: `enabled=False` = trip edilmiş acil stop (`disable()` == trip) → D13b'de
   L3 `shell_run` VETO edilmeli. Eski haliyle çalışan kill switch FAIL, bozuk olan PASS skorluyordu.
   Artık `outcome=BLOCKED` + `expected_tool="shell_run"`.
2. **P0 — pre-execution bloklar yapısal kanıt bırakıyor** (`jarvis/graph/nodes.py`):
   confirmation_node'un kill-switch vetosu ve external-write bloku tool callback'lerine hiç
   düşmediği için trace'e görünmüyordu; oracle response-regex'e düşüyordu ("devre dışı" YAZAN
   model, hiç tool çağırmadan D12'yi geçebiliyordu). İki blok noktası artık
   `event="policy_decision"` + `outcome="blocked_*"` trace satırı yazıyor;
   `eval_oracle._blocked_signal` YALNIZ yapısal kanıta bakıyor (policy satırı veya
   `[BLOCKED]/[DENIED]` execution satırı), response-text fallback silindi; `expected_tool`
   blok kanıtını hedef tool'a scope'luyor. İzole seam-check ile uçtan uca doğrulandı
   (gerçek node → gerçek trace dosyası → driver'ın gerçek EXPECTED girdileri; kanıtsız tur FAIL).
3. **P0/P1 — `turn_summary()` yanlış çağrıyı seçiyordu** (`jarvis/llm_trace.py`): "agent"
   tercihli seçim, tool turn'lerinde `/status`'un latency/TTFT/cold-start/model etiketini
   görünür cevabı yazan **compose** çağrısı yerine tool-SELECTION çağrısına bağlıyordu —
   thinking A/B'nin en çok ölçmesi gereken senaryolarda. Artık compose > agent > any;
   tool'suz sohbet turn'leri (compose çağrısı yok) değişmedi.
4. **Hardening:** tool_trace args preview'u key-redaksiyonlu (password/token/api_key/secret/
   body/content/... → `<redacted>`; sonuç `content_head` bilerek dokunulmadı — oracle'ın
   `[BLOCKED]`/`[ERROR]` tespiti ona bakıyor; `audit_log.args_preview` da bilerek tam — L2+,
   forensik amaç). `plot_data` inline limitleri: 256 KB / 10k satır / 100 kolon / düz
   primitifler (nested reddi). CI ruff kapsamına `scripts/eval_oracle.py` +
   `scripts/manual_test_driver.py` eklendi (ters D13b tam da lint/test görmeyen beklenti
   tablosunda saklanmıştı). `.env.example`'a `LOCAL_REASONING_EFFORT` rollback bloğu.
5. **G17 dürüst etiketlendi:** --all içinde cross-session recall testi (yalnız /reset;
   server restart YOK). Gerçek restart testi driver'da zaten mümkün, iki ayrı invokasyonla:
   `manual_test_driver.py G17a` → server'ı kapat → aynı `JARVIS_TEST_HOME` ile aç →
   `manual_test_driver.py G17b`.

## SONRAKİ OTURUM — kalan iş

1. **Faz 3.3 — thinking on/off A/B (EN ÖNCELİKLİ, CANLI).** Ölçüm engelleri kalktı. Beklenti
   değişikliklerine dikkat: D13b artık BLOCKED bekliyor; D12/D13b policy_decision satırıyla
   kanıtlanıyor; latency/TTFT compose'dan geliyor. Koşum (öncekiyle aynı):
   ```powershell
   ollama serve            # ayrı pencere (zaten çalışıyor olabilir)
   $env:JARVIS_TEST_HOME = "C:\...\stable-test-home"
   $env:JARVIS_TEST_RESULTS = "C:\...\ab_off.jsonl"
   python -m jarvis --api --profile test --port 8132     # terminal 1
   python scripts/manual_test_driver.py --all             # terminal 2 → ORACLE x/y özeti
   # thinking-ON turu: server'ı LOCAL_REASONING_EFFORT="" ile başlat, ab_on.jsonl'e yaz
   ```
   16 senaryo × 5'er tur × 2 konfig. Doğruluk düşerse tek env var ile rollback.
2. **Faz 4 — challenger'lar** (A/B bittikten sonra): `qwen3.5:9b` Q4_K_M, `ministral-3:8b`
   Q4_K_M — aynı oracle harness'la. `config.local_model` yalnız kazanan varsa değişir.
3. **Bilinçli ertelenenler (review'un orta maddeleri):**
   - **Yapısal history-echo invariantı** — compose guard'ı hâlâ prompt seviyesinde (talimata
     uymayan model teknik olarak eski cevabı üretebilir). Review'un önerisi: tool-routed
     turn'de ne başarılı tool ne yapılandırılmış block/clarify kararı varsa compose'a
     gidilmesin (agent'a dön veya deterministik "işlem yapılmadı" cevabı). Graph routing
     değişikliği — kendi oturumunu hak ediyor.
   - **G17 gerçek restart otomasyonu** — prosedür belgelendi (yukarıda), driver server
     process'ini yönetmiyor; owner isterse iki-invokasyon manuel akış yeterli.
   - **Domain closure E2E yarısı** — 13 domain "membership closure" (doğru domain + tool
     görünürlüğü) testli; tool'un gerçekten çağrıldığı/argümanların doğruluğu/çok-adımlı
     tamamlanma canlı A/B'nin işi. Raporlarken "membership closure geçti" de, "closure" değil.
4. **`main` merge YAPMA** — A/B doğrulaması bitmeden değil (owner kararı).

## Ortam / komutlar
```powershell
.\.venv\Scripts\Activate.ps1
pytest                                   # 417 test, offline
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py   # CI ile aynı kapsam
```
Env vars: `JARVIS_TOOL_TRACE` (test profilinde otomatik 1; artık args key-redaksiyonlu),
`LOCAL_REASONING_EFFORT` (default `none`; `""` → thinking on; artık `.env.example`'da).
Ollama 0.32.1, qwen3:8b (thinking cap'li) + qwen2.5:7b mevcut.

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor.
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — açık.
