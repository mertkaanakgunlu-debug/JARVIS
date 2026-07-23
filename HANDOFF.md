# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-23 (15. oturum) — FAZ 7.3'ÜN KENDİSİNİN BAĞIMSIZ REVIEW'I: 15 YENİ BULGU, HEPSİ DÜZELTİLDİ, TEST EDİLDİ, COMMIT'LENDİ VE PUSH'LANDI

**Durum tek cümlede:** Owner `/code-review ultra` ile Faz 7.3'ün kendi diff'ine (7 düzeltme commit'i
+ 444a88c) karşı bağımsız bir max-effort local review istedi (cloud versiyonu terminal gerektiriyor);
review 15 yeni bulgu çıkardı (9 correctness, 5 efficiency, 1 docs) — owner "fix problems" dedi,
hepsi bu oturumda düzeltildi, test edildi, **commit'lendi VE push'landı** (owner'ın açık isteğiyle:
"commit push yap eğer fix işlemi tamamsa"). `langgraph-migration` şu an `origin` ile byte-equal,
`b61ca21`'de.

**En ciddi bulgu:** Faz 7.3 workflow-engine'in onay decision parser'ını tam
`approve`/`deny`/`deny:<reason>` allowlist'ine düzeltmişti ("yes/typo/boş hepsi approve" açığını
kapatarak) — ama AYNI düzeltmeyi eski, çok daha sık kullanılan chat-turn onay kapısına
(`jarvis/graph/nodes.py`'nin `make_confirmation_node`'u — `POST /chat/confirm`'ün doğrulanmamış
`decision: str` alanının GERÇEKTEN sürdüğü kapı) hiç uygulamamıştı. Aynı şekilde düzeltildi: geçersiz
bir decision artık açık bir deny'e normalize ediliyor, approve'a düşmüyor.

**Diğer bulgular:** Faz 7.3'ün kendi yeni streaming-interrupt-fallback marker'ı
(`__jarvis_confirm__`, aynı turdaki İKİNCİ bir interrupt için) DÖRT tüketicisinden ÜÇÜ tarafından hiç
algılanmıyordu — `/chat/confirm`'ün SSE endpoint'i, CLI'nin onay prompt'u, ve paylaşılan voice resume
helper'ı hepsi bunu ham metin/ses olarak sızdırıyordu; `resume_and_stream()`'in `chat_stream()`'in
defans-in-depth olarak tuttuğu `except GraphInterrupt` handler'ı hiç yoktu; `/workflow approve|deny`
"onay beklemiyor" durumunu hem CLI'de hem API'de sahte bir başarı gibi gösteriyordu; engine'in todo-
compensation delete'i ve kill-switch/stale-approval blokları Faz 7.3'ün kendi iddia ettiği audit/
tool_trace tamlığını atlıyordu; `reset_conversation_async`'in event_bus bildirimi lock serbest
bırakıldıktan SONRA `self.session_id`'yi tekrar okuyordu (TOCTOU). Artı efficiency düzeltmeleri (sync
dosya/SQLite I/O event loop'u bloklamıyor artık, `make_tools()` her approval'da yeniden inşa
edilmiyor, `_pending_confirmations` artık TTL ile temizleniyor) ve bir docs düzeltmesi (HANDOFF'un
kendi "7 push'lanmamış commit" iddiası bir eksikti — bunu kaydeden 444a88c'in kendisi sayılmayan 8.
commit'ti).

**Süreç notu:** review sırasında 5/10 paralel finder subagent'ı session usage limit'e takıldı;
owner'ın açık isteğiyle ("krediler çok hızlı tükeniyor") kalan analiz doğrudan Read/Grep ile, düzeltme
ve testler doğrudan tool call'larla yapıldı, ek subagent spawn edilmedi (bkz.
[[feedback-checkpoint-expensive-reviews]]). 4 yeni test dosyası + 5 mevcut dosyaya ekleme, 191 test
yeşil (dokunulan her alanda), ruff temiz.

