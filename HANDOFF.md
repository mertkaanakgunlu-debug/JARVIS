# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).
>
> **KALICI KURAL (owner koydu, 2026-07-23):** bu dosya kendi kapanış commit'inin SHA'sını ve
> kendi push/CI sonucunu ASLA yazmaz. Kapanış commit'i oturumun commit sayısına her zaman
> İLİŞKİSEL dahil edilir ("N iş commit'i ve bu kapanış HANDOFF commit'i"); oturum sonu durumu
> "local == origin senkrondu" diye yazılır; test iddiaları çalıştırılan komut + tarih ile
> bağlanır ("tests pass" tek başına yazılmaz). Branch ucunun CI sonucuna her zaman
> `gh run list --branch langgraph-migration` ile canlı bakılır — bu dosyadan okunmaz.

## Last session: 2026-07-23 (17. oturum) — FAZ 8 (SON FAZ) + ELECTRON CONFIRMATION UI + BAĞIMSIZ İKİNCİ REVIEW'IN 4 BULGUSU

**Durum tek cümlede:** Agent Runtime rev.2'nin son fazı Faz 8 inşa edildi (offline eval altyapısı
+ alpha gate enstrümanı), ardından owner'ın seçtiği Electron confirmation UI inşa edildi, sonra
owner bu ikisinin diff'ini başka bir modele (Sonnet) bağımsız review'a soktu — 4 gerçek bulgu
çıktı, hepsi kodda tek tek doğrulandı (biri chromadb'nin kendi kaynağına kadar inildi), hepsi
düzeltildi ve test edildi.

**COMMIT DURUMU:** bu oturumda 9 iş commit'i push'landı (Faz 8: `7d6a7af`; Electron UI: `52d0b72`;
review'ın 4 bulgusu: `599066d`, `7136690`, `5645ae2`, `3c6c05a`; **canlı CI'da yakalanan bir
izleme-sonrası düzeltme**: `0687772` — bkz. aşağıdaki "CI'da yakalanan" notu) + aralarında 2 docs
commit'i (`8b451dc`, `8e3977e`) ve bu kapanış docs commit'i. Oturum sonunda local == origin
senkrondu. Kalıcı kural gereği kapanış commit'inin kendi SHA'sı/CI'ı burada yok — uç CI'ına
`gh run list --branch langgraph-migration` ile bakın. (`.claude/settings.local.json` her zamanki
gibi hariç.)

**CI'da yakalanan, docs commit'inden SONRA çıkan bir 5. sorun (`0687772`), CANLI DOĞRULANDI:**
`4a37222`'i push ettikten sonra CI'ı izlerken `electron` job'ının `npm ci` adımında gerçekten
kırıldığını gördüm — `vitest@^4.1.10` (review'a yanıt olarak eklenirken "latest" seçilmişti, ne
sürüklediği kontrol edilmeden) kendi içinde `vite@7`'yi (esbuild 0.27/0.28 gerektiren) taşıyor;
bu, projenin zaten sahip olduğu `vite@^5.4.0`/esbuild@0.21.5 kuşağıyla çakışan İKİNCİ bir nesil.
Yerel npm (11.16.0) bunu gevşek çözmüş, CI'nın npm'i (workflow "20" istese de GitHub artık zorla
Node 24'e geçiriyor, farklı bir npm geliyor) daha katıymış ve lockfile'ı reddetmiş. Düzeltme:
`vitest@^2.1.9`'a geçildi (aynı `vite@5` kuşağını hedefleyen en yeni majör — ikinci nesil hiç
girmiyor). Bu kez "düzelttim" demeden önce `node_modules` silinip CI'ın attığı `npm ci` komutu
BİREBİR yerel çalıştırıldı, sonra test+build. Dürüst not: `npm audit` artık 8 önceden-var
advisory gösteriyor (5'ten) — hepsi electron/vite/esbuild/babel'ın ZATEN var olan CVE'leri,
vitest'in kendi vite-node/mocker'ı aynı zincire farklı yollardan değiyor; düzeltmeleri kırıcı
sürüm atlamaları (electron 43, vite 8) gerektiriyor, bu oturumun kapsamı dışında — sessizce
ertelenmedi, burada görünür kılındı.

`0687772`'nin CI run'ı (29989343268) canlı izlendi: **`python` job SUCCESS, `electron` job
SUCCESS** (`npm ci`/`npm test`/`npm run build` üçü de gerçekten geçti), `mobile` job bilinen
kozmetik `flutter analyze` hatası (continue-on-error, run'ın genel `conclusion`'ını
etkilemiyor) — run'ın genel sonucu **success**. Bu, bugünün TÜM commit zincirinin (Faz 8'den bu
son düzeltmeye kadar) bağımsız CI'da uçtan uca yeşil olduğunun canlı kanıtı.

### Faz 8 + Electron confirmation UI — özet (detay: CHANGELOG.md)

Faz 8: registry sweep (157 test), per-capability contract testleri (38 test), hypothesis
property-fuzz (derandomize profil), 13 sınıflı hata taksonomisi (`jarvis/execution/taxonomy.py`,
tek kaynak), `scripts/alpha_gate.py` enstrümanı. Electron: paylaşılan SSE reader
(`chatStream.js`), amber `ConfirmationOverlay`, WS `confirmation_required` dinleme,
`conversation_id` persistence.

### Bağımsız review'ın 4 bulgusu — hepsi doğrulandı ve düzeltildi

Owner Sonnet'e diff'i (Faz 8 + Electron UI) bağımsız review ettirdi; 4 madde geldi, hepsi
kodda/chromadb kaynağında tek tek doğrulandı (kör kabul edilmedi) — bu repo'nun "dış review'ı
ampirik doğrula, körü körüne uygulama" disiplini (bkz. [[project-agent-runtime-rev2]] update
#10'daki P0 çürütme emsali).

1. **Cross-transport confirmation race (GERÇEK, düzeltildi — `599066d`).** `python -m jarvis
   --api --voice --wakeword`'ün voice loop'u API server ile AYNI process'te, aynı `JarvisAgent`
   + `event_bus`'ı paylaşarak çalıştığı doğrulandı. Bu oturumun Electron değişikliği HUD'u
   `confirmation_required` WS broadcast'ini dinler hale getirdiğinden (Phase 3'ten beri
   yayındaydı, hiç dinlenmiyordu), sesle başlayan bir onay artık HUD'dan da çözülebiliyor — ama
   hem `voice_api.py` hem `cli.py`'nin yerel `pending_confirmation` bayrağı bundan haberdar
   değildi: kullanıcının SONRAKİ sesli cümlesi, zaten başka yerden çözülmüş eski bir onaya
   "evet/hayır" cevabı sanılıp yutuluyordu (sunucu tarafı tekrar-çalıştırmayı engelliyor ama
   gerçek komutu kurtaramıyor). Düzeltme: `JarvisAgent.has_pending_confirmation()` +
   `voice/session.is_confirmation_still_pending()` — her iki çağrı sitesi artık tüketmeden önce
   hâlâ gerçekten bekleyen mi diye soruyor; değilse (başka yerden çözülmüş VEYA TTL ile
   silinmiş) normal yeni tur olarak işliyor. 7 test.
