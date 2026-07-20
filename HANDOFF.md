# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-20 (5. oturum) — GPT_Analysis.md İNCELENDİ, "AGENT RUNTIME rev.2" PLANI ONAYLANDI, FAZ 0 KODU GİTTİ

**Bağlam:** Owner, önceki oturumdan sonra bir dış geliştirici GPT'ye gitti; o oturumun bulgularını
(context leakage, B6 yanlış tool argümanı, uydurma başarı iddiası, run-to-run değişkenlik) ortak
mimari kök nedenle çözecek bir plan istedi — yeni domain tool'u değil, **herhangi bir tool'u
JARVIS'in güvenilir kullanmasını sağlayacak runtime sözleşmesi**. Repo kod okunarak incelendi
(rev.1), sonra owner'ın GPT'ye götürdüğü rev.1 dış reviewer 14 maddelik revizyon verdi (özellikle:
TaskContract eksikliği, confirmation'ın normalize-edilmemiş ham argümanı onaylaması, idempotency'nin
P0 olmaması gerektiği halde Faz E'ye atılmış olması, `python_run`'ın "bilinen açık" diye
bırakılamayacağı, Workflow Runtime fazının hiç yazılmamış olması). Tüm 14 madde işlenip **rev.2**
plan onaylandı.

**Plan dosyası:** `C:\Users\mertk\.claude\plans\c-users-mertk-desktop-gpt-analysis-md-s-delegated-scone.md`
— 9 faz (0-8). **Bu numaralandırma [ROADMAP.md](ROADMAP.md)'nin kendi Faz 0-8'iyle (local-first
pivot, hepsi done/deferred) KARIŞTIRILMAMALI** — ayrı bir inisiyatif, kod içi yorumlarda hep
"Agent Runtime rev.2, Faz N" diye açık nitelenir.

## Bu oturumda yapılanlar

1. **Repo durumu yeniden doğrulandı** (GPT'nin "commitleri otomatik doğru kabul etme" kuralı):
   `pytest` **480** test topluyor artık (Faz 0'ın 12 yenisiyle; önceki HANDOFF'un "453"ü zaten
   bayattı, gerçek baseline 468'di). `origin/langgraph-migration`'a göre fark **4 commit**'ti,
   önceki HANDOFF'un "7" rakamı da bayatmış (o 7'nin çoğu zaten origin'deydi).
2. **Faz 0 kod:** `jarvis/tool_registry.py`'a alpha-capability allowlist —
   `ALPHA_STATUS_VALUES` (5 kapalı değer: contract_enforced/shadow_validated/quarantined/
   disabled/explicitly_unverifiable) + `get_alpha_status()`. İki canlı karar: `python_run` →
   **disabled** (`make_tools()`'un listesinden çıkarıldı VE `policy_guard.evaluate()`'te
   bağımsız veto — iki ayrı savunma katmanı), `shell_run` → **quarantined**. `PolicyDecision`'a
   `veto_kind` alanı (confirmation_node'un ack mesajı artık gerçek nedeni söylüyor). 12 yeni test.
   **480 pytest yeşil, ruff temiz.**
3. **Commit + push** (owner onayıyla, bu oturumda): 4 bekleyen commit + Faz 0'ın commit'i,
   toplam 5, `origin/langgraph-migration`'a gitti (`792329e`). CI (`ci.yml`) push sonrası
   commit `792329e` için tetiklendi, tamamlanması bu oturumda beklenmedi.
4. **CHANGELOG.md geriye dönük dolduruldu** — 4 pushed-ama-hiç-yazılmamış commit'in içeriği
   ("Merge-öncesi review sertleştirmesi" bölümüne eklendi: agent.py'nin env_block context-leak'i,
   iki-metrikli oracle, `-Scenarios` param + splatting fix'i) + bugünün Faz 0 girdisi eklendi.

## Önemli, taşınan bir bulgu: model-selection kararı hâlâ PROVISIONAL

`a6a3426` (bu oturumda push edilen, ama önceki oturumda yazılmış commit) şunu not ediyor:
**`qwen3:8b` + `LOCAL_REASONING_EFFORT=none` varsayılan kararı, iki-metrikli oracle'la bir
re-baseline koşulana kadar geçicidir** — eski 65/65, tool-execution compliance'tı, semantic
correctness değil (B6 yanlış veriyi çizip geçmişti). İki-metrikli oracle (`b8463dc`) o
tarihten sonra geldi ama re-baseline hiç koşulmadı. **Sonraki oturumun 5×13 A/B koşusu bu
re-baseline'ı da kapatacak** — sadece Faz 0'ın bookkeeping'i değil.

## SONRAKİ OTURUM — kalan iş

1. **5×13 A/B baseline koşusu — owner bu oturumda ertelemeyi seçti.** Faz 0'ın son kalemi
   (Agent Runtime rev.2'nin sonraki fazlarını karşılaştıracağı referans) VE yukarıdaki
   provisional model-selection kararının re-baseline'ı. Ollama'nın açık olduğunu önce doğrula
   (oturumlar arası açık kalmıyor, MEMORY.md). Komut:
   ```powershell
   .\scripts\ab_run_config.ps1 -Config champ -Runs 5
   python scripts\ab_analyze.py C:\Temp\jarvis-ab champ --runs 5
   ```
2. **Baseline'dan sonra Agent Runtime rev.2'nin Faz 1'i:** `jarvis/execution/` paketi —
   `TaskContract`, `PostconditionSpec`, `ExecutionEnvelope`, ortak redaksiyon katmanı. Bu
   oturumda bulunan ek açık, Faz 1'in kapsamına zaten dahil: `agent.py`'nin `audit_log.record`
   çağrıları (`:139` `args_preview`, `:195` `result_preview`) ham argüman yazıyor —
   `tool_trace`'in `redact_tool_args()`'ı (`:97-105`) audit log'a hiç uygulanmıyor; bir
   `gmail send` gövdesi trace'te maskeli, audit log'da açık. `tool_execution_ledger.content_head`
   ([tool_accounting.py:156](jarvis/graph/tool_accounting.py)) de aynı durumda, checkpointer
   SQLite'ına gidiyor.
3. **Dış reviewer'ın önceki paketi hâlâ inceleniyor olabilir** — `docs/review/2026-07-premerge-
   summary.md` üzerinden, owner süreci, bu oturumda dokunulmadı.
4. **Bilinçli ertelenenler (önceki oturumdan, değişmedi):**
   - W4b (veto-turn'de critic LLM atlaması) — graph routing değişikliği ayrı oturum gerektiriyor.
   - `[BLOCKED]` sunum katmanından kod soyma — ayrı iyileştirme.
   - qwen3.5/ministral-3'ün thinking-on kolu koşulmadı (owner kararıyla OFF-only kapsam).
5. **Kalıcı hafıza (G17b'nin davranışsal yarısı):** `stoic-spence` worktree'sindeki rolling-
   summarization hâlâ ayrı bir iş kalemi.

## Ortam / komutlar
```powershell
.\.venv\Scripts\Activate.ps1
pytest                                   # 480 test, offline
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py
```
Env: `LOCAL_REASONING_EFFORT` default `none`, `LOCAL_MODEL` default `qwen3:8b` — **karar
provisional, bkz. üstteki bölüm**. Koşum artifact'leri: `C:\Temp\jarvis-ab\`.

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — açık.
