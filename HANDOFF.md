# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-22 (13. oturum) — AGENT RUNTIME REV.2 FAZ 7, KISIM 1 (WORKFLOW RUNTIME) YAPILDI, HENÜZ COMMIT'LENMEDİ

**Durum tek cümlede:** Bu oturum önce 11. oturumun bıraktığı commit kararını uyguladı (owner "önerilen
3 parçalı yapıyla commit et" dedi — `d6ce968`/`a2a1bb3`/`a87ebe9` commit'lendi, henüz push'lanmadı),
sonra owner "sıradaki faz ile devam et" dedi ve **Agent Runtime rev.2'nin Faz 7'si (Workflow
Runtime)** ele alındı — plan dosyası okunarak (`C:\Users\mertk\.claude\plans\c-users-mertk-desktop-
gpt-analysis-md-s-delegated-scone.md`) tasarlandı, inşa edildi, test edildi. Faz 6 Part 1→2'nin
kendi emsaliyle tutarlı olarak **"Kısım 1: mekanizma, Kısım 2: canlıya bağlama"** ayrımına gidildi —
bu oturum yalnızca Kısım 1'i (bağımsız, doğrudan çağrılabilir workflow motoru) kapsıyor, hiçbir canlı
tetikleyici eklenmedi. **914 pytest yeşil (868+46), ruff temiz, `git diff --check` temiz — Faz 7
kod/test/docs değişiklikleri henüz commit'lenmedi** (owner'ın kararını bekliyor, aşağıdaki
"SONRAKİ OTURUM" listesinin ilk maddesi).

## Bu oturumda yapılanlar

### 1. 11. oturumun commit kararı uygulandı

Owner'a HANDOFF'un bıraktığı 3 parçalı yapı soruldu (`AskUserQuestion`), "önerilen yapıyla commit
et" seçildi. Commit'lemeden önce 868 testin/ruff'ın hâlâ gerçekten yeşil/temiz olduğu yeniden
doğrulandı (11. oturumun kendi raporuna körü körüne güvenmek yerine). Üç commit landed:
- `d6ce968` — feat(api): conversation_id özelliği
- `a2a1bb3` — fix(scripts): ilgisiz auth_setup.py ruff düzeltmesi
- `a87ebe9` — docs: HANDOFF/ROADMAP/MEMORY'yi gerçek commit SHA'larına göre senkronize etti

**Henüz push'lanmadı** — bu üçü hâlâ `origin/langgraph-migration`'ın ilerisinde.

### 2. Agent Runtime rev.2, Faz 7 Kısım 1 — Workflow Runtime (`jarvis/execution/workflow*.py`)

Owner'a kaç faz kaldığı soruldu — plan dosyası okunarak Faz 7 (Workflow Runtime) ve Faz 8
(Evaluation v2 + alpha kapısı) olmak üzere 2 faz kaldığı, artı Faz 6'nın canlı trafik verisi
bekleyen bir alt maddesi (Literal-terfi) olduğu netleştirildi. Owner "sıradaki faz" (Faz 7) ile
devam edilmesini istedi.

**Yeni dosyalar:**
- `jarvis/execution/workflow.py` — `WorkflowStep`/`WorkflowPlan` (saf tipler + I/O'suz bağımlılık-
  hazırlık/hata-yayılım/terminal-durum mantığı)
- `jarvis/execution/workflow_store.py` — SQLite checkpoint/resume deposu (`idempotency.py`'nin
  her-çağrıda-taze-bağlantı desenini taklit ediyor)
- `jarvis/execution/workflow_engine.py` — `WorkflowEngine`: adımları sırayla yürütür, Faz 1-4'ün
  execution contract'ını (schema validation, policy_guard, HMAC onay bağlama, ExecutionEnvelope,
  postcondition runner, idempotency journal) ikinci bir doğrulama sözlüğü icat etmeden yeniden
  kullanır. Tek-turlu chat graph'ından kasıtlı olarak ayrı — kendi LangGraph node'u yok.