2. **`alpha_gate.py` exit-code + isolation açığı (GERÇEK, düzeltildi — `7136690`).**
   `evaluate()` rapor ne derse desin HER ZAMAN 0 dönüyordu — artık GEÇTİ/KALDI/EKSİK
   VERİ/HARNESS_ERROR için 0/1/2/3. `isolation`'ın hem rapor satırı hem kendi exit code'u
   `tool_ok`'u hiç kontrol etmiyordu (file_list 0/20 başarı + sızıntı yok = eskiden "geçti"
   sayılıyordu). `verdict_of()`/`isolation_verdict_ok()` tek kaynak yapıldı. 13 test.
3. **ChromaDB flake kök nedeni (GERÇEK, chromadb kaynağında doğrulandı, düzeltildi —
   `5645ae2`).** `test_shadow_replay_equivalence.py`'nin off/shadow kollarının AYNI test
   içinde ayrı `Memory()` inşa ettiğini ama `isolated_cwd`'ın per-test chdir yaptığını (per-arm
   değil) ve chroma_dir/vault_dir default'larının cwd-relative olduğunu doğruladım — iki kol
   AYNI dizini paylaşıyordu. Daha da derini: `chromadb.api.shared_system_client
   .SharedSystemClient`'ı okuyup bunun `persist_directory` string'ine keyed, process-global,
   refcount'lu bir System cache'i olduğunu ve hiçbir yerde `close()` çağrılmadığı için
   refcount'un asla sıfırlanmadığını (dolayısıyla iki Memory'nin aynı System'i SESSIZCE
   paylaştığını, ve HER test dosyasının kendi System'ini süresiz sızdırdığını) doğruladım.
   Düzeltme: her kol artık kendi workspace'ine izole chroma/vault dizini alıyor, `Memory.close()`
   eklendi (gerçek `chromadb.Client.close()`'a sarma) ve `test_shadow_replay_equivalence.py` +
   `test_procedure_store.py`'de kullanılıyor. Yan etki yakalandı: chroma'yı workspace içine
   taşımak `_side_effects()`'in onu da hash'lemesine yol açtı (chroma'nın kendi iç byte'ları
   reproducible değil) → checkpoint gibi hariç tutuldu. **Ampirik doğrulama: 4 dosya 15 ardışık
   turda, her turda 38/38 yeşil, 0 hata.** 4 yeni test (`test_memory_lifecycle.py`).
4. **Electron `chatStream.js` sağlamlığı (GERÇEK, düzeltildi — `3c6c05a`).** `resp.ok`
   kontrolü yoktu (401/422 gibi non-2xx JSON gövde sessizce `{text:'', error:null}` olarak
   yutuluyordu) ve stream sonundaki newline'sız son satır hiç işlenmiyordu — ikisi de doğrulandı
   ve düzeltildi. Repo'nun ilk JS test altyapısı (Vitest) kuruldu, 13 senaryo (istenen 9 + 4 ek,
   tek-byte chunk split dahil) — hepsi geçti. CI'ın electron job'ına `npm test` eklendi (job'ın
   zaten var olan continue-on-error politikasını miras alıyor).

