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

## Son oturum: 2026-07-23 (19. oturum) — 18. OTURUMUN İŞİ ÜÇ AYRI TURDA DOĞRULANDI/DÜZELTİLDİ: ATOMİK CLAIM, TTL-AWARENESS (BENİM KENDİ HATALI İLK DEĞERLENDİRMEMİN DÜZELTMESİ), VE CI'DA AYRI YAKALANAN BİR RUFF SÜRÜM KAYMASI — HEPSİ COMMIT+PUSH+CI DOĞRULANDI

**Durum tek cümlede:** Owner, 18. oturumun uncommitted diff'ine (atomik `claim_pending_
confirmation()` + üçüncü, korumasız `/ws` çağrı sitesi + 2 küçük bulgu) karşı yapılan bağımsız
değerlendirmeleri art arda yapıştırdı; ilk tur (thread-safety + 5 test senaryosu + Electron CI
blocking kararı) uygulandı ve commit+push+CI doğrulandı, AMA bir SONRAKİ tur benim kendi
değerlendirmemdeki gerçek bir hatayı yakaladı (aşağıya bakın) — o da düzeltildi, test edildi,
commit+push+CI doğrulandı; push sırasında BAĞIMSIZ, ilgisiz bir üçüncü sorun (ruff sürüm kayması)
CI'da canlı yakalandı ve o da aynı oturumda kök nedeniyle düzeltildi.

### Tur 1: atomik claim + Electron CI blocking (özet — tam detay CHANGELOG.md)

Thread-safety kod okunarak doğrulandı (`JarvisMonitor`/`TaskExecutor`/wakeword thread'lerinin
hiçbiri `_pending_confirmations`'a dokunmuyor — ek lock gerekmiyor). 5 istenen test senaryosundan
4'ü zaten vardı; remote `/ws` yolu için gerçek `_handle_transcript` closure'ını uçtan uca çalıştıran
yeni `tests/test_ws_remote_confirmation_claim.py` eklendi. Electron CI job'ından
`continue-on-error` kaldırıldı (artık blocking). Commit: `ff357fc` (confirmation fix),
`1faa2c5` (alpha_gate/Memory.close()/CI fix), docs `0c85a51` — hepsi push'landı, CI'da
`gh run view --json` ile doğrulandı: `success` (python/electron), mobile bilinen kozmetik fail.

### Tur 2: BENİM KENDİ HATAM — TTL-awareness açığı gerçekti, "HMAC testi yeterli" gerekçem yanlıştı

Bir SONRAKİ bağımsız değerlendirme, Tur 1'de benim "ek bir entegrasyon testi orantısız, çünkü
güvenlik sınırı zaten `test_expired_approval_is_denied` ile test ediliyor" diye yazdığım
gerekçenin **yanlış** olduğunu buldu — ve haklıydı, kodu tekrar okuyunca kendim de doğruladım:

`claim_pending_confirmation()` atomikti ama TTL-farkında DEĞİLDİ — salt `dict.pop(conf_id, None)`,
yaşına hiç bakmıyor. `_register_pending_confirmation()`'ın kendi TTL süpürmesi fırsatçı: yalnızca
YENİ bir confirmation register edildiğinde çalışıyor. Kullanıcı bir onayın süresi dolana kadar
sessiz kalıp SONRA register tetiklenmeden alakasız bir şey söylerse, stale kayıt sözlükte kalmaya
devam ediyordu — VE `voice/session.py`'nin `resolve_confirmation()`'ı affirmative olmayan HER
transcript'i `deny:<transcript>` kararına çeviriyor, `confirmation_node`'un deny dalı ise HMAC/expiry
kontrolüne HİÇ uğramıyor (deny'de hiçbir şey çalıştırılmıyor, kontrol edilecek bir şey yok — bu
kontrol yalnızca approve yolunda var, `test_expired_approval_is_denied`'ın test ettiği tam olarak
bu). Sonuç: kullanıcının gerçek, alakasız yeni komutu sessizce eski, muhtemelen unutulmuş bir
işlemin "denial reason"ı olarak yutulabiliyordu — tam olarak bu oturumun TÜM düzeltmelerinin
önlemeye çalıştığı hata sınıfı, benim kendi doğrulamamda gözden kaçmış.

**Hata neredeydi:** iki farklı invariant'ı birbirine karıştırdım — *güvenlik invariant'ı*
(süresi dolmuş bir onay asla çalışmamalı — HMAC testi bunu KANITLIYOR) ile *konuşma invariant'ı*
(süresi dolmuş bir onay kullanıcının sonraki alakasız cümlesini yutmamalı — HMAC testi BUNU HİÇ
test ETMİYOR, çünkü deny yolu o kontrole hiç uğramıyor). "İki katman ayrı test edilmiş, aralarında
dallanan mantık yok" gerekçem yanlıştı çünkü gerçekte dallanan bir mantık VARDI (approve vs deny),
ben bunu görmemiştim.

**Düzeltme (`10bf0fa`):** `claim_pending_confirmation()` artık pop ettiği kaydın yaşını
`approval_ttl_sec` ile karşılaştırıyor (HMAC-imzalı `ExecutionRequest`'in kendi kullandığı AYNI
pencere) — süresi dolmuşsa kayıt yine de sözlükten siliniyor (leak guard anlamlı kalsın diye) ama
None dönüyor, çağıran taraf normal yeni tur olarak işliyor. `has_pending_confirmation()`
(artık hiçbir production çağrı sitesi kullanmıyor) TTL-farkında YAPILMADI, bunun yerine
docstring'i "TTL kontrolü yapmaz, bunun için claim_pending_confirmation()'ı kullan" diye
netleştirildi — davranışını değiştirmek bu düzeltmeyle ilgisiz testlere dokunma riski taşıyordu.
Yeni testler `test_pending_confirmations_ttl.py`'de: süresi dolmuş, hiç süpürülmemiş bir kaydın
reddedildiğini VE sözlükten silindiğini kanıtlıyor.

**İkinci, daha düşük önemli bulgu (kod değişikliği yapılmadı, belgelendi):** `resume_and_stream()`'in
`pre_claimed` parametresi runtime'da tek-kullanımlık olarak ZORLANMIYOR — düz bir dict, tüketildi
bayrağı yok. Doğru ama gerçek bir risk değil: her gerçek çağrı sitesi `claim_pending_confirmation()`'dan
tam bir kez alıp tam bir kez kullanıyor, hiçbir canlı yol aynı nesneyi ikinci kez geçirmiyor; bu
sözleşme ihlal edilseydi bile grafiğin kendi idempotency journal'ı (`test_replayed_already_
committed_execution_is_denied`) ek bir savunma katmanı. Spekülatif tek-kullanımlık enforcement kodu
eklenmedi — hiçbir canlı çağrı yolunun tetikleyemediği bir senaryo için orantısız görüldü.

**Test/lint (2026-07-23, TTL düzeltmesiyle):**
```powershell
python -m pytest -q       # 1273 passed, 0 failed (206s) — FULL suite
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py scripts/alpha_gate.py   # All checks passed! (ruff 0.15.21)
```

### Tur 3: CI'da canlı yakalanan, ilgisiz üçüncü bir sorun — ruff sürüm kayması (kök nedeniyle düzeltildi)

Tur 2'nin push'unun CI'ı `python` job'ında KIRILDI — ama pytest değil, `ruff check` adımı, 672
yeni bulgu ile (bu oturumun hiç dokunmadığı dosyalarda: `jarvis/__main__.py`, `jarvis/agent.py`'nin
eski satırları vb.). Kök neden doğrudan doğrulandı, tahmin edilmedi: `ci.yml`'nin `pip install ruff`'ı
pin'siz — `ruff 0.16.0` tam bu aralıkta PyPI'a çıkmış ve `pyproject.toml`'da açık bir
`[tool.ruff.lint].select` olmadığı için hangi kuralların uygulandığını değiştirmiş. `ruff==0.16.0`'ı
izole bir `pip install --target` dizinine kurup AYNI ağaca karşı çalıştırarak AYNI 672 hatayı yerelde
tekrar ürettim; `ruff==0.15.21` (bu oturumun fiilen kullandığı sürüm) aynı ağaca karşı temiz.
Electron'un `vitest`/`vite`/`esbuild` kaymasıyla (`0687772`) BİREBİR aynı hata sınıfı — pin'siz bir
dev-tool sürümü kaymış, kod değişikliği olmadan yeşil bir CI run'ını kırmış. Düzeltme (`b850346`):
`ci.yml`'de `pip install ruff==0.15.21`.

## Commit ve push durumu

4 iş commit'i + bu kapanış docs commit'i, hepsi `langgraph-migration`'a push'landı:
- `ff357fc` — atomik `claim_pending_confirmation()` (3 transport) + yeni `/ws` testi
- `1faa2c5` — `alpha_gate.py` isolation trace + `Memory.close()` log + Electron CI blocking
- `0c85a51` — Tur 1'in docs commit'i
- `10bf0fa` — `claim_pending_confirmation()` TTL-awareness düzeltmesi + yeni testler
- `b850346` — ruff sürüm pin'i (Tur 3'ün CI düzeltmesi)

Her push öncesi `git fetch` + `git rev-list --left-right --count origin/langgraph-migration...HEAD`
ile temiz fast-forward doğrulandı (beklenmedik remote commit yok, force yok). Her commit'in kendi CI
run'ı `gh run view --json conclusion,jobs` ile doğrulandı:
- `1faa2c5` (run 30033048953): **success** — python/electron success, mobile bilinen kozmetik fail.
- `0c85a51` (run 30033909099): **success** — aynı dağılım.
- `10bf0fa` (run 30041168858): **failure** — yalnızca `python`'ın `ruff` adımı (Tur 3, yukarıda),
  `electron` yine success.
- `b850346` (run 30041676158): **success** — python/electron success, mobile bilinen kozmetik fail.

Kalıcı kural gereği bu kapanış docs commit'inin kendi SHA'sı/CI'ı burada yok — uç CI'ına
`gh run list --branch langgraph-migration` ile bakın. Oturum sonunda local == origin senkrondu.

### Önceki oturumların özeti (17-18, detay: CHANGELOG.md)

17. oturum Faz 8'i ve Electron'un L3 confirmation UI'ını inşa etti, sonra bağımsız bir review'ın 4
bulgusunu doğrulayıp düzeltti — hepsi commit'lenip push'landı (`7d6a7af`..`0687772`, docs `174f8c2`),
CI'da yeşil doğrulandı. 18. oturum bu review'ın kendi diff'ini bağımsız doğrularken cross-transport
fix'in atomik olmadığını + 3. korumasız çağrı sitesini buldu, uncommitted bıraktı — 19. oturum
(yukarıdaki 3 tur) o işi commit'leyip push'ladı ve kendi sürecinde 2 gerçek düzeltme daha buldu.

## SONRAKİ OTURUM — kalan iş

1. **CANLI HUD E2E'si (değişmedi):** server + Electron + gerçek APPROVE/DENY tıklaması —
   TTL-awareness düzeltmesi dahil, atomik claim'in gerçek kabul testi. Test etmek için: sesle bir
   L3 aksiyon başlat, onaylamadan `approval_ttl_sec` süresini bekle, SONRA alakasız bir şey söyle —
   düzeltmeden önce bu yutulurdu, şimdi yeni bir tur olarak işlenmeli. Ayrıca eşzamanlı bir ikinci
   onay denemesinin (HUD + ses aynı anda) yalnız BİRİNİN kazandığını da doğrulamak gerekir.
2. **Sistemik risk, gözlem (yeni bu oturumdan):** iki ayrı pin'siz dev-tool sürümü (electron'un
   `vitest`, şimdi Python'ın `ruff`) art arda CI'ı kırdı, ikisi de "aynı jenerasyonu hedefleyen bir
   sürüme pinle" ile düzeltildi. CI'ın kurduğu HERHANGİ bir başka pin'siz araç var mı diye bir
   tarama (`ci.yml`'nin tamamı) owner onayıyla ayrı bir oturumun işi olabilir — bu oturumun
   kapsamına girmedi, yalnızca gözlem olarak not edildi.
3. **Model tool-calling güvenilirliği** (14. oturumdan; taksonomiyle ölçülebilir):
   `false_success_claim`'i 0'a indirme — alpha kapısının asıl kilidi.
4. Alpha gate'in owner-koşusu ölçümleri (10-run `ab_run_config.ps1` + `isolation`) + 2 kapsama
   boşluğu senaryosu (uzun-workflow E2E, block/veto-dışı recovery sınıfları).
5. Branch ucunun CI'ı: `gh run list --branch langgraph-migration -L 3` ile kontrol et (bu
   dosyanın kendi kapanış commit'i dahil).
6. **Yeni, küçük, bilinçli kapsam dışı bırakılan:** `Memory()` inşa eden DİĞER test dosyaları hâlâ
   `close()` çağırmıyor (zararsız, sızıntı pytest bitince temizleniyor) — repo-geneli bir tarama
   yapılmadı, ayrı bir oturumun işi olabilir. `electron/`'da `npm audit` 8 önceden-var advisory
   gösteriyor — kırıcı sürüm atlamaları gerektiriyor, bilinçli olarak ertelendi.
7. Eski kalanlar (değişmedi): `workflow_start` JSON `steps` güvenilirliği; gerçek CLI REPL E2E;
   Faz 6 Kısım 3 Literal-terfi; Faz 5 kalanları; canlı A/B B6 sorusu; 4 worktree branch; mobile
   flutter-analyze info/warning.

## Değişmeyen taşınan işler

- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Pre-first-turn kozmetik model label — değişmedi.
