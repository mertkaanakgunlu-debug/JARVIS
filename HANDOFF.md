# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-22 (14. oturum) — FAZ 7.3 (PRE-FAZ-8 REVIEW REMEDIATION): TÜM 7 MADDE UYGULANDI, TEST EDİLDİ, GERÇEK BİR SUNUCUYA KARŞI CANLI DOĞRULANDI; CANLI DOĞRULAMA SIRASINDA YENİ, GERÇEK BİR P1 BULUNUP AYNI OTURUMDA DÜZELTİLDİ; COMMIT'LENDİ, HENÜZ PUSH'LANMADI

**Durum tek cümlede:** Owner Faz 8'e geçmeden önce bağımsız bir kod review'ı (Claude web/başka bir
oturum) getirdi — 1 kritik (P0), 4 yüksek (P1), 3 orta önem düzeyinde bulgu, Faz 7'nin workflow
runtime'ında. Ben önce her bulguyu canlı koddan tek tek doğruladım (hepsi doğru çıktı), sonra
owner'ın "review'ın tam 7 adımı" seçimiyle **hepsini bu oturumda uyguladım** — 7 commit, 986 pytest
yeşil (927 → 986, +59), ruff temiz — **ve owner'ın 7. maddesinde istediği gerçek E2E doğrulamasını
gerçekten yaptım**: izole bir `python -m jarvis --api --profile test` sunucusunu gerçek yerel Ollama
modeline karşı çalıştırıp restart/crash/approval/reset senaryolarını canlı HTTP ile sürdüm. Bu canlı
geçiş sırasında **review'da olmayan, gerçek ve ciddi yeni bir P1 bulundu ve aynı oturumda
düzeltildi**: `/chat/stream` ve `resume_and_stream()` bu LangGraph sürümünde `GraphInterrupt`'ı hiç
raise etmiyor (aynı `ainvoke()`'un Faz 3'te bulunup düzeltilmiş açığı — ama streaming tarafı hiç
düzeltilmemiş, hatta koddaki yorum "streaming path raise ediyor" diye yanlış iddia ediyordu) — sonuç:
onay gerektiren bir eylem (email gönder, takvim sil, shell çalıştır) `/chat/stream` üzerinden
istendiğinde **sessizce hiçbir şey dönmüyordu** (ne prompt, ne hata) — bu da voice (tüm voice loop'lar
`chat_stream()`/`resume_and_stream()` üzerine kurulu) ve Electron HUD dahil tüm streaming yüzeyini
etkiliyordu. Düzeltildi ve düzeltme canlı sunucuya karşı 3/3 tekrar doğrulandı.

## Bu oturumda yapılan 7 commit (hepsi `langgraph-migration`'da, push'lanmadı)

1. **`0cdaa2b` — P0: crash recovery artık idempotent-olmayan adımları asla otomatik tekrar
   çalıştırmıyor.** Önceki davranış: `_dispatch()` `idempotency.commit()`'i tool başarılı olduktan
   SONRA çağırıyordu; process bu ikisi arasında çökerse (`gmail send` gerçekten gitmiş ama journal'a
   yazılmamış), recovery "running + journal yok" durumunu körü körüne "pending"e çeviriyor, bir
   sonraki `advance()` aynı e-postayı tekrar gönderiyordu. Düzeltme: yeni terminal durum
   `unknown_outcome` (dependents skip edilir, plan `partially_committed`, rapor "check manually"
   der) — yalnız `ToolSpec.idempotency == "natural"` olan capability'ler eski "pending'e resetle"
   davranışını korur. `tool_registry.py`'de 38 tool'un gerçek `idempotency` sınıflandırması
   (`_IDEMPOTENCY` dict, `_TOOL_DOMAINS` ile aynı "tek yer, import-time kontrollü" şekli).
   Review'ın istediği tam fault-injection testi: sahte gmail tool GERÇEKTEN "başarılı" dönüyor,
   `idempotency.commit` exception fırlatıyor (gerçek crash penceresi), recovery `workflow_store`'un
   gerçekten diske yazdığı şeye karşı çalışıyor — sıfır yeniden çalıştırma doğrulanıyor.
   **Kendi bulduğum bir nüans:** P0 gerçek ama şu an canlıda tetiklenebilir değildi — `advance()`'i
   çağıran tek iki yer (`workflow_start`, her zaman yeni plan; CLI'nin `/workflow approve`, yalnız
   `paused_for_approval`) bir çökmüş-ama-onay-beklemeyen planı asla yeniden `advance()` edemiyordu
   (`/workflow resume` diye bir şey yoktu) — yani düzeltme öncesi gerçek sonuç "workflow kalıcı
   takılı kalır", "sessizce iki kez çalışır" değildi. Aynı kök neden, aynı gerekli düzeltme.
