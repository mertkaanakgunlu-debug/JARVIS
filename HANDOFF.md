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

## Son oturum: 2026-07-31 — Post-MVP **Faz 2: Clock + temporal + entity**

**Durum tek cümlede:** takvim tarih hatası kapandı (24 saatin 3'ünde yanlış → **0'ında**), ve
"tarihi/saati/ismi kim çözüyor?" sorusunun cevabı artık **kod**, model değil.

## Plan ve önceki faz

Plan: `C:\Users\mertk\.claude\plans\c-users-mertk-downloads-jarvis-post-mvp-federated-kettle.md`
(Faz 0A + 0B + 1 önceki oturumlarda bitti). Sıra: **2.5** otomatik rol seçimi → 3 Sabah Brifingi →
4 Working Set → 5 proaktif → 6 hafıza → 7 render/harita → 8 web_download → 9 Kimi K3.

## Hata önce ölçüldü, sonra düzeltildi

`calendar.py:_parse_date` "yarın"ı `datetime.now(timezone.utc)` ile çözüyor, sonuçtaki naive
duvar-saatini Google'a `timeZone: Europe/Istanbul` ile yolluyordu. UTC'nin hâlâ önceki günde olduğu
saatlerde bu **tam bir gün** hata — gerçek bir gün, gerçek bir etkinlik, hiç hata mesajı yok.

Düzeltmeden **önce** gerçek `_parse_date`'e karşı 24 yerel saatin tamamı süpürüldü: **24'te 3**
yanlış (İstanbul 00:00, 01:00, 02:00). Düzeltilmiş yoldan aynı süpürme: **24'te 0**.
`tests/test_calendar_date_regression.py` üç bozuk saati değil **süpürmenin tamamını** koşuyor —
hatayı başka bir saate taşıyan bir "düzeltme" dar bir testi geçerdi.

**HANDOFF'un kendi kaydı düzeltildi:** önceki sürüm regresyon anını
`FrozenClock(2026-07-31 23:30 Europe/Istanbul)` diye yazmıştı. **O an hatayı üretmiyor** —
İstanbul'da 23:30, UTC'de aynı günün 20:30'u. Pencere **00:00–02:59 yerel**.

## Ne yapıldı

1. **`jarvis/clock.py`** — "bugün ayın kaçı" sorusunun **tek** cevabı. `SystemClock`/`FrozenClock`,
   `JarvisAgent.__init__`'te `settings.calendar_timezone`'dan bir kez ayarlanıyor. Now-block'un
   tzdata fallback'i buraya taşındı. `scheduler.py` ve `todo_store.py` de buraya bağlandı: onlar
   çıplak `datetime.now()` ile **işletim sisteminin** saat dilimini okuyordu. Bu makinede ikisi
   aynı, yani gözle görülür bir yanlış yoktu — *"iki bağımsız ayar tesadüfen uyuştuğu için doğru"*
   tam olarak bu modülün ortadan kaldırdığı hata şekli.
