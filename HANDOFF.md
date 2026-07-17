# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-17 — Patch 1.2 + Sprint 2 + model A/B (kabiliyet regresyonu)

**Bağlam:** Owner "eskiden takvime ekleme gibi işleri yapıyordu, şimdi yapamıyor — sorun modelde
mi başka yerde mi?" diye sordu + ChatGPT-5.6'nın ikinci review'unu (`GPT_Analysis.md`) verdi.
**Teşhis (kodda doğrulandı):** kabiliyet kaybı kod çürümesi değil, motor değişimi — "takvim"
çalışırken non-trivial turn'ler cloud Gemini'deydi; `CLOUD_POLICY=off` (maliyet kararı) sonrası her
şey qwen2.5:7b'ye düştü, o da ~34 araç altında tool-call üretemiyor. Ama tek sorun bu değildi:
review + kod doğrulaması 8 modelden-bağımsız gerçek bug çıkardı. Onaylanan plan
`.claude/plans/...witty-wirth.md` — inline, sıralı, faz-sonu commit'ler ($0 kalır kararı, cloud
köprüsü yok).

## Bu oturumda ne yapıldı (hepsi commit + push edildi)

**5 commit, `langgraph-migration` → origin. 324/324 pytest yeşil, ruff temiz.** Sırayla:

1. **`6c9fa0d` `feat: complete stabilization patch 1.1`** — önceki oturumun working tree'si (Patch
   1.1, 23 dosya) nihayet commit edildi (review "kaybolmadan koru" dedi).
2. **`4b3a8cc` `test: add manual E2E test driver`** — `scripts/manual_test_driver.py` (path'ler
   env ile parametreli: `JARVIS_TEST_BASE_URL`/`_HOME`/`_RESULTS`; `--all` bayrağı).
3. **`2092441` `fix: add deterministic tool runtime safety (Patch 1.2)`** — 4 alt-faz:
   - **1A** `imap-tools` bağımlılığı + `SafeToolNode` (`jarvis/graph/safe_tools.py`): tool
     exception → sanitize `[TOOL_ERROR]` ToolMessage, graph ölmüyor.
   - **1B** deterministik limitler (batch4/turn6/round2/identical1, `nodes.py` confirmation başı) +
     `tool_execution_ledger`/`tool_result_accounting` node (`jarvis/graph/tool_accounting.py`) +
     seen/completed fingerprint ayrımı + ledger-bazlı recursion cevabı (opak 500 yerine).
   - **1C** `ProcedureStore` idempotency: content fingerprint + güvenli migration + `add_or_get()`.
   - **1D** turn compaction (`agent.py`: history'ye yalnız gerçek human + nihai AI + tek satır özet)
     + turn-bazlı `_trim_history` (max 10). A2→A3 regresyonu düzeldi.
4. **`f05de2c` `feat: add capability-scoped tool routing (Sprint 2)`**:
   - **2A** `jarvis/graph/tool_router.py`: deterministik `\b`-sınırlı TR+EN sınıflandırıcı →
     `ToolRoute`; `ToolSpec.domain` + 13-domain haritası; MCP karantina; `_is_trivially_simple`
     emekli. `make_agent_node` turn-scoped subset bind ediyor (route yoksa full set).
   - **2B** bare `compose` node + `post_tool_router` (F16 döngüsü yapısal kapandı) + ephemeral
     critic (fake HumanMessage kaldırıldı).
5. **`c1e1467` `feat: qwen3:8b local model + Faz 3 live-found fixes`**:
   - A/B sonucu: **default `qwen2.5:7b-instruct` → `qwen3:8b`** + local `temperature=0`.
   - Canlı bulunan 3 gerçek bug (aşağıda "Live fixes").

## Faz 3 A/B kararı — neden qwen3:8b

Aynı 16-senaryo suite, `temp=0`, aynı scoped subset, `--profile test`, ground-truth = audit +
dosya sistemi:
- **qwen2.5:7b — SIFIR gerçek tool çağrısı.** "dosyayı oluşturdum / maili gönderdim" hepsi
  halüsinasyon metni; audit boş, diskte `jarvis_test.txt` yok. Başarısızlıktan beter: gate'ler
  devreye bile girmedi.
- **qwen3:8b — gerçek çağrılar.** `file_write` GERÇEKTEN yazdı, `shell_run` dir GERÇEKTEN çalıştı;
  external-write gate / shell deny-list / SSRF / killswitch İLK KEZ uçtan uca gerçek çağrılarla
  doğrulandı. 8 GB RTX 4070 Laptop VRAM'e sığıyor. Daha yavaş (turn 15-40s) ama doğruluk çok yüksek.

## Live fixes (A/B sırasında bulundu, kalıcı, testli)

1. **langgraph 1.2.x non-streaming `ainvoke()` dinamik interrupt'i RAISE etmiyor** —
   `result["__interrupt__"]`'te döndürüyor. `/chat`'in `except GraphInterrupt`'i hiç tetiklenmiyordu,
   onay payload'u sessizce düşüyordu (Faz 4'ten beri latent; yalnız streaming CLI/voice canlı
   test edilmişti). chat/proactive/background üçünde de düzeltildi.
