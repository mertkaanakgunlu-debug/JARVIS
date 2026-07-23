# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).
>
> **KALICI KURAL (owner koydu, 2026-07-23 — bu hata sınıfının 5. tekrarından sonra):** bu dosya
> kendi kapanış commit'inin SHA'sını ve kendi push/CI sonucunu ASLA yazmaz. Kapanış commit'i
> oturumun commit sayısına her zaman İLİŞKİSEL dahil edilir ("N iş commit'i ve bu kapanış
> HANDOFF commit'i"); oturum sonu durumu "local == origin senkrondu" diye yazılır; test
> iddiaları çalıştırılan komut + tarih ile bağlanır ("tests pass" tek başına yazılmaz). Branch
> ucunun CI sonucuna her zaman `gh run list --branch langgraph-migration` ile canlı bakılır —
> bu dosyadan okunmaz.

## Last session: 2026-07-23 (16. oturum) — OWNER DOĞRULAMASI SONRASI DÜZELTME: HANDOFF TUTARSIZLIĞI + CI'NIN TEK FLAKY TESTİ

**Durum tek cümlede:** Owner 15. oturumun çıktısını GitHub üzerinden bağımsız doğruladı — kod
değişikliklerinin hepsi doğru bulundu (geri alma yok), ama HANDOFF yine kendi kapanış
commit'ini saymamıştı; bu oturum HANDOFF'u düzeltti, yukarıdaki kalıcı kuralı koydu VE CI'yı
aralıklı kırmızıya boyayan tek flaky testi kök nedeninden deterministik yaptı (`dd339b9`).

### Owner doğrulamasının sonucu (bu oturumun sebebi)

- **Kod tarafı ONAYLANDI:** 15. oturumun 3 remediation commit'i (`e83a7b4`, `31a63ac`,
  `b61ca21`) yerinde ve doğru; geri alma gerekmedi. "4 yeni + 5 mevcut test dosyası" düzeltmesi
  de doğrulandı.
- **HANDOFF HATALIYDI:** "bu oturumda 3 commit / HEAD=`b61ca21` / push `9ddf8fb..b61ca21`"
  diyordu — gerçekte 15. oturum 3 remediation commit'i VE onları kaydeden kapanış HANDOFF
  commit'i olmak üzere toplam **4** commit push'lamıştı; "actual final state" başlıklı kapanış
  commit'i kendini saymamıştı. Yukarıdaki kalıcı kural bunun yapısal çözümü (SHA pin'lemeye
  çalışmak değil — o self-reference'ı yeniden üretir).
