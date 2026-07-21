# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-20 (6. oturum) — ŞAMPİYON BASELINE KOŞULDU + ANALİZ EDİLDİ, AGENT RUNTIME rev.2 FAZ 1 KODU GİTTİ (henüz commit/push edilmedi)

**Bağlam:** Önceki oturum (5.) Faz 0'ı gönderip iki iş bırakmıştı: (1) ertelenen 5×13 A/B
baseline koşusu (Faz 0'ın son kalemi + `a6a3426`'nın "provisional" dediği model-seçim kararının
re-baseline'ı), (2) sonra Agent Runtime rev.2 Faz 1. Bu oturum ikisini de sırayla yaptı.

## Bu oturumda yapılanlar

1. **Şampiyon A/B baseline koşuldu ve analiz edildi** (`qwen3:8b`, `LOCAL_REASONING_EFFORT=none`,
   5×13, port 8132, arka planda ~34 dakika sürdü — `.\scripts\ab_run_config.ps1 -Config champ
   -Effort none -Runs 5`). Sonuç: **62/65 (95.4%)** — iki-metrikli oracle'da compliance 62/65,
   semantic 63/65. Güvenlik senaryoları (C9/D11/D12/D13b) **hepsi 5/5**, G17b (geçmişte hep
   fail eden kişisel-veri senaryosu) artık **5/5 temiz**. Tek gerçek bulgu kümesi: **B6 3/5**
   (plot_data — "response claims success but no tool succeeded", tam olarak bu planın var
   olma sebebi olan B6 hallucination deseni, canlı ve hâlâ oluyor) ve **F16 4/5** (procedure_save,
   1 run'da `trace tools=none`). Rapor: `C:\Temp\jarvis-ab\results\ab_report.md`.
   **Sonuç: model-seçim kararı artık PROVISIONAL değil — CONFIRMED.** 62/65, eski 2026-07-18
   baseline'ının 60/65'inden daha iyi (o da iki config'te de yalnız G17b'den kaybediyordu, bu da
   artık düzelmiş). B6/F16 bir model sorunu değil — Faz 2-4'ün TaskContract + verified-composition
   işinin tam olarak çözmeyi hedeflediği mimari sınıf; bu koşu o işe canlı, güncel bir regresyon
   referansı sağladı.
   **Not:** `ab_analyze.py`'yi ilk çağırışım Git Bash'te ters eğik çizgi yutulması yüzünden sahte
   ikinci bir "baseline" config'i üretti (gerçek `champ` verisi etkilenmedi) — PowerShell'den doğru
   tekrarlandı. Bu script'i **PowerShell'den çalıştırın, Bash'ten değil** (Windows path'leri).
2. **Agent Runtime rev.2 Faz 1 koda geçti** — yeni paket `jarvis/execution/`:
   - `contract.py` — `TaskContract`/`ExpectedOutcome` (henüz hiçbir extractor yok, yalnız şekil)
   - `postcondition.py` — `PostconditionSpec`/`PostconditionResult`/`VerificationStatus`
   - `envelope.py` — `ExecutionEnvelope` + `build_shadow_envelope()`
   - `redaction.py` — paylaşılan redaksiyon katmanı: key-based (eski) + **pattern-based (yeni)**
     — `agent.redact_tool_args`'ın "plain-string taranmıyor" olarak belgelenmiş açığını kapatıyor
   - `ToolSpec` (`tool_registry.py`) additive 6 yeni alan: `args_schema`, `postconditions`,
     `idempotency`, `effect_scope`, `contract_status` (Faz 0'ın `_ALPHA_STATUS`'u artık buraya da
     katlanıyor — `get_alpha_status()` değişmedi), `timeout_class`. Hiçbiri henüz canlı davranış
     değiştirmiyor (Faz 3/6/7 hedefleri).
   - `Settings.execution_contract_mode` (`off|shadow|enforce_read_only|enforce_reversible
     |enforce_all`, varsayılan **off**). Faz 1 yalnız off/not-off ayrımını uyguluyor.
   - `JarvisState.execution_envelopes` yeni alan.
   - `tool_result_accounting` (Faz 1'in shadow ledger'ı) — mode≠"off" iken her tool call için bir
     `ExecutionEnvelope` üretip state'e ekliyor, **hiçbir kararı değiştirmiyor**. mode="off" veya
     `settings=None` iken bu blok hiç çalışmıyor (unit testle kanıtlı: `test_no_settings_produces_
     no_envelopes_key`, `test_off_and_shadow_agree_on_every_pre_existing_field`).
   - **HANDOFF'un işaret ettiği 3 ham-redaksiyon açığı kapatıldı** (hepsi `agent.py`): `on_tool_
     start`'ın `audit_log` `args_preview`'i (:139 idi), `_record_execution_end`'in `result_
     preview`'i (:195 idi), ve ayrıca kodu okurken bulunan **dördüncü bir açık**: `_trace_end`'in
     `tool_trace.record`'a yazdığı `content_head` de ham'dı (kendi modül docstring'i "file
     contents... must never persist through it" diyordu, tutmuyordu). `tool_accounting.py`'nin
     `tool_execution_ledger.content_head`'i de düzeltildi (checkpointer SQLite'ına ham gidiyordu).
   - `graph.py`: `make_tool_result_accounting_node()` artık `settings` alıyor.
3. **43 yeni test** (`test_execution_types.py`, `test_execution_redaction.py`,
   `test_execution_shadow_ledger.py`, + `test_tool_trace.py`/`test_audit_outcome.py`'ye ekler).
   **523 pytest yeşil (480+43), ruff temiz.**
4. **Küçük canlı shadow-mode kontrolü** (tam 5×13 değil — bkz. aşağıdaki "kalan iş"): port 8133'te
   tek senaryo (`-Scenarios B6`, sonra `B4`) `EXECUTION_CONTRACT_MODE=shadow` ile koşuldu, sunucu
   çökmeden tamamlandı. **Beklenmedik gözlem:** her iki izole tekli-senaryo koşusunda da model
   "Gemini 2.5 Pro (cloud)" etiketiyle cevapladı ve `trace tools=none` — hiç tool çağrılmadı.
   Kod-yolu analizi bunun Faz 1 değişikliğiyle **ilgisi olamayacağını** gösteriyor
   (`tool_result_accounting`, `agent_node`'un hiç üretmediği bir tool call'ı işleyemez — üstteki
   koddaki döngüye hiç girilmiyor). Şampiyon baseline'ın TAM 13-senaryo sırasındaki sonuçlarıyla
   çelişmiyor de (B6 orada 3/5 gerçek plot_data çağrısıyla başarısız oluyor, F16'nın 1/5'i de
   `trace tools=none`) — yani "tool çağrılmadı" nadiren ama gerçekten oluyor; izole tekli-senaryo
   koşusunun HER SEFERİNDE bunu tetiklemesi muhtemelen CLAUDE.md/ROADMAP'te zaten bilinen açık
   "pre-first-turn kozmetik model label" sınıfının bir uzantısı (izole koşu her zaman "session'ın
   ilk turu" koşuluna düşüyor). **Faz 1 kodunu şüpheli görmüyorum ama kesin kanıt değil** —
   sıradaki oturumun tam shadow 5×13'ü bunu da netleştirecek.
5. **Commit/push YAPILMADI bu oturumda** — owner onayı bekleniyor (bkz. sıradaki iş #1).

## SONRAKİ OTURUM — kalan iş

1. **Commit + push onayı iste** — çalışan ağaçta: `jarvis/execution/` (yeni), `jarvis/agent.py`,
   `jarvis/config.py`, `jarvis/graph/graph.py`, `jarvis/graph/state.py`,
   `jarvis/graph/tool_accounting.py`, `jarvis/tool_registry.py`, 5 test dosyası (3 yeni + 2 ek).
   `.claude/settings.local.json`'daki değişiklik muhtemelen commit'e dahil edilmemeli (lokal araç
   izinleri) — kontrol et.
2. **Faz 1'in kendi kabul kriteri hâlâ eksik: tam shadow-mode 5×13 koşusu.**
   Bu oturum yalnız 2 izole tekli-senaryo denedi (yukarıya bkz.) — gerçek kabul şu:
   ```powershell
   $env:EXECUTION_CONTRACT_MODE = "shadow"
   .\scripts\ab_run_config.ps1 -Config champ-shadow -Effort none -Runs 5
   python scripts\ab_analyze.py C:\Temp\jarvis-ab champ champ-shadow --runs 5
   ```
   Beklenen: oracle skorları `champ`'a (62/65) **bit-identical**; envelope üretim oranı ölçülecek
   bir yol yok henüz raporda (`ab_analyze.py` envelope'ları okumuyor — bunu ölçmenin en basit yolu
   `home-champ-shadow\data\jarvis_checkpoints.db`'yi elle örneklemek, ya da Faz 1'in kabul testini
   düşürüp yalnız "skorlar bit-identical + sunucu hiç çökmedi" ile yetinmek; owner'a sor).
3. **Model-seçim kararı artık CONFIRMED — MEMORY.md/config.py'deki "provisional" notları
   güncellenebilir** (bu oturumda `project_agent_runtime_rev2` hafıza dosyası zaten güncellendi).
4. **Ardından Faz 2** — `prepare_execution` node (`agent → prepare_execution → confirmation`
   routing değişikliği), approval binding (HMAC), idempotency journal. Plan dosyası:
   `C:\Users\mertk\.claude\plans\c-users-mertk-desktop-gpt-analysis-md-s-delegated-scone.md`
   §Faz 2.
5. **Dış reviewer'ın önceki paketi hâlâ inceleniyor olabilir** — `docs/review/2026-07-premerge-
     summary.md` üzerinden, owner süreci, bu oturumda dokunulmadı.
6. **Bilinçli ertelenenler (değişmedi):**
   - W4b (veto-turn'de critic LLM atlaması) — graph routing değişikliği ayrı oturum gerektiriyor.
   - `[BLOCKED]` sunum katmanından kod soyma — ayrı iyileştirme.
   - qwen3.5/ministral-3'ün thinking-on kolu koşulmadı (owner kararıyla OFF-only kapsam).
7. **Kalıcı hafıza (G17b'nin davranışsal yarısı):** `stoic-spence` worktree'sindeki rolling-
   summarization hâlâ ayrı bir iş kalemi.
8. **Flagged, ayrı oturum bekliyor** (spawn_task ile bu oturumda işaretlendi):
   `docs/ARCHITECTURE.md`'nin orchestrator bölümü birkaç faz geride (Faz 2B topology, Faz 4
   confirmation gate, Faz 3 model swap hiç yansımamış) — task_f540cef1.

## Ortam / komutlar
```powershell
.\.venv\Scripts\Activate.ps1
pytest                                   # 523 test, offline
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py
```
Env: `LOCAL_REASONING_EFFORT` default `none`, `LOCAL_MODEL` default `qwen3:8b` — **artık
CONFIRMED, bkz. üstteki bölüm**. `EXECUTION_CONTRACT_MODE` default `off` (yeni, Faz 1).
Koşum artifact'leri: `C:\Temp\jarvis-ab\` (`ab_analyze.py`'yi **PowerShell'den** çalıştırın —
Git Bash Windows path'lerindeki ters eğik çizgileri yutuyor, canlı bulundu bu oturumda).

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — açık (bu oturumun izole-senaryo gözlemiyle muhtemelen
  ilişkili, bkz. yukarı).