**Önemli tasarım noktaları:**
- **Onay duraklatma**: `confirmation_node`'un HMAC bağlamasının birebir aynısı ama LangGraph
  interrupt'ı olmadan (burada compile edilmiş bir graph yok) — `plan.status="paused_for_approval"`
  set edilip persist ediliyor; `resolve_approval(plan, step_id, "approve"|"deny:sebep")` imza/digest/
  süre doğrulamasını tekrar yapıp öyle dispatch ediyor.
- **Checkpoint/resume**: her adım geçişi `workflows.db`'ye yazılıyor. Yüklemede "running" durumunda
  bulunan bir adım = süreç dispatch ortasında çökmüş demek — `idempotency.is_committed()` (tahmin
  değil) "succeeded" (çökmeden önce commit olmuş, envelope dürüstçe yok) mu yoksa "pending"e
  sıfırlanıp güvenle yeniden mi denenecek karar veriyor.
- **Hata yayılımı**: `WorkflowPlan.propagate_skip()` bir hatayı/reddi tüm transitif bağımlılara
  yayıyor. **Yazarken gerçek bir hata bulundu**: adım bütçesi sayacı (`executed_count()`) başta
  pending-olmayan HER durumu sayıyordu — yani yayılan (asla dispatch edilmemiş) bir "skipped" adım
  da bütçeyi tüketiyordu, bu da bağımsız bir dalın haksız yere bütçeden mahrum kalmasına yol
  açabilirdi. Düzeltildi (yalnız gerçekten dispatch edilen durumlar sayılıyor) ve
  `test_a_propagated_skip_does_not_consume_the_step_budget` ile kilitlendi.