## Bu oturumda yapılan 3 commit (hepsi `langgraph-migration`'da, PUSH'LANDI)

1. **`e83a7b4` — fix(graph): confirmation-resume gate exact-decision fix.**
   `jarvis/graph/nodes.py`'nin `make_confirmation_node`'u aynı "yes/typo/boş approve" açığını
   taşıyordu, Faz 7.3'ün workflow-engine için kapattığı — ama bu, `/chat/confirm`'ün gerçekten
   sürdüğü, çok daha sık kullanılan kapı. Geçersiz decision artık açık bir "deny:<reason>"a
   normalize edilip mevcut deny koluna düşüyor (yeni kod tekrarı yok). 7 yeni parametrized test
   (`test_prepare_execution_node.py`).
2. **`31a63ac` — docs: HANDOFF.md'nin push'lanmamış commit sayısı düzeltmesi (7 → 8).**
3. **`b61ca21` — fix(agent,api,cli,workflow): confirmation resume, workflow audit completeness,
   event-loop blocking I/O.** Tek commit'te toplanan, birbirine dosya-örtüşmesiyle bağlı 8 ayrı
   düzeltme (agent.py/api.py/cli.py/workflow_engine.py hunk-splitting olmadan ayrılamıyordu):
   - `__jarvis_confirm__` marker leak — `resume_and_stream()`'in ikinci bir interrupt'ta yeniden
     yaydığı marker, `/chat/confirm` (API), CLI onay prompt'u, ve `voice/session.py`'nin paylaşılan
     resume helper'ı tarafından ham metin/ses olarak sızdırılıyordu (4 endpoint'in 3'ü çekmiyordu,
     yalnız `chat_stream()`'in 4 SSE dalı çekiyordu). Hepsi düzeltildi; `api.py`'de tek paylaşılan
     `_sse_frames()` wrapper'ı 5 endpoint'in tamamına taşındı.
   - `resume_and_stream()`'e `chat_stream()`'in tuttuğu `except GraphInterrupt` defans-in-depth
     handler'ı eklendi (hiç yoktu).
   - `/workflow approve|deny` CLI + API: "onay beklemiyor" durumu artık gerçekten hata olarak
     raporlanıyor (eskiden sahte başarı paneli/200 dönüyordu).
   - Todo-compensation delete artık `_dispatch()`'in kendi execution_start/end audit çiftini
     alıyor (eskiden atlıyordu); engine'in kill-switch/capability-disabled/external-writes/stale-
     approval blokları artık `nodes.py`'nin eşleniği gibi `tool_trace` "policy_decision" satırı da
     yazıyor (eskiden yalnız `audit_log`).
   - `reset_conversation_async`'in event_bus bildirim kararı artık `_reset_state_sync`'in kendi
     lock'u İÇİNDE atomik olarak belirleniyor (eskiden lock serbest bırakıldıktan sonra
     `self.session_id`'yi tekrar okuyordu — TOCTOU).
   - Efficiency: `WorkflowEngine._audit()` artık `asyncio.to_thread` ile offload ediyor (sync
     dosya I/O event loop'u bloklamıyordu); yeni `/workflow` GET/GET/POST endpoint'lerinin
     `workflow_store` çağrıları da offload edildi; `JarvisAgent.get_workflow_tools()` tool
     listesini agent ömrü boyunca cache'liyor (her approval'da `make_tools()` yeniden
     inşa edilmiyor); `_pending_confirmations` artık TTL ile (2x `approval_ttl_sec`) temizleniyor.
   - 4 yeni test dosyası + 4 mevcut dosyaya ekleme.