### Canlı doğrulama (2026-07-23, bu oturumda koşuldu)

```powershell
python -m pytest -q       # 1263 passed, 0 failed (206s) — FULL suite, yerel
# chromadb flake tekrar testi (4 dosya x 15 tur):  DONE: 0/15 rounds failed
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py scripts/alpha_gate.py   # All checks passed!
cd electron && npm ci && npm test && npm run build   # vitest@2.1.9, 13/13; build clean; npm ci de dahil (CI'ın attığı komutun birebiri)
gh run view 29989343268 --json jobs   # bu oturumun SON push'unun CI'ı: python SUCCESS, electron SUCCESS, mobile bilinen kozmetik fail (continue-on-error) — genel conclusion: success
```

## SONRAKİ OTURUM — kalan iş

1. **CANLI HUD E2E'si (değişmedi):** server + Electron + gerçek APPROVE/DENY tıklaması —
   `52d0b72`'nin ve bugünkü cross-transport düzeltmesinin (`599066d`) gerçek kabul testi. Bu
   ikinciyi test etmek için: sesle bir L3 aksiyon başlat, HUD'dan onayla, SONRA sesle alakasız
   bir şey söyle — düzeltmeden önce bu ikinci komut yutulurdu.
2. **Model tool-calling güvenilirliği** (14. oturumdan; taksonomiyle ölçülebilir):
   `false_success_claim`'i 0'a indirme — alpha kapısının asıl kilidi.
3. Alpha gate'in owner-koşusu ölçümleri (10-run `ab_run_config.ps1` + `isolation`) + 2 kapsama
   boşluğu senaryosu (uzun-workflow E2E, block/veto-dışı recovery sınıfları).
4. Branch ucunun CI'ı: `gh run list --branch langgraph-migration -L 3` ile kontrol et.
5. **Yeni, küçük, bilinçli kapsam dışı bırakılan:** `Memory()` inşa eden DİĞER test dosyaları
   (bugün yalnız en açık ilişkili ikisi — shadow-replay + procedure-store — düzeltildi) hâlâ
   `close()` çağırmıyor; her biri kendi System'ini süresiz sızdırıyor (zararsız ama gereksiz —
   process pytest'in kendisi bittiğinde zaten temizleniyor). Repo-geneli bir "her Memory()
   testi close() etsin" taraması yapılmadı — orantısız kapsam genişlemesi olurdu, ayrı bir
   oturumun işi olabilir.
   `electron/`'da `npm audit` 8 önceden-var advisory gösteriyor (electron/vite/esbuild/babel'ın
   kendi CVE'leri, bu oturumdan önce de vardı) — düzeltmeleri kırıcı sürüm atlamaları (electron
   43, vite 8) gerektiriyor, bilinçli olarak ertelendi.
6. Eski kalanlar (değişmedi): `workflow_start` JSON `steps` güvenilirliği; gerçek CLI REPL E2E;
   Faz 6 Kısım 3 Literal-terfi; Faz 5 kalanları; canlı A/B B6 sorusu; 4 worktree branch; mobile
   flutter-analyze info/warning.

## Değişmeyen taşınan işler

- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Pre-first-turn kozmetik model label — değişmedi.