2. **`66b942a` — P1: transport-agnostic approval servisi + structured SSE confirmation.** Yeni
   `jarvis/execution/workflow_approval.py` — CLI ve API'nin ikisinin de geçtiği TEK insan-only onay
   katmanı (yine agent-callable bir tool DEĞİL). Exact decision allowlist (yalnız
   `approve`/`deny`/`deny:<reason>` — "yes"/""/"invalid" artık hata). `WorkflowEngine.resolve_approval`:
   restart sonrası bozulan imza (approval.py'nin process-local HMAC key'i) artık workflow'u
   öldürmüyor — adım baştan `_run_step`'e gönderilip taze imzalı yeni bir istek üretiliyor, plan
   yine `paused_for_approval`'a dönüyor, insan tekrar sorulan bir soruyla karşılaşıyor (execute
   ETMİYOR eski "yes" ile). API: `GET /workflow`, `GET /workflow/{id}`, `POST /workflow/{id}/resolve`.
   `/chat/stream` + `/chat/upload`'ın 3 SSE dalı: `chat_stream()`'in içsel `__jarvis_confirm__`
   marker'ı artık ham metin gibi geçmiyor, `{"type":"confirmation_required","id","payload"}` olarak
   yayınlanıyor.
3. **`f8d85ef` — P1: workflow adımları artık audit log'a yazıyor.** `WorkflowEngine` artık
   `tool.ainvoke()`'u audit'siz çağırmıyor — `transport`/`conversation_id` alıyor, `nodes.py`'nin
   `_HudEventCallback`'iyle AYNI event vocabulary'sini yazıyor (`decision`
   auto_approved/confirm_required/user_approved/user_denied/blocked_*, `execution_start`/
   `execution_end`, yeni `compensation` event'i). `agent.py`'nin 4 graph-config noktası artık
   `transport`/`conversation_id`'yi `configurable`'da taşıyor; `workflow_start` bunu injected
   `RunnableConfig`'ten okuyor (model-facing şemaya asla sızmıyor — testle sabitlendi).
4. **`ae3a41c` — P1: başarısız compensation artık "compensated" sayılmıyor.** Yeni
   `CompensationResult(ok, note, retryable)` — iki compensator (`file_write`, `todo add`) artık
   yapısal sonuç dönüyor. Yeni terminal durum `compensation_failed`, `compensated`'dan ayrı — rapor
   artık gerçekten ne olduğunu söylüyor ("⚠ COMPENSATION FAILED -- check manually", "Compensation
   applied" ile ASLA karışmıyor), ve `compensate()` ikinci çağrıda `compensation_failed` adımları
   YENİDEN dener (eskiden `status != succeeded` olduğu için asla denenemiyordu).
5. **`eecb173` — P1: `/reset` artık `conversation_id` alıyor, başka client'ın konuşmasını
   arşivleyemiyor.** Review'ın tam senaryosu: Client B aktifken Client A `/reset` çağırırsa eskiden
   B'nin konuşması arşivleniyordu. `JarvisAgent.reset_conversation_async(conversation_id)`: hedef
   şu an aktif olan session DEĞİLSE, doğrudan ID ile arşivler, `self.session_id`/`_history`/`_turn`'e
   HİÇ dokunmadan — hangi konuşma o an aktifse tamamen bozulmadan kalıyor.
6. **`f45a763` — Orta: rapor step tablosu + exact allowlist (engine seviyesinde de, defense-in-
   depth) + replan invariant.** `render_workflow_report()` artık HER adım için satır üretiyor
   (`step_id | capability | status | error | execution_id | compensation`) — validation/policy-veto
   nedeniyle dispatch'e hiç ulaşmamış adımlar artık görünür (eskiden sadece "Skipped (blocked by...)"
   deniyordu, blocker'ın KENDİ nedeni hiç görünmüyordu). `replan()` artık `new_steps`'in KENDİ İÇİNDEKİ
   duplicate ID'leri de yakalıyor (eskiden yalnız mevcut plan'a karşı kontrol ediyordu).
7. **`fa34225` — P1 (canlı E2E sırasında bulundu): `chat_stream()`/`resume_and_stream()` stream'in
   hiç raise etmediği bir interrupt'ı tespit edemiyordu.** Yukarıda özetlendi. Yeni
   `JarvisAgent._pending_interrupt_payload(config)` — stream sonlandığında `graph.aget_state(config)
   .interrupts`'a doğrudan bakıyor (aynı `chat()`'in `ainvoke()` için zaten yaptığı
   `result["__interrupt__"]` kontrolünün streaming eşleniği). Hem `chat_stream()` hem
   `resume_and_stream()`'e (aynı turdaki İKİNCİ bir interrupt için) eklendi.