**Not:** bu oturum bir canlı-sunucu E2E turu DEĞİLDİ (önceki 14. oturumun kapsamı) — statik kod
review + düzeltme + test. 191 test yeşil (dokunulan her alanda), ruff temiz. Full-suite tek seferlik
çalıştırmada chromadb-tabanlı 4 ilgisiz dosyada (`test_shadow_replay_equivalence.py`,
`test_procedure_store.py`, `test_shell_workspace.py`, `test_todo_bg_analysis.py`) flakiness
gözlemlendi — `git stash` ile bu oturumun değişikliklerinden BAĞIMSIZ olduğu, izole/küçük grup
çalıştırmalarında güvenilir şekilde geçtiği doğrulandı, dokunulmadı.

## Ortam / komutlar — bu oturum sonunda
```powershell
git log --oneline -8
#  b61ca21 fix(agent,api,cli,workflow): confirmation resume, workflow audit completeness, ...   <- HEAD
#  31a63ac docs: correct HANDOFF.md's unpushed commit count (7 -> 8)
#  e83a7b4 fix(graph): confirmation-resume gate must reject non-exact decision strings
#  444a88c docs: record Faz 7.3 completion (review remediation + live E2E findings)   <- bu oturumun başlangıcı
#  fa34225 fix(agent): chat_stream()/resume_and_stream() must detect an interrupt ...
#  f45a763 fix(workflow): report step table, exact approval allowlist, replan invariant (medium)
#  eecb173 fix(api): /reset takes conversation_id ...
#  ae3a41c fix(workflow): a failed compensation attempt is no longer reported as applied (P1)
git rev-list --left-right --count origin/langgraph-migration...HEAD   # 0  0 (PUSH'LANDI)
python -m pytest -q       # tam suite'te 21 chromadb-flakiness başarısızlığı (bkz. not),
                          # dokunulan her alanda 191/191 yeşil (bkz. aşağıdaki test dosyası listesi)
ruff check jarvis/ tests/    # All checks passed!
git status --short        # yalnız .claude/settings.local.json (oturum öncesinden, ilgisiz)
```
**Push'landı ve doğrulandı bu oturumda** — `git push origin langgraph-migration` başarıyla
`9ddf8fb..b61ca21` fast-forward yaptı, `langgraph-migration` şu an `origin` ile byte-equal (0/0).
CI bu oturumda izlenmedi (owner'dan ayrı bir "CI'ı izle" isteği gelmedi) — bir sonraki oturum
`gh run list --branch langgraph-migration` ile kontrol edebilir.

## SONRAKİ OTURUM — kalan iş

1. **Faz 8 (Evaluation v2 + manuel alpha kapısı)** artık GERÇEKTEN önü açık — Faz 7.3'ün kendi 7
   maddesi VE bu oturumun bulduğu 15 ek bulgu de artık tamamlandı, test edildi, commit'lendi ve
   push'landı. Push kararı bekleyen bir şey yok.
2. **Model tool-calling güvenilirliği** (14. oturumda canlı gözlemlendi, düzeltilmedi): yerel modelin
   bazen tool çağırmadan başarı uydurması ayrı bir inceleme/düzeltme gerektirebilir — muhtemelen
   sistem promptu/tool-seçim talimatları tarafı, workflow safety kernel'inin değil.
3. `workflow_start`'ın JSON `steps` güvenilirliği hâlâ ölçülmedi (değişmedi, 13. oturumdan).
4. Gerçek CLI REPL E2E testi hâlâ yok (bilinçli, değişmedi).
5. Diğer eski kalan işler (değişmedi): Faz 6 Kısım 3 Literal-terfi, Faz 5 kalan işleri
   (`run_manifest.json` prompt hash/registry version, artifact tool run-scoping), canlı A/B'nin B6
   sorusu, conversation_id client-side adoption, 4 worktree branch, mobile flutter-analyze info/warning,
   chromadb-tabanlı test flakiness'i (bkz. yukarıdaki not) — istenirse ayrıca araştırılabilir.

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice (+ workflow onayı artık API'den
  de mümkün, madde 2 — ama Electron/mobil UI'ı hâlâ bunu render etmiyor).
- Pre-first-turn kozmetik model label — değişmedi.
