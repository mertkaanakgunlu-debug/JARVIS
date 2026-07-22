# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-22 (13. oturum) — FAZ 7 KISIM 1 (WORKFLOW MOTORU) + KISIM 2 (CANLI TETİKLEYİCİ) YAPILDI; KISIM 1 COMMIT'LENDİ+PUSH'LANDI, KISIM 2 HENÜZ COMMIT'LENMEDİ

**Durum tek cümlede:** Bu oturum önce 11. oturumun bıraktığı commit kararını uyguladı (owner "3
parçalı yapıyla commit et" dedi, 3 commit landed+push'landı, CI yeşil), sonra owner "sıradaki faz"
dedi ve **Agent Runtime rev.2'nin Faz 7'si (Workflow Runtime)** iki bölüm halinde inşa edildi:
**Kısım 1** (bağımsız workflow motoru — commit'lendi `20280a3`, push'landı, CI yeşil) ve **Kısım 2**
(motoru gerçek bir model-facing tool'a bağlama: `workflow_start`/`workflow_status` + insan-only
`/workflow` CLI komutu — owner "devam et" dedi, inşa edildi, test edildi, **henüz commit'lenmedi**).
**926 pytest yeşil (868+46+12), ruff temiz, `git diff --check` temiz.**

## Bu oturumda yapılanlar

### 1. 11. oturumun commit kararı uygulandı + push'landı

`d6ce968` (feat: conversation_id) + `a2a1bb3` (fix: auth_setup.py) + `a87ebe9` (docs) — commit'lendi,
`git fetch`+`rev-list` ile temiz fast-forward doğrulanıp push'landı. CI: `python` job yeşil.

### 2. Faz 7, Kısım 1 — Workflow Runtime motoru (commit `20280a3`, push'landı, CI yeşil)

Owner'a kaç faz kaldığı soruldu (Faz 7 + Faz 8, artı Faz 6'nın canlı-veri-bekleyen bir alt maddesi),
"sıradaki faz" (Faz 7) ile devam edildi. Yeni `jarvis/execution/workflow.py`/`workflow_store.py`/
`workflow_engine.py` — bağımsız bir `WorkflowEngine`: bağımlılık sıralı adımlar, adım bütçesi,
SQLite checkpoint/resume (çökme sonrası `idempotency` journal'ına bakarak dürüst kurtarma), onay
duraklatma (`confirmation_node`'un HMAC bağlamasının aynısı, LangGraph interrupt'ı olmadan), ve dar
kapsamlı otomatik telafi (yalnız `file_write` ve `todo add` için gerçek kayıtlı ters işlem). Tek-turlu
chat graph'ından ayrı, Faz 1-4'ün execution contract'ını yeniden kullanıyor. Yazarken gerçek bir hata
bulundu ve düzeltildi: adım bütçesi sayacı yayılan (hiç çalıştırılmamış) "skipped" adımları da
sayıyordu. 46 yeni test. Tam detay [CHANGELOG.md](CHANGELOG.md)'de.

### 3. Faz 7, Kısım 2 — Canlı tetikleyici (henüz commit'lenmedi)

Owner "devam et" dedi, Kısım 1'in kendi notundaki açık soru (motoru nasıl gerçek bir isteğe
bağlamalı) ele alındı. **Önemli güvenlik kararı:** onay çözümlemesi (approve/deny) bilinçli olarak
bir tool DEĞİL, insan-only bir CLI komutu (`/workflow`) yapıldı — agent'ın kendi onayını kendisinin
vermesini engellemek için (mevcut `confirmation_node`'un LangGraph interrupt'ının da aynı özelliği
taşıdığı gibi: yalnız transport katmanı `Command(resume=...)` ile devam ettirebilir, model tool
call'ı ile değil).

- `workflow_start(goal, steps)` — yeni `@tool`, `steps` bir JSON dizisi (bu codebase'in mevcut
  karmaşık-argüman geleneği — `geo_math`'in `grid_data`/`x_data` gibi). Her `capability`,
  modelin görebildiği alpha-filtrelenmiş tool listesine karşı doğrulanıyor (`python_run` gibi
  alpha-disabled bir capability, bilinmeyen bir tool adı gibi reddediliyor).
- `workflow_status(workflow_id)` — salt-okunur, hiçbir şeyi ilerletmiyor/onaylamıyor.
- `/workflow list|show <id>|approve <id>|deny <id> [sebep]` — yeni CLI komutu (`cli.py`).
- `jarvis/graph/tool_router.py`'de yeni "workflow" domain'i — `procedure_save`'in kullandığı
  "yalnız açık niyetle" (explicit_tool_intent) mekanizması genelleştirildi. **Gerçek bir çakışma
  yazarken bulundu ve düzeltildi:** ilk "adım adım" kalıbı, mevcut bir procedure-save test
  sorgusuyla da eşleşiyordu — "çok adımlı görev"e daraltıldı, kilitleyen bir test eklendi.
- `WorkflowEngine.report()` modül seviyesinde `render_workflow_report()`'a taşındı (salt-okunur
  çağıranlar tam bir engine inşa etmeden rapor okuyabilsin diye).

**Bilinçli olarak test edilmedi:** `/workflow` CLI komutunun kendisi için REPL-loop testi yok — bu
repo'da `cli.py`'nin interaktif döngüsünü uçtan uca süren bir test altyapısı hiç yok (mevcut tek CLI
test dosyası salt bir yardımcı fonksiyonu test ediyor), ve komutun kendi mantığı zaten test edilmiş
`WorkflowEngine` metodları üzerine ince bir argüman-ayrıştırma katmanı — bu orana yeni bir test
altyapısı kurmak orantısız görüldü, kayıt altına alınmış bir kapsam kararı.

12 yeni test (`test_tool_router.py` +3, yeni `test_workflow_tools.py` 9). **926 pytest yeşil
(868+46+12), ruff temiz, `git diff --check` temiz — Kısım 2 henüz commit'lenmedi.**

## Ortam / komutlar — bu oturum sonunda
```powershell
git log --oneline -4
#  20280a3 feat(workflow): add standalone workflow runtime (Agent Runtime rev.2, Faz 7 Part 1)  <- HEAD, origin da burada
#  a87ebe9 docs: sync conversation_id feature and Faz 6 Part 3 decision
#  a2a1bb3 fix(scripts): remove placeholder-less f-string in auth_setup.py
#  d6ce968 feat(api): add per-client conversation_id support
git rev-list --left-right --count origin/langgraph-migration...HEAD   # 0  0 (Kısım 1 push'landı)
git status --short
#  M jarvis/cli.py
#  M jarvis/execution/workflow_engine.py
#  M jarvis/graph/tool_router.py
#  M jarvis/graph/tools.py
#  M jarvis/tool_registry.py
#  M tests/test_tool_router.py
#  ?? tests/test_workflow_tools.py
#  (+ CHANGELOG.md/ROADMAP.md/HANDOFF.md/MEMORY.md docs-sync, + ilgisiz .claude/settings.local.json)
python -m pytest -q       # 926 passed, 223s
ruff check jarvis/ tests/ scripts/    # All checks passed!
git diff --check          # temiz
```

## SONRAKİ OTURUM — kalan iş

1. **Faz 7 Kısım 2'nin commit kararı owner'ı bekliyor** — kod+test+docs working tree'de duruyor,
   `origin` hâlâ Kısım 1'de (`20280a3`, byte-equal).
2. **Faz 7 Kısım 2'nin kendi takip maddeleri (yeni):**
   - `/workflow` CLI komutu gerçek bir terminalde manuel olarak hiç denenmedi (yalnız
     `WorkflowEngine`'in kendisi + tool'ların `.ainvoke()` çağrıları test edildi) — bir sonraki
     oturumda `python -m jarvis` ile canlı bir workflow başlatıp onaylamak/reddetmek faydalı olur.
   - `workflow_start`'ın JSON `steps` argümanının gerçek bir LLM (özellikle yerel qwen2.5:7b) ile ne
     kadar güvenilir üretildiği hiç ölçülmedi — yalnız doğrudan `.ainvoke()` ile test edildi, model
     bu şemayı gerçekte ne sıklıkla doğru dolduruyor bilinmiyor.
   - API/Electron/mobil'de workflow onayı için hiçbir arayüz yok (yalnız CLI) — mevcut confirmation
     mekanizmasının aynı, zaten bilinen kısıtıyla aynı asimetri.
3. **Faz 8 (Evaluation v2 + manuel alpha kapısı)** hâlâ başlanmadı.
4. **Faz 6, Kısım 3'ün Literal-terfi maddesi** (değişmedi, hâlâ bilinçli ertelenmiş).
5. Diğer Faz 5 kalan işleri (değişmedi): `run_manifest.json`'ın prompt hash/registry version
   alanları boş; `plot_data` dışındaki artifact tool'ları run-scoped değil.
6. Canlı A/B'nin B6 sorusu hâlâ açık (değişmedi). Şampiyon 62/65 referansı kontamine (değişmedi).
   **Not:** Faz 7'nin iki yeni tool'u + yeni "workflow" domain'i canlı A/B'nin tool sayısını/routing
   davranışını etkileyebilir — bir sonraki A/B koşusu bunu hesaba katmalı.
7. conversation_id'nin istemci tarafı hâlâ yapılmadı (değişmedi, ayrı takip).
8. Bilinçli ertelenenler (değişmedi): W4b, `[BLOCKED]` sunum katmanı, qwen3.5/ministral-3
   thinking-on, `stoic-spence` rolling summarization, `docs/ARCHITECTURE.md` orchestrator bölümü,
   mobile'ın 71 flutter-analyze info/warning'i.

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice (+ workflow onayı da hâlâ CLI-only, madde 2).
- Pre-first-turn kozmetik model label — değişmedi.