- **Telafi (compensation)**: planın kendi ifadesiyle dar kapsamlı — "yalnız kayıtlı gerçek tersi
  olan işlemlerde otomatik telafi." Sadece 2 gerçek telafi kayıtlı: `file_write` (önceki içeriği
  geri yükle, ya da yeni oluşturulmuş dosyayı sil — capture, üzerine yazmadan ÖNCE yapılıyor) ve
  `todo`'nun `"add"` action'ı (oluşturulan görevi sil, id'si kendi sonuç metninden regex ile
  çıkarılıyor — `postcondition_runner.py`'nin exit-code kontrolüyle aynı desen). Kayıtlı telafisi
  olmayan her capability, başarılı adımı dürüstçe telafi edilmemiş bırakılıyor, asla sessizce geri
  alındığı iddia edilmiyor. Plan "failed"/"partially_committed" olarak sonuçlandığında (adım bütçesi
  tükenmesi dahil, yalnız sert hatalarda değil) otomatik tetikleniyor. `ToolSpec.effect_scope` ilk
  gerçek sınıflandırmasını aldı: `file_write` → `"reversible"`.
- **Workflow seviyesinde son doğrulama**: Faz 4'ün `VerifiedExecutionSummary`/
  `render_operation_status_for_user`'ı workflow'un kendi toplanan adım envelope'ları üzerinde
  yeniden kullanılıyor (`WorkflowEngine.report()`), ikinci bir agregasyon icat edilmedi.
- Yan refactor: `_resolve_target_resource`, `jarvis/graph/nodes.py`'den `jarvis/execution/
  request.py`'ye public `resolve_target_resource()` olarak taşındı — workflow motoru da aynı
  mantığa ihtiyaç duyuyordu, ikinci bir kopya yerine `nodes.py` da artık oradan import ediyor.

**Bilinçli olarak YAPILMADI** (`workflow_engine.py`'nin kendi docstring'i): hiçbir LLM *ne zaman*
yeniden planlama gerektiğine ya da yeni adımların ne olacağına karar vermiyor —
`WorkflowEngine.replan()` `max_replans`'ı gerçek, test edilmiş bir bütçe olarak uyguluyor ve
çağıranın verdiği adımları ekliyor, ama bunu tetikleyen hiçbir şey yok (Faz 1'in shadow ledger'ının
Faz 2'den önce var olması ile aynı "tetikleyiciden önce mekanizma" emsali). Hiçbir canlı giriş
noktası gerçek bir kullanıcı isteğinden `WorkflowPlan` üretmiyor — bu faz motoru bağımsız, doğrudan
çağrılabilir bir mekanizma olarak inşa edip test ediyor, Faz 1→2/Faz 6 Kısım 1→2 ile aynı ayrım.

**46 yeni test** (`test_workflow_types.py` 16, `test_workflow_store.py` 9, `test_workflow_engine.py`
21) — gerçek tool nesneleriyle (`jarvis.graph.tools.make_tools()`), fake değil
(`test_langchain_dispatch_coercion.py` emsaliyle aynı). **914 pytest yeşil (868+46), ruff temiz,
`git diff --check` temiz.**

## Ortam / komutlar — bu oturum sonunda
```powershell
git log --oneline -3
#  a87ebe9 docs: sync conversation_id feature and Faz 6 Part 3 decision   <- HEAD, origin da burada
#  a2a1bb3 fix(scripts): remove placeholder-less f-string in auth_setup.py
#  d6ce968 feat(api): add per-client conversation_id support
git rev-list --left-right --count origin/langgraph-migration...HEAD   # 0  3 (push edilmedi)
git status --short
#  M jarvis/execution/request.py
#  M jarvis/graph/nodes.py
#  M jarvis/tool_registry.py
#  ?? jarvis/execution/workflow.py
#  ?? jarvis/execution/workflow_engine.py
#  ?? jarvis/execution/workflow_store.py
#  ?? tests/test_workflow_engine.py
#  ?? tests/test_workflow_store.py
#  ?? tests/test_workflow_types.py
#  (+ CHANGELOG.md/ROADMAP.md/HANDOFF.md docs-sync, + ilgisiz .claude/settings.local.json)
python -m pytest -q       # 914 passed, 206s
ruff check jarvis/ tests/ scripts/    # All checks passed!
git diff --check          # temiz
```

## SONRAKİ OTURUM — kalan iş

1. **İki ayrı commit/push kararı owner'ı bekliyor:**
   (a) Zaten commit'lenmiş 3 commit (`d6ce968`/`a2a1bb3`/`a87ebe9`) hâlâ push'lanmadı.
   (b) Bu oturumun Faz 7 Kısım 1 çalışması (kod + 46 test + docs-sync) hiç commit'lenmedi.
   Owner'ın tercihi net değil — tek seferde mi (b'yi de commit'leyip ikisini birden push), yoksa
   ayrı ayrı mı istiyor, sorulmalı.
2. **Faz 7, Kısım 2 (canlıya bağlama, henüz başlanmadı):** `WorkflowEngine`'i gerçek bir tetikleyiciye
   bağlamak — muhtemelen yeni bir `workflow_start`-tarzı tool, ya da planner'ın çok adımlı bir
   isteği otomatik olarak bir WorkflowPlan'a terfi ettirmesi. Hangisi olacağı owner'ın kararı
   (Faz 6 Part 2'nin repair-ladder yorumu gibi, bu da bir tasarım çatalı — muhtemelen bir dış
   review'den geçirilmeli).
3. **Faz 8 (Evaluation v2 + manuel alpha kapısı)** hâlâ başlanmadı — registry sweep contract
   testleri, property-based fuzzing, 13 maddelik hata sınıfı taksonomisi, alpha kapısı eşikleri.
4. **Faz 6, Kısım 3'ün Literal-terfi maddesi** (değişmedi, hâlâ bilinçli ertelenmiş): canlı trafikte
   `blocked_invalid_args` oranı ölçülmeden gündeme gelmemeli.
5. Diğer Faz 5 kalan işleri (değişmedi): `run_manifest.json`'ın prompt hash/registry version
   alanları boş; `plot_data` dışındaki artifact tool'ları run-scoped değil.
6. Canlı A/B'nin B6 sorusu hâlâ açık (değişmedi). Şampiyon 62/65 referansı kontamine (değişmedi).
7. conversation_id'nin istemci tarafı hâlâ yapılmadı (değişmedi, ayrı takip).
8. Bilinçli ertelenenler (değişmedi): W4b, `[BLOCKED]` sunum katmanı, qwen3.5/ministral-3
   thinking-on, `stoic-spence` rolling summarization, `docs/ARCHITECTURE.md` orchestrator bölümü,
   mobile'ın 71 flutter-analyze info/warning'i.

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — değişmedi.