## Canlı E2E doğrulaması (owner'ın 7. maddesi) — ne yapıldı, ne bulundu

İzole `JARVIS_TEST_HOME` ile gerçek `python -m jarvis --api --profile test` (CLOUD_POLICY=off →
gerçek yerel Ollama, `EXTERNAL_WRITES_ENABLED=false` → gerçek dış yan etki riski sıfır) süreç
başlatılıp gerçek HTTP ile sürüldü, gerçek proje `data/`'sına hiç dokunulmadı (`git status` oturum
sonunda temiz).

- ✅ **Crash+restart+re-approval, tam canlı:** `shell_run` içeren bir workflow gerçek onay
  beklemesine sokuldu, süreç öldürüldü (`kill`), AYNI `JARVIS_HOME`'a karşı yeni bir süreç
  başlatıldı, `POST /workflow/{id}/resolve` ile ilk deneme `signature_mismatch` → `reapproval_required:
  true` (execute ETMEDİ), ikinci deneme (taze imzayla) → gerçekten çalıştı (`shell_run` çıktısı
  `hello-from-e2e` olarak audit log'da ve raporda göründü). `audit_log.jsonl`'da tam
  decision→execution_start→execution_end zinciri doğru transport etiketleriyle doğrulandı.
- ✅ **Per-conversation reset, tam canlı:** conv-x "42" öğrendi, conv-y "7" öğrendi (aktif olan),
  yalnız conv-x reset edildi, conv-y'ye tekrar soruldu — hâlâ doğru "7" cevabını verdi (bozulmadı).
- ✅ **Structured SSE confirmation, düzeltmeden ÖNCE ve SONRA canlı karşılaştırıldı:** aynı prompt
  `/chat`'te 5/5 doğru `confirmation_required` döndü, `/chat/stream`'de düzeltmeden önce 5/5 boş
  döndü (yalnız `[DONE]`) — bu paired karşılaştırma yukarıdaki 7. bulguyu ortaya çıkardı. Düzeltme
  sonrası aynı sunucuya karşı 3/3 doğru structured frame + `/chat/confirm` ile tam round-trip
  (gerçek shell komutu çalıştı, sonuç stream'e düzgün geri geldi).
- **Yeni, ayrı bir gözlem (düzeltilmedi — bu oturumun kapsamı dışı, ayrı bir konu):** yerel model
  (`qwen3:8b`, bu makinede varsayılan) bir "run this command" tarzı isteğe bazen tool hiç
  çağırmadan "komut çalıştırıldı, çıktı: X" diye DÜZ METİNLE UYDURUYOR (2/2 gözlemde) — tam da bu
  girişimin (Agent Runtime rev.2) başlangıç motivasyonu olan "fabricated success claims" sınıfından
  canlı bir örnek. `workflow_start`'ı tetiklemek için de model 2/2 denemede bunun yerine doğrudan
  `file_write`/`file_read` kullanmayı tercih etti (görev basit olduğu için makul bir tercih, ama
  `workflow_start`'ın JSON güvenilirliği hâlâ ölçülemedi — HANDOFF'un önceki notu hâlâ geçerli).
  Bu, review'ın kapsamındaki bir "workflow safety kernel" bulgusu değil, ayrı bir model/prompt
  güvenilirlik sorusu — owner'a bilgi olarak not düşülüyor, bu oturumda müdahale edilmedi.
- **Bilinçli yapılmayan (owner'a aktarılan, mevcut kod tabanının kendi emsaliyle tutarlı bir kapsam
  kararı):** gerçek CLI REPL'ini (`/workflow` komutu) programatik sürecek bir test altyapısı
  kurulmadı — bu repo'nun zaten belgelenmiş, bilinçli bir kapsam dışı kararı ("bu orana yeni bir
  test altyapısı kurmak orantısız görüldü"), bu oturum da aynı disiplini korudu. Bunun yerine API
  yüzeyi (voice/Electron/mobile'ın da gerçekte kullandığı yüzey) çok daha kapsamlı canlı test edildi.

## Ortam / komutlar — bu oturum sonunda
```powershell
git log --oneline -8
#  fa34225 fix(agent): chat_stream()/resume_and_stream() must detect an interrupt ...   <- HEAD
#  f45a763 fix(workflow): report step table, exact approval allowlist, replan invariant (medium)
#  eecb173 fix(api): /reset takes conversation_id ...
#  ae3a41c fix(workflow): a failed compensation attempt is no longer reported as applied (P1)
#  f8d85ef feat(workflow): workflow steps now write the same audit trail as the graph path (P1)
#  66b942a feat(workflow): transport-agnostic human approval + structured SSE confirmation (P1)
#  0cdaa2b fix(workflow): never auto-retry a non-idempotent step after a crash (P0)
#  9ddf8fb docs: record CI fully green after both regression fixes   <- bu oturumun başlangıcı
git rev-list --left-right --count origin/langgraph-migration...HEAD   # 0  7 (push edilmedi)
python -m pytest -q       # 986 passed (927 + 59 yeni), ~200s
ruff check jarvis/ tests/ scripts/    # All checks passed!
git diff --check          # temiz
git status --short        # yalnız .claude/settings.local.json (oturum öncesinden) — gerçek data/ hiç dokunulmadı
```
**CI'da doğrulanmadı** (henüz push edilmedi) — yalnız yerel `pytest`/`ruff`. Push, owner'ın ayrı
kararı.

**Not (review remediation ile bulundu, 2026-07-23):** yukarıdaki `git rev-list` çıktısı bu commit
(444a88c, bu HANDOFF metnini kaydeden dokümantasyon commit'i) `HEAD` olmadan ÖNCE, `fa34225`
`HEAD` iken alınmış bir anlık görüntü — o an doğruydu, ama 444a88c'in kendisi de push'lanmamış
8. commit olduğu için artık `origin/langgraph-migration...HEAD` gerçekte `0  8` döndürüyor. Bu,
anlık görüntüyü kaydeden commit'in kendisinin o anlık görüntüyü bir commit geride bırakması —
"SONRAKİ OTURUM" bölümündeki sayı (aşağıda) güncel, bu bloktaki dondurulmuş komut çıktısı değil.

## SONRAKİ OTURUM — kalan iş

1. **Push kararı bekliyor** — 8 commit `langgraph-migration`'da, `origin`'e hiç gönderilmedi (7 düzeltme
   commit'i + bu HANDOFF'u kaydeden 444a88c dokümantasyon commit'i — bkz. yukarıdaki not).
2. **Faz 8 (Evaluation v2 + manuel alpha kapısı)** artık gerçekten önü açık — review'ın kendi
   sözleriyle "Bu maddeler tamamlanmadan Faz 8 ölçümleri yanıltıcı olur" — 7 madde de artık
   tamamlandı ve canlı doğrulandı.
3. **Model tool-calling güvenilirliği** (bu oturumda canlı gözlemlendi, düzeltilmedi): yerel modelin
   bazen tool çağırmadan başarı uydurması ayrı bir inceleme/düzeltme gerektirebilir — muhtemelen
   sistem promptu/tool-seçim talimatları tarafı, workflow safety kernel'inin değil.
4. `workflow_start`'ın JSON `steps` güvenilirliği hâlâ ölçülmedi (değişmedi, 13. oturumdan).
5. Gerçek CLI REPL E2E testi hâlâ yok (bilinçli, değişmedi).
6. Diğer eski kalan işler (değişmedi): Faz 6 Kısım 3 Literal-terfi, Faz 5 kalan işleri
   (`run_manifest.json` prompt hash/registry version, artifact tool run-scoping), canlı A/B'nin B6
   sorusu, conversation_id client-side adoption, 4 worktree branch, mobile flutter-analyze info/warning.

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice (+ workflow onayı artık API'den
  de mümkün, madde 2 — ama Electron/mobil UI'ı hâlâ bunu render etmiyor).
- Pre-first-turn kozmetik model label — değişmedi.
