# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-22 (10. oturum) — DOC-DRIFT + 2 KÜÇÜK GERÇEK BULGU DÜZELTİLDİ, 3 COMMIT PUSH'LANDI, CI'DA DOĞRULANDI

**Durum tek cümlede:** Bu oturum "kaldığımız yerden devam" ile başladı ve önce HANDOFF.md'nin
kendisinin yalan söylediğini buldu — Faz 6 Kısım 2 aslında önceki bir oturumda `10e1508` ile
COMMIT'LENMİŞTİ (mesaj "Co-Authored-By: Claude Sonnet 5" taşıyor, yani bir Claude Code oturumu
commit etmiş), ama HANDOFF.md/ROADMAP.md hâlâ "henüz commit'lenmedi, HEAD b7d03c6" diyordu — **bu
tam olarak aynı hatanın üçüncü tekrarı** (`git log`'da bunu düzelten iki ayrı geçmiş commit var:
`8613bfc`, `8b3cd1f`). Düzelttim, sonra HANDOFF'un kendi "SONRAKİ OTURUM" listesindeki gerçek işe
geçtim: CI'nin neden kırmızı olduğunu (birden fazla oturumdur "pre-existing, incelenmedi" diye
geçiştiriliyordu) kök nedenine kadar izleyip düzelttim, ve Kısım 2 review'inin bulup ertelediği
küçük ama gerçek `tool_call_fingerprint` normalizasyon boşluğunu kapattım. Owner'ın açık talimatıyla
**3 ayrı, bağımsız commit'te** landed: CI fix `a06cbd2`, fingerprint fix `cf769b2`, docs-sync
`1df4c5b` — sonra owner'ın onayıyla tek seferde `origin/langgraph-migration`'a push'landı
(`8613bfc..1df4c5b`, fast-forward, force YOK; local/origin şu an `0/0`, tam senkron). **857 pytest
yeşil (853+4 yeni test), ruff temiz, `git diff --check` temiz — hem yerelde hem push sonrası gerçek
GitHub Actions'ta** ([run 29885614689](https://github.com/mertkaanakgunlu-debug/JARVIS/actions/runs/29885614689):
`python` job ✓ 8m46s, 857 passed + ruff "All checks passed!"; `electron` ✓ 38s; `mobile`'ın
`flutter analyze`'i beklenen ✗ 3m2s, `continue-on-error: true`, bloklamıyor). **CI fix artık gerçek
CI'da doğrulandı — bu, önceki taslağın "push sonrası teyit edilecek" olarak bıraktığı açık madde,
kapandı.**