- **Owner'ın "CI kanıtı yok" bulgusu ise yanlış çıktı** (bu oturum canlı doğruladı): CI var
  (`.github/workflows/ci.yml` — windows-latest, full pytest + ruff, Faz 7'de eklendi) ve bu
  branch'in her push'unda koşuyor. `b61ca21` → CI **SUCCESS** (run 29977003242, 1011 passed) —
  ana remediation paketi bağımsız CI yeşili almış durumda. 15. oturumun kapanış docs commit'i →
  CI **FAILURE** (run 29977298023) — ama docs-only diff'te tek fail:
  `test_ps_wrapper_propagates_driver_failure_exit_code` (`assert 0 >= 1`), yani bilinen flaky,
  kod regresyonu değil (22'sindeki iki kırmızı run'ın da sebebi aynı test).

### Bu oturumda yapılan iş: 1 fix commit'i ve bu kapanış HANDOFF commit'i

1. **`dd339b9` — fix(tests,ci): ps-wrapper exit-code guard'ı deterministik yapıldı.**
   İki yarışlı kök neden: (a) stub'ın portu çalınabiliyordu — http.server VE uvicorn ikisi de
   `SO_REUSEADDR` set ediyor; Windows'ta bu, ikinci soketin dolu portu bind etmesine izin veren
   hijack bayrağı. Gerçek sunucu yarışı kazanınca trafiği alıp CI'da (model yok) readiness
   penceresinde ölüyordu. (b) `ab_run_config.ps1`'in readiness döngüsü /status'u probe etmeden
   ÖNCE `$srv.HasExited`'a bakıyordu — ölü gerçek sunucu, stub cevap verirken bile run'ı
   `server_not_ready` / `invalid_runs=0` yoluna düşürüyordu (testin reddettiği manifest şekli
   tam bu; 22'sindeki KeyError düzeltmesi semptomu kapatmıştı, yolu değil). Düzeltme: stub
   `SO_EXCLUSIVEADDRUSE` ile bind ediyor (çalınamaz; Windows-dışı no-op) + readiness döngüsü
   önce probe, sonra liveness. Gerçek A/B kullanımının davranışı değişmedi (ölen sunucu yine
   ~2s içinde abort eder).
2. **Bu HANDOFF düzeltmesi** (4-commit sayımı, CI gerçeği, kalıcı kural, canlı doğrulanmış
   durum) + CLAUDE.md'ye kuralın tek cümlelik kalıcı kopyası.

### Canlı doğrulanmış durum (2026-07-23, bu oturumda koşuldu)

```powershell
git log --oneline -6
#  (en üstte bir de bu dosyayı yazan kapanış commit'i görünür — kural gereği SHA'sı burada yok)
#  dd339b9 fix(tests,ci): make the ps-wrapper exit-code guard deterministic
#  2f6d60f docs: overwrite HANDOFF.md ...   <- 15. oturumun kendini saymayan kapanış commit'i
#  b61ca21 fix(agent,api,cli,workflow): ... <- CI SUCCESS (run 29977003242)
#  31a63ac docs: correct HANDOFF.md's unpushed commit count (7 -> 8)
#  e83a7b4 fix(graph): confirmation-resume gate must reject non-exact decision strings
python -m pytest -q       # 1012 passed, 0 failed (212s) — FULL suite, yerel
python -m pytest tests/test_ab_harness_guards.py -q   # 18 passed (düzeltme sonrası)
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py   # All checks passed!
git status --short        # yalnız .claude/settings.local.json (oturum öncesinden, ilgisiz)
```

- **Full suite bu oturumda yerel YEŞİL (1012/1012).** 15. oturumun tek seferlik gördüğü 21
  chromadb hatası bugün reprodüve OLMADI; CI'da da 22'sindeki `fix(tests)` commit'inden beri
  chromadb hatası görünmüyor. Statü: makine-lokal/aralıklı flakiness — tekrar görülürse o
  oturumda araştırılmalı; "full suite kırmızı" diye TAŞINMAMALI, ama "her zaman yeşil" diye de
  satılmamalı.
- Bu oturum sonunda local `langgraph-migration` == `origin/langgraph-migration` senkrondu
  (`dd339b9` ve bu kapanış HANDOFF commit'i push'landı).

## SONRAKİ OTURUM — kalan iş

1. **Branch ucunun CI run'ını kontrol et** (`gh run list --branch langgraph-migration -L 3`):
   `dd339b9` sonrası python job'ının yeşil kalması beklenir; kırmızıysa flake'in üçüncü bir
   modu var demektir — log'daki FAILED satırından devam. (Mobile job'ın flutter-analyze fail'i
   `continue-on-error` — run'ı kırmızı yapmaz, ayrı eski iş.)
2. **Faz 8 (Evaluation v2 + manuel alpha kapısı)** — önü açık; push/CI bekleyen bir şey yok
   (`b61ca21` bağımsız CI yeşili de aldı).
3. **Model tool-calling güvenilirliği** (14. oturumda canlı gözlemlendi, düzeltilmedi): yerel
   modelin bazen tool çağırmadan başarı uydurması — muhtemelen sistem promptu / tool-seçim
   talimatları tarafı, workflow safety kernel'i değil.
4. `workflow_start`'ın JSON `steps` güvenilirliği hâlâ ölçülmedi (13. oturumdan).
5. Gerçek CLI REPL E2E testi hâlâ yok (bilinçli; eklenen CLI/voice/API confirmation testleri
   fake-agent/TestClient seviyesinde regression testleri — gerçek Ollama + LangGraph checkpoint
   + SSE + voice zincirini uçtan uca doğrulamıyorlar).
6. Diğer eski kalanlar (değişmedi): Faz 6 Kısım 3 Literal-terfi, Faz 5 kalanları
   (`run_manifest.json` prompt hash/registry version, artifact tool run-scoping), canlı A/B'nin
   B6 sorusu, conversation_id client-side adoption, 4 worktree branch, mobile flutter-analyze
   info/warning, chromadb yerel flakiness izlemesi (yukarıdaki not).

## Değişmeyen taşınan işler

- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice (+ workflow onayı API'den
  de mümkün — ama Electron/mobil UI'ı bunu render etmiyor).
- Pre-first-turn kozmetik model label — değişmedi.