2. **Boş-cevap fallback'i tüm mesaj listesini tarıyordu** → önceki turn'ün cevabını yankılıyordu
   ("echo"). Artık yalnız bu turn'ün mesajları.
3. **`graph_stream_to_text` yalnız "agent" node'unu stream ediyordu** → Sprint 2 sonrası onaylanan
   `shell_run` boş stream dönüyordu. "compose" da stream ediliyor.

Testler: `tests/test_interrupt_surface.py` (3).

## Kabul turu (16 senaryo, qwen3:8b default)

**HTTP 500 = 0 · raw-JSON/pseudo final = 0 · uydurma tool adı = 0 · aynı tool+args tekrarı = 0
(F16 tek draft) · izinsiz dış yan etki = 0 · A2→A3 recall = GEÇER · gerçek tool-call ≈ %100 ·
doğru domain 15/15 · maliyet $0.00.** E14/E15 kimlik-yok artık düzgün hata mesajı (500 değil).
Killswitch izole doğrulandı (temiz session, off → `blocked_kill_switch` + model doğru bildirim).

## Bilinen sınırlar / sonraki adımlar

1. **YENİ bulgu — history echo:** aynı tool-isteği önceki turn'de geçmişte varsa, model tool
   çağırmadan önceki turn'ün cevabını yankılayabiliyor (D13b canlıda killswitch testini geçersiz
   kıldı — killswitch'in kendisi izole testte sağlam). Turn compaction özet-satırının yan etkisi.
   Muhtemel çözüm: tool-turn'lerinde nihai cevabı da history'de kısaltmak, veya tekrarlanan
   isteklerde compose'a "geçmişi kopyalama" talimatı. Sonraki iterasyona bırakıldı.
2. **Pre-first-turn model label** hâlâ `cloud_model_label` ("Gemini 2.5 Pro (cloud)") gösteriyor
   `CLOUD_POLICY=off`'ta bile — kozmetik, ilk turn'den sonra trace-driven label düzeltiyor
   (önceki handoff'tan taşındı, hâlâ açık).
3. **qwen3:8b latency:** turn başına 15-40s. Kabul edilebilir ama voice UX için gözden geçirilebilir
   (num_ctx/quantization ayarı, veya kısa turn'ler için qwen2.5'e düşürme — ama o tool-calling
   yapamıyor, dikkat).
4. **Sprint 3** (8 direct-Gemini modülün shared gateway'e migrasyonu) — hâlâ kapsam dışı, değişmedi.
5. **4 worktree branch** read-through — hâlâ carried over.
6. Wake-word/HUD uçtan uca kabul turu — görev icra katmanı artık güvenilir olduğuna göre yapılabilir.

## Environment checklist to resume work

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt          # imap-tools artık listede
ollama pull qwen3:8b                      # yeni default local model (~5.2GB)
pytest                                    # 324 test, ~57s, offline
ruff check jarvis/ tests/                 # clean
python -m jarvis --api --profile test --port 8132   # izole smoke
# kabul turu: JARVIS_TEST_HOME=... JARVIS_TEST_RESULTS=... python scripts/manual_test_driver.py --all
```

Yeni env vars (hepsi opsiyonel, `.env.example`'da): `MAX_TOOL_CALLS_PER_AI_MESSAGE`/`_PER_TURN`,
`MAX_IDENTICAL_TOOL_CALL`, `MAX_TOOL_ROUNDS_PER_TURN`, `MAX_CONVERSATION_TURNS`. `LOCAL_MODEL`
default artık `qwen3:8b`; `LOCAL_TEMPERATURE` default `0.0`.