**Bir önceki taslak (`1df4c5b` commit'i) burada tam da önlemeye çalıştığı doc-drift kalıbını
dördüncü kez tekrarladı** — commit anında push+CI sonucu henüz gerçekleşmemişti, o yüzden "push
edilecek"/"henüz teyit edilmedi" diye yazıldı (o an için doğruydu), ama HANDOFF.md kendi
sözleşmesi gereği HER ZAMAN mevcut duruma göre olmalı, commit anındaki duruma göre değil — push+CI
sonucu dakikalar içinde gerçekleşince taslak stale kaldı. Bu paragraf owner'ın işaret etmesiyle
aynı oturum içinde, ayrı bir 4. docs-only commit'le düzeltildi. **Genel ders:** bir commit
"X olacak" diye yazıyorsa ve X aynı oturumda gerçekleşecekse, X gerçekleştikten sonra HEMEN bir
takip commit'i ile "X oldu"ya çevir — bir sonraki oturumu beklemeyi bekleme.

## Bu oturumda yapılanlar

### 1. Doc-drift düzeltmesi (HANDOFF.md / ROADMAP.md / CHANGELOG.md)

`10e1508` ("feat: complete Agent Runtime rev.2 Faz 6 bounded arg repair") zaten HEAD'de duruyordu
ve local `langgraph-migration`, `origin/langgraph-migration`'ın **2 commit ilerisinde**
(`b7d03c6`, `10e1508` — ikisi de push'lanmamış). Faz 6 Kısım 2'nin dolu anlatısı artık `10e1508`'in
commit mesajında kalıcı olarak duruyor (ve şimdi CHANGELOG.md'de de) — bu dosyadan çıkarıp oraya
işaret ettim; ayrıca CHANGELOG.md'de hiç var olmayan "Faz 6, Part 2" girdisini ekledim (Faz 4/5/
6-Part-1'in hepsi kendi girdisini almıştı, bu commit'te unutulmuş görünüyor).

**Neden tekrar oldu (üçüncü kez), akılda tutulması gereken kalıp:** owner bir fazı commit etmeden
önce "owner'ın kararını bekliyor" diye HANDOFF taslağı yazılıyor; owner commit kararını verince o
commit'in KENDİSİ HANDOFF/ROADMAP'i de günceller ama taslak metin önceden (commit'ten önce, "henüz
commit'lenmedi" varsayımıyla) yazıldığı için commit sonrası gerçek duruma göre YENİDEN üretilmiyor.
Bir dahaki sefere: bir fazı commit ederken HANDOFF/ROADMAP metnini commit'ten SONRAKİ gerçek
duruma göre (HEAD neye işaret ediyor, push'landı mı) yeniden yaz, önceden hazırlanmış "bekliyor"
taslağını aynen taşıma.

### 2. CI kırmızısının kök nedeni bulundu ve düzeltildi (`scripts/ab_run_config.ps1`)

Birkaç oturumdur HANDOFF/ROADMAP "CI kırmızı ama bu oturumun regresyonu değil" diyip geçiyordu,
hiç kök nedene inilmemişti. `gh run view --log-failed` ile gerçek CI log'una bakıldı:

- **`python` job (BLOCKING, continue-on-error değil) gerçekten kırmızı** —
  `tests/test_ab_harness_guards.py::test_ps_wrapper_propagates_driver_failure_exit_code` düşüyor:
  `assert proc.returncode != 0` başarısız, çünkü `ab_run_config.ps1:39`'daki
  `$Py = "$Repo\.venv\Scripts\python.exe"` GitHub Actions runner'ında yok (CI `requirements-lock.txt`'i
  runner'ın sistem Python'ına kuruyor, venv oluşturmuyor — `ci.yml`). `& $Py ...` çağrısı
  `CommandNotFoundException` fırlatıyor; script'in tepesindeki `$ErrorActionPreference = "Continue"`
  yüzünden bu HATA SCRIPT'İ DURDURMUYOR, `$LASTEXITCODE` önceki bir `git` komutundan kalan `0`'ı
  taşıyor, wrapper "ölçüm geçerli" sanıp `exit 0` ile çıkıyor — **bu dosyanın kendi felsefesinin
  ("a harness that cannot measure must never emit a plausible-looking zero") tam olarak yakalamaya
  çalıştığı hata sınıfı, ama test altyapısının kendisinde, driver'da değil.**
  **Düzeltme:** `$Py` yoksa `py`/`python`/`python3` adaylarını sırayla dene, ama sadece
  `Get-Command`'ın BULMASI yetmiyor — gerçekten `--version` çalıştırıp exit code'u doğrula. Bu ayrım
  gerçek: bu dev makinede çıplak `python`/`python3` Windows'un App Execution Alias stub'ları (Store'dan
  yükle nag'i basıp exit 9009 ile çıkıyor), sadece `py` gerçek bir yorumlayıcıya çözülüyor —
  canlı test edilerek bulundu, varsayılmadı. Hiçbir aday çalışmazsa script artık sessizce devam
  etmek yerine gürültülü `exit 1` ile duruyor.
  **Doğrulama seviyesi (dürüstçe):** dev makinede `.venv` var olan asıl yol hiç değişmedi (17/17
  `test_ab_harness_guards.py` + 857 tam paket yeşil). Fallback dalının PowerShell mekaniği izole
  test edildi (gerçekten `py`'yi seçip bozuk `python`/`python3` stub'larını atladığı doğrulandı).
  Kod `a06cbd2`'de commit'lendi, push'landı, ve **gerçek GitHub Actions'ta teyit edildi**: `python`
  job [run 29885614689](https://github.com/mertkaanakgunlu-debug/JARVIS/actions/runs/29885614689)'da
  8m46s'de yeşil (857 passed, ruff "All checks passed!") — birkaç oturumdur "pre-existing"
  diye geçiştirilen kırmızı artık gerçekten kapandı, varsayımla değil canlı CI çalıştırmasıyla
  doğrulanarak.
- **`mobile` job kırmızı ama BLOCKING DEĞİL** (`continue-on-error: true`) — `flutter analyze` 71
  adet salt "info"/"warning" seviyeli sorun buluyor (çoğu `withOpacity` deprecation, düzinelerce
  dosyada), gerçek bir hata değil. Kapsamı bu oturumun "kaldığımız yerden devam" hedefine göre
  orantısız büyük bir kozmetik temizlik olduğu için DOKUNULMADI — bilinçli, orantılı bir kapsam
  kararı, unutkanlık değil.

### 3. `tool_call_fingerprint` case/whitespace normalizasyonu (`jarvis/graph/tool_accounting.py`)

Faz 6 Kısım 2'nin ikinci review turunun bulup "bu oturumda düzeltilmedi, ayrı iyileştirme" diye
bıraktığı gerçek ama düşük ciddiyetteki boşluk: fingerprint ham `args`'ı hash'liyordu, yani
`action="send"` ile `action=" SEND "` farklı hash üretiyordu — aynı turda modelin biçim farkıyla
retry ettiği bir çağrı duplicate-tespitinden kaçabiliyordu. **Düzeltme:** yalnızca `action`
alanı (varsa ve string ise) hash'lenmeden önce `.strip().lower()` ile normalize ediliyor —
`jarvis.execution.args_schemas`'ın zaten AYNI alan için yaptığı normalizasyonla birebir tutarlı
(kör bir "tüm string'leri normalize et" DEĞİL — email body/arama sorgusu/dosya yolu gibi diğer
alanlar bilerek ham kalıyor, çünkü onlar gerçekten case-sensitive). Kalıcılık riski yok:
`jarvis/execution/idempotency.py`'nin journal'ı `execution_id`'yi PRIMARY KEY yapıyor, fingerprint
sadece açıklayıcı bir kolon, hiçbir yerde lookup key değil — algoritma değişikliği eski satırları
bozmuyor. 4 yeni test eklendi (`tests/test_tool_limits.py`): case/whitespace eşitliği, diğer
alanların hâlâ case-sensitive kaldığının regresyon testi, non-string `action`'ın crash etmediği,
ve fonksiyonun çağıranın orijinal `args` dict'ini mutate etmediği.

## Ortam / komutlar — 3 commit + push + gerçek CI doğrulamasından SONRAKİ nihai durum
```powershell
git log --oneline -6
#  1df4c5b docs: reconcile Faz 6 status and follow-up fixes
#  cf769b2 fix(runtime): normalize tool action fingerprints
#  a06cbd2 fix(ci): resolve a working Python interpreter in AB harness
#  10e1508 feat: complete Agent Runtime rev.2 Faz 6 bounded arg repair
#  b7d03c6 docs: fix a real arithmetic error (841 -> 792) and catch up docs to reality
#  8613bfc docs: HANDOFF/ROADMAP said "not yet committed" about commits that just landed
git rev-list --left-right --count origin/langgraph-migration...HEAD   # 0  0 (tam senkron)
git status --short
#  M .claude/settings.local.json          (ilgisiz, hiçbir commit'e girmedi)
```
`gh run view 29885614689` (push'un tetiklediği CI çalışması,
https://github.com/mertkaanakgunlu-debug/JARVIS/actions/runs/29885614689):
`python` ✓ 8m46s (857 passed, ruff "All checks passed!") · `electron` ✓ 38s · `mobile` ✗ 3m2s
(`flutter analyze`, `continue-on-error: true`, bloklamıyor — 71 info/warning seviyeli kozmetik
uyarı, bilinçli olarak dokunulmadı). `langgraph-migration` `origin` ile birebir aynı. Bu oturumun
CI fix'i **artık sadece yerel muhakeme değil, gerçek GitHub Actions çalıştırmasıyla doğrulandı.**

## SONRAKİ OTURUM — kalan iş

1. **Faz 6, Kısım 3 (hâlâ yapılmayanlar, bilinçli):**
   - `@tool` fonksiyon imzalarını doğrulanmış Literal'lere terfi ettirmek (modelin GÖRDÜĞÜ şemayı
     sıkılaştırmak) — dahili validation canlı trafikte bir süre gözlemlenip (audit_log'daki
     `blocked_invalid_args` oranı) ölçüldükten sonra gündeme gelmeli.
   - "Alternatif capability" (planın "tek repair → alternatif capability → açık hata" ladder'ının
     ortadaki basamağı) hiç yapılmadı — yalnız "tek repair → açık hata" var.
2. Diğer Faz 5 kalan işleri (değişmedi): API'nin gerçek per-client conversation_id desteği yok;
   `run_manifest.json`'ın prompt hash/registry version alanları boş; `plot_data` dışındaki
   artifact tool'ları run-scoped değil.
3. Canlı A/B'nin B6 sorusu hâlâ açık (değişmedi). Şampiyon 62/65 referansı kontamine (değişmedi).
4. Bilinçli ertelenenler (değişmedi): W4b, `[BLOCKED]` sunum katmanı, qwen3.5/ministral-3
   thinking-on, `stoic-spence` rolling summarization, `docs/ARCHITECTURE.md` orchestrator bölümü,
   mobile'ın 71 flutter-analyze info/warning'i (yukarıda "Bu oturumda yapılanlar #2"de detay —
   bilinçli olarak kapsam dışı bırakıldı, unutulmadı).

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — değişmedi.