2. **`jarvis/nlu/temporal.py`** — Türkçe/İngilizce tarih-saat çözümleyicisi. `öğlen 3` = 15:00
   (owner'ın canlı hatasının tam ifadesi). Gün değil **dönem** adlandıran ifadeler (`haftaya`)
   tahmin edilmiyor, gerekçesiyle **reddediliyor**.
3. **`jarvis/nlu/entities.py`** — "Baranla → Baranda" sınıfı. Güvenli kılan kural: bir kök **ancak
   bir kaynak doğrularsa** benimseniyor. Kör ek atma "Metin"i "Met" yapardı — düzeltilen hatadan
   beteri.
4. **`jarvis/nlu/event_text.py`** — takvim **kaydı** tutar, isteği değil. Asıl değerli kısım
   **çıkarmadığı** şeyler: erken bir sürüm "Cuma raporu"ndaki "Cuma"yı yemişti.
5. **Güvene dayalı takvim onayı** (`policy_guard`) — tek `create` + yüksek güven → sormadan çalışır;
   belirsizse sorar; `batch_create`/`update`/`delete` **her zaman** sorar.

## Bu fazın en önemli bulgusu — gate yanlış şeyi puanlıyordu

Gate **araç argümanlarını** puanlıyordu. Argümanlar isteğin kendisi değil, **modelin yorumu**.

*"Pazartesi saat 4'te spor salonu diye takvime bir şey ekle"* denince gerçek qwen3:8b, docstring'in
"kelimeyi olduğu gibi geçir" talimatını yok sayıp hafta gününü **kendi** çözdü — bir **Cumartesi**'ye
— ve ISO tarih yolladı. Argüman bazında bu **1.00** güven demek: **10/10 tur, yanlış güne, hiç
sorulmadan etkinlik oluşturdu.**

Bunu **2235 birim testi ve 40/40 mutasyon turu bulamazdı** — mekanizma doğruydu, yanlış girdiye
bakıyordu. Gate artık `state["user_query"]`'i de okuyor (güveni yalnız **düşürebilir**) ve
kanıtlanabilir çelişkide (kullanıcının andığı gün ≠ verilen tarihin günü) doğrudan blokluyor.

## Canlı ölçüm (gerçek qwen3:8b · Ollama · CLOUD_POLICY=off · **sahte** Google servisi)

Ayrı `JARVIS_HOME` + Calendar servisi yerine capture nesnesi: **her iki koşuda da 0 gerçek Google
çağrısı**. n=10/senaryo. Saat 2026-08-01 01:30 İstanbul'a donduruldu — bilerek eski hata
penceresinin içinde.

| Senaryo | Düzeltmeden ÖNCE | SONRA |
|---|---|---|
| *"Pazartesi saat 4'te spor salonu … ekle"* | **10/10 sorulmadan, hepsi Cumartesi'ye** | **0/10 sorulmadan** ✅ |
| *"Cumaya Baran'la toplantı ekle"* | — | **0/10 sorulmadan** ✅ |
| *"Yarın öğlen saat 3'e Baran'la toplantı ekle"* | 10/10 her eksende | **10/10 oluşturuldu · 10/10 doğru tarih · 0 soru** ✅ |
| *"15 Ağustos 2026 saat 14:00'te … ekle"* | 10/10 her eksende | **10/10 doğru · 0 soru** ✅ |
| *"Gelecek pazartesi 18:00'de … ekle"* | — | **10/10 doğru Pazartesi · 0 soru** ✅ |

Son üç satır **regresyon kontrolü**: gate'i her şeyi sormaya çeviren bir "düzeltme" ilk iki satırı
"geçer" ve özelliği yok ederdi. İlk koşu ayrıca **docstring değişikliği riskini de temizledi**
(plan'ın risk kaydı: bir docstring cümlesi bir gate'i 10/10 → 0/10 yapmıştı) — üç açık senaryoda
30/30 araç çağrısı.

## Kapatılan gerçek bypass — kill switch

Bu, kod tabanında `risk_level >= 3` ile `requires_confirmation=True` denkliğini **bozan ilk**
değişiklik. Kill switch vetosu `requires_confirmation and risk_level >= 3` okuyordu; otomatik
onaylanan bir create **tetiklenmiş kill switch'in yanından geçip giderdi**. Artık yalnız
`risk_level`'a bağlı — mevcut her araç için doğrulanmış bir no-op.

Aynı denkliğe dayanan **6 mekanizma daha** varsayılmadı, otomatik-onaylanan bir çağrıya karşı
teker teker sınandı ve her birinin testi var: risk sınıflandırması (hâlâ L3/external_write), denetim
kaydı (hâlâ yazılıyor, `outcome=auto_approved` — *"sorulmadı"* asla *"kaydedilmedi"* demek değil),
`--profile test`, proaktif/arka plan turları (`interactive` transport'tan türediği için downgrade'e
hiç ulaşmıyor), workflow engine (varsayılan `interactive=False`), ve mail/Drive (*"Mail hep
onaylı"*).

## Test / lint (2026-07-31, bu oturumda çalıştırıldı)

```powershell
.venv\Scripts\python.exe -m pytest -q                        # 2235 passed, 5 deselected, 4 dk 48 sn
.venv\Scripts\python.exe -m ruff check jarvis scripts tests  # All checks passed!
npm test    --prefix electron                                # 27 passed
npm run build --prefix electron                              # 35 modül, hatasız
```

Yeni: `test_clock.py`, `test_temporal_resolver.py`, `test_entity_resolver.py`,
`test_event_text.py`, `test_calendar_date_regression.py`, `test_calendar_autonomy.py`.
Baseline 1836 + yeni testler = 2235; hiçbir eski test sessizce düşmedi (aritmetikle doğrulandı).

## Mutasyon turu — **40/40 yakalandı**, ama ilk turda 5 mutasyon **hayatta kaldı**

Her biri gerçek bir kapsam açığıydı, hepsi kapatıldı:

- Regresyon testi `_clock_for`'u komple monkeypatch ediyordu, yani **UTC hatası geri konsa bile
  geçerdi**. (Gerçek fonksiyon artık yamasız test ediliyor.)
- Clock bağımsızlık testi süreç saatini hiç oynatmıyordu.
- `calendar_control`'ün başlık temizleyiciyi gerçekten **çağırdığına** dair uçtan uca kontrol yoktu.
- Boş-başlık koruması hiç tetiklenmiyordu (yalnız noktalama-only girdi oraya ulaşıyor).
- Biri gerçek bir **eşdeğer mutant**tı (araç kontrolünü tek başına genişletmek davranışı
  değiştirmiyor, çünkü başka hiçbir aracın `create` action'ı yok) — davranışı gerçekten değiştiren
  bir varyantla değiştirildi. Onu incelemek saklamaya değer bir şey ortaya çıkardı: `shell_run`,
  `python_run` ve `workflow_start`'ın **hiç args şeması yok**, yani modelin takvim biçimli
  argümanları bir shell komutuna iliştirmesini durduran tek şey araç-adı kontrolü.

## Faz 2'den taşınan, bilinçli olarak yapılmayanlar

- **Entity resolver kuruldu ve test edildi, ama HİÇBİR canlı yola bağlanmadı.** Bu, üstteki
  Contacts kararının dürüst sonucu: Contacts kapalıyken çekimli bir formu otomatik düzeltebilecek
  tek kaynak yok, yani takvim yolunun ondan alacağı her cevap "dokunma" olurdu — gözlenebilir
  davranışı olmayan bir kod yolu eklemek olurdu. Faz 7'nin workflow runtime'ındaki Part 1/Part 2
  ayrımının aynısı. **Bitirmek için:** owner Contacts'ı açar (bir yeniden onay), sonra "sor" bandı
  zaten var olan onay istemi üzerinden yüzeye çıkar.
- **Google Contacts kapalı** (`google_contacts_enabled=False`). People API `contacts.readonly`
  scope'u istiyor; mevcut credentials'a eklemek çalışan calendar/gmail token'ını geçersiz kılardı.
  Kendi token dosyası var (açmak eklemeli), ama **bir OAuth yeniden onayı** gerekiyor — owner kararı.
- **`utterance` kontrolü modelin sadakatini denetlemiyor.** Belirsiz bir isteğin kendinden emin
  argümanlara aklanmasını yakalıyor; hiç zamansal belirsizlik içermeyen bir cümlede uydurulan bir
  detayı yakalayamaz.
- **Hafta günü çapraz kontrolü yanlış-pozitif yönünde hata yapar.** Cümlede başka tarih ifadesi
  yoksa, isim olarak kullanılan bir gün adı bir onay istemine mal olur. Güvenli yön, bedelsiz değil.
- **`_all_day_end` Google'a karşı canlı doğrulanmadı** — API sözleşmesinden türetildi (end.date
  exclusive), credential olmadığı için gerçek istekle sınanmadı. Yalnız sonu ileri alabildiği için
  her iki durumda da güvenli yazıldı.

## Oturum sonu

**6 iş commit'i ve bu kapanış HANDOFF commit'i.** Yapı bilinçli olarak bağımsız ve tek tek geri
alınabilir: clock → nlu → calendar düzeltmesi → policy gate → nlu refactor/dürüstlük düzeltmesi →
docs. Bağımlılıklar ileri akıyor, yani ara commit'lerin hiçbiri kırık bir ağaç bırakmıyor.
Oturum sonunda **local == origin senkrondu**.

## SONRAKİ OTURUM — Faz 2.5: otomatik rol seçimi

Plan dosyasındaki Faz 2.5. Ölçüm zaten planda: `fast` ≈ 0.9 sn / 19 token, `reasoning` ≈ 12–33 sn /
524–1360 token, ve `_route_query()` (agent.py:262) **konuşma dışı her şeyi** `reasoning`'e yolluyor
— *"bugünkü takvimimi göster"* gibi tek-araçlık iş bile 30 saniye sürüyor.

Bu oturumun canlı ölçümünden gecikme verisi (hepsi **aynı** rolde koştu, yani bu sayılar rol
seçimini değil **istek zorluğunu** ölçüyor — Faz 2.5'in kazancının kanıtı değil, sadece bugünkü
taban): açık takvim istekleri p50 18.9–23.4 sn, belirsiz istek p50 78.9 sn. Faz 2.5'in kendi
ölçümü rolleri ayırarak yapılmalı.

## Önceki oturumlardan taşınan, değişmeyen işler

- Faz 1'den: **`enforce_reversible` açılmadı** (varsayılan `shadow`); terfi
  `rollout.enforce_gate_status()`'a bağlı — 100 gerçek artifact işlemi, 0 bildirilmiş yanlış blok.
  Ek terfi engeli: akan bir taslağı geri alamama. Doğrulama 5 araçta, 36'da değil.
  `EvidenceSet.facts` boş.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- 7 direct-Gemini modülün shared gateway'e migrasyonu.
- Electron `npm audit`; canlı HUD E2E'nin **Electron penceresi** ayağı.
- Alt+Space → `/voice/ptt/start`; wake-word modeli; alpha gate'i GEÇTİ'ye taşımak.
- Finans: boru hattı çalışıyor, tek gerçek kaynak PDF ekstre importu.
- **CI:** `gh run list --branch langgraph-migration` ile **job düzeyine** bakın
  (`gh run view <id> --json jobs`). `mobile` job'ı `continue-on-error: true` (owner kararı,
  2026-07-23) ve **en az 2026-07-25'ten beri başarısız** — 71 bulgunun tamamı info/warning.
  Kod regresyonu değil, bloke etmiyor. **Owner kararı bekliyor.**
- **CI teşhis notu — `chromadb: no such table: acquire_write` bir FLAKE'tir, regresyon değil.**
  2026-07-31'de `python` job'ı bu hatayla kaldı ve **kaldığı commit sadece docs'tu** (`b0d4725`);
  bir önceki ve bir sonraki koşu aynı kodla geçti. `requirements.txt`'te `chromadb>=0.6` **pinsiz**.
  **Yeniden görülürse önce koşuyu tekrarlayın**, kod aramayın.
