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

## Son oturum: 2026-08-02/03 — **Faz 5 hazırlığı** + **Faz 5 (mail → takvim)**

**Durum tek cümlede:** dış inceleme (GPT, dalın gerçek ucunda) Faz 4'ün **8 yapısal açığını**
buldu — 8'i de doğrulandı ve kapatıldı — ardından **Faz 5 kuruldu**: mail'den takvim etkinliği,
ama *doğrudan yazma değil*, **onaylanabilir öneri** olarak.

**Commit yapısı:** iki iş commit'i (`fix(faz5-prep)`, `feat(faz5)`) ve bu kapanış HANDOFF
commit'i. İkisi ayrı tutuldu çünkü hazırlık Faz 5 olmadan da tek başına doğru ve gözden
geçirilebilir; her ikisinin ağacı da kendi başına yeşil.

**Ölçüm durumu, dürüstçe:** Faz 5'in tamamı **fixture** üzerinde doğrulandı (42 test).
**Gerçek posta kutusunda hiç koşmadı** — owner OAuth onayı gerekiyor. Arka plan ingestion
varsayılan **KAPALI**.

## Sıra ve plan

Plan: `C:\Users\mertk\.claude\plans\c-users-mertk-downloads-jarvis-post-mvp-federated-kettle.md`
(Faz 0A + 0B + 1 + 2 + 2.5 + 2.75 + 3 + 4 + **hazırlık** + **5** bitti).
**Sıra: canlı posta kutusunda ölçüm** (aşağıda), sonra Faz 6.

---

# Faz 5 — mail → takvim (bu oturumun ikinci yarısı)

## Kurulmayan akış

```
yeni mail geldi → model → Google Calendar create        ← BU DEĞİL
```

Arka planda çalışan bir model çağrısının yanlış günü seçtiğini görecek kimse yok. Onun yerine:

```
message_id → fetch → deterministik çıkarım → doğrulama → dedup
           → calendar_candidate (working set) → KULLANICI ONAYI → takvim create
```

## Dört karar, her biri bu repo'nun yaşadığı bir hatanın cevabı

- **Araç yalnız `message_id` alıyor.** Gönderen/konu/tarihi modele yeniden yazdırmak, Faz 4'te
  grafiğin rengini kaybettiren ve `'Tarih'` diye olmayan bir kolon uyduran şeyin aynısı. Servis
  maili kanonik kimlikle kendi okuyor.
- **Çıkarım LLM çağırmıyor.** Faz 3 ölçtü: bildiğini sandığı bir olguyu model 5'te 4 uyduruyor
  (`weather`). Maildeki tarih tam olarak o cins bir olgu. `nlu/temporal.py` + `nlu/event_text.py`
  çözüyor — takvim aracının kendi kullandığı çözücüler, aynı güven bantları.
- **Güven kaydediliyor, harcanmıyor.** 0.99 bile etkinlik değil, **aday** üretiyor. Güvenilir
  gönderen için otomatik oluşturma bilinçli olarak **yapılmadı**: önce ölçüm gerekiyor, ölçüm için
  de bu ledger'ın var olması gerekiyordu.
- **Toplantıdan söz etmeyen mail hiçbir şey üretmiyor.** Metindeki tarih randevu değil (fatura
  vadesi, bültendeki "2019'dan beri"); bu olmadan özellik takvimi reklam mailiyle dolduran bir
  makine olurdu.

## `mail_event_candidates` — kalıcı ledger

`message_id` **primary key**; bu bir ipucu değil, idempotency garantisi: retry, restart veya
ikinci bir kullanıcı isteği yeni etkinlik değil, **var olanın id'sini** döndürüyor. Durumlar:
`new → extracted | ambiguous → proposed → confirmed → created | ignored | error`.
**`error` terminal DEĞİL** — kötü bir sebeple düşen mailin tekrar denenmesi bu tablonun varlık
sebebi.

Bu, `monitor._notified_email_ids` **değil** ve olmamalı: o küme iş başarısız olsa bile ekliyor ve
açılışta tüm okunmamışları "görülmüş" sayıyor. Toast spam'i için doğru, işleme kaydı olarak ölümcül.
Ingestion ayrıca proaktif kıskacın **dışında**: 10 dk **bildirimi** sınırlar, aday üretimini değil.

## Working Set'in ikinci tüketicisi

Faz 4 kind-agnostik store'u tek tüketiciyle bırakmıştı. `calendar_candidate` ikinci kind ve
`create`/`activate`/prompt-injection yolundan **değişiklik olmadan** geçiyor. Aktif bir aday varken
çıplak *"evet, ekle"* `calendar` domain'ine yönleniyor — bu olmasa o tur `conversation`'a düşer,
model sıfır araç alır ve var olmayan bir etkinlik için *"tamam, ekledim"* der.

## Kapı

`calendar_from_mail` **tool düzeyinde L3 / external_write / requires_confirmation**; yalnız
`propose` L1 `external_read` olarak ayrıldı. Katı varsayılan bilinçli: sonradan eklenen ve tabloya
yazılmayan bir action **tehlikeli** sınıfı devralır — bunun testi var. `create` her yolda gerçek
bir interrupt, proaktif yol dâhil.

**Yan değişiklik:** `calendar.create_event()` artık yapılandırılmış `EventCreation` döndürüyor,
`_format_creation()` render ediyor. Mail akışının yeni etkinliğin id'sine ihtiyacı vardı ve tek yol
gösterim string'ini regex'lemekti — `gmail.message_fields`'in bitirmek için yazıldığı hatanın
aynısı (BUG-15). Render edilen metin **bayt bayt aynı**.

---

# Kapatılan sekiz bulgu

Hepsi `langgraph-migration` ucunda (`f4d0989`) doğrulandı. İnceleme dalın **162 commit** ileride
olduğunu doğru saymış — CLAUDE.md'deki "136" bayat; **sayıyı okumayın, türetin:**
`git rev-list --left-right --count origin/main...origin/langgraph-migration`.

## P0 — background görev YANLIŞ konuşmanın tool context'iyle koşuyordu

`background_turn()` girişte `origin_session_id`'yi sabitliyor ve bellek bağlamı, working-set
prompt bloğu ve geçmiş append'i için onu kullanıyordu. **`RunnableConfig` kullanmıyordu** —
`thread_id` ve `conversation_id` hâlâ `self.session_id` okuyordu. A konuşmasında kuyruğa giren bir
görev, kullanıcı B'ye geçtiğinde modele **A'nın** working set'ini gösteriyor, config okuyan her
araca (`chart_revise`, `working_set`) **B'nin** kimliğini veriyordu: araç ya B'nin grafiğini
düzenliyor ya da prompt'un az önce anlattığı nesne için *"bu nesne bu konuşmaya ait değil"* diyordu.
`proactive_turn()`'de aynı ayrışma vardı, aynı düzeltmeyi aldı.

## Proaktif çalışma artık foreground'u dondurmuyor

- `proactive_turn()` `_state_lock`'ı **tüm `ainvoke()` boyunca** tutuyordu. Proaktif turlar 20-80 sn
  ölçülüyor ve `chat()`/`chat_stream()` aynı kilidi bekliyor — yani rutin bir "yeni mail geldi"
  kontrolü canlı konuşmayı bir dakika dondurabiliyordu. Kilit artık yalnız kurulumu kapsıyor;
  `background_turn()` zaten bu çizgiyi çiziyordu.
- `monitor._maybe_proactive()` monitörün **tek polling thread'inde** `asyncio.run(...)` çalıştırıyordu;
  takvim/scheduler/todo/finans/GCP kontrolleri model çağrısı boyunca bekliyordu. **Ölçüldü:
  düzeltmeden önce 10.0 sn bloke, sonra saniye altı.** Artık sınırlı (maxsize=4) kuyruk + kendi
  worker'ı; kuyruk dolduğunda **log'layarak** düşürüyor.

## Working Set spec bütünlüğü

| bulgu | kullanıcının bir cümlede ulaştığı hâli |
|---|---|
| `hue`/`color` dışlaması yalnız redraw yolunda | *"rengi kırmızı yap"* gruplu grafikte **ikisini birden** saklıyordu; renderer `hue` varken `color`'ı yok sayıyor — grafik değişmiyor, prompt her tur değiştiğini söylüyordu |
| başlık iki yerde | başlık revizyonundan sonra header eski, spec yeni başlığı gösteriyordu — model hangisinin gerçek olduğunu bilemezdi |
| alan silinemiyordu | store ilk günden `None = sil` okuyor; model tarafında ifade edilemiyordu → *"başlığı kaldır"*, *"gruplamayı kaldır"* imkânsızdı |
| `undo` önce yazıp sonra çiziyordu | render hatasında store bir sürüm geri, ekrandaki PNG yeni sürüm — **ayrışma** |
| düzenlenen nesne aktif olmuyordu | `chart_revise(object_id=B)` A'yı aktif bırakıyordu; `active(kind)` `is_active`'i `updated_at`'ten önce sıraladığı için sonraki *"şimdi başlığını da değiştir"* sessizce A'ya gidiyordu |

Tüm mutasyonlar artık tek `canonicalize_chart_patch()`'ten geçiyor; `clear_fields` argümanı eklendi.
`undo` **çizip sonra commit ediyor**.

## Grafik kimliği ve depolama

- Kimlik artık **`(source, sheet, x, y)`**, source kanonikleştirilmiş. `sheet` anahtara girdi çünkü
  bir workbook'un Ocak/Şubat sayfaları aynı kolon adlarını taşıyor ve tek, kendini ezen grafik
  çiziyordu; `satis.csv` / `./satis.csv` / mutlak yol üç ayrı grafikti. Redraw artık **inactive**
  grafiklerle de eşleşiyor (eskiden yalnız aktif olanla — kopya doğuruyordu).
- **Inline `data_json` grafikleri revize edilebilir.** `source=""` ile kaydediliyorlardı — bu
  workspace **dizinine** çözülüyor — yani working set'e "düzenlenebilir" diye girip ilk revizyonda
  düşüyorlardı. Frame artık PNG'sinin yanına yazılıyor (`_source_type=inline_materialized`).
- **`default_store()` artık gerçekten tek store.** Her çağrıda yeni store + yeni SQLite bağlantısı
  üretiyordu ve hiçbiri kapatılmıyordu; agent'ın store'u ile araçlarınki aynı dosya üstünde iki ayrı
  nesneydi. Memoize edildi, `make_tools()` agent'ın instance'ını alıyor.
- **Başarısız grafik kaydı artık log'lanıyor.** Sessizce `None` dönüyordu — kullanıcıya tüm özelliği
  kaybettiren, izi olmayan bir hata. **Bu iş sırasında tam o satırın arkasına bir wiring hatası
  saklandı** (aşağıya bakın).

---

# Bu oturumda öğrenilen iki şey

**1. `make_tools()` içinde `@tool` isim gölgeliyor.** Enjekte edilen store parametresine önce
`working_set` adını verdim; `@tool def working_set(...)` o adı fonksiyon kapsamında **StructuredTool
ile yeniden bağlıyor**, yani closure store yerine aracı okuyordu. `register_chart`'ın
`except Exception: return None`'ı bunu sessizce yuttu: grafik çiziliyor, working set'e hiçbir şey
girmiyor, sonraki her revizyon *"düzenlenecek bir grafik yok"* diyor — **hiç iz yok.** Parametre
`working_set_store` olarak adlandırıldı ve o `except` artık `logger.warning(exc_info=True)` yazıyor.

**2. Router ölçümünü GERÇEK araç listesiyle yapın.** İncelemenin *"`file_write` görünüyorsa bu
hatayı tamamen model-side sayamazsınız"* hipotezini test ettim. İlk ölçümüm `sorted(TOOL_SPECS)`
besledi ve `file_write` **subset'te çıkmadı** — hipotezi çürüttüm sandım. `make_tools()`'un
**gerçek** çıktısıyla (42 araç, farklı sıra) tekrar ölçünce `file_write` **subset'e giriyor**.
Kayıt için: **inceleme haklı**, benim ilk ölçümüm yanlıştı — `_by_relevance` sıralaması araç
listesinin sırasına duyarlı.

```
turn 0 gerçek subset: ['csv_read','file_read','file_write','file_list',
                       'data_analyze','plot_data','chart_revise','working_set']
```

Yani Faz 4'ün *"CSV'yi yeniden yazdı"* hatası **tamamen model tarafı değil**: salt-okuma/çizim
isteğinde sisteme kaynak veriyi değiştirme yeteneği görünürdü.

**Ama incelemenin önerdiği filtrenin naif hâli özelliği kırar:** `plot_data` ve `chart_revise` de
`side_effect_type="local_write"` taşıyor (`file_write` ile aynı). "Okuma isteğinde yazma araçlarını
gizle" kuralı **grafik çizmeyi de kapatır**. Gereken ayrım *yazma/okuma* değil, **"yeni artifact
üretir" vs "kullanıcının kaynak verisini değiştirir"** — bu, Faz 5'te tasarım işi, hazırlıkta
yamalanacak bir şey değil.

---

## Doğrulama (2026-08-02/03, bu oturumda çalıştırıldı)

```powershell
.venv\Scripts\python.exe -m pytest -q                        # 2853 passed, 5 deselected, 6 dk 18 sn
.venv\Scripts\python.exe -m ruff check jarvis scripts tests  # All checks passed!
.venv\Scripts\python.exe scripts\plot_intent_ab.py --runs 5  # 2×2 pilot, 20 canlı tur
```

Suite 2739 → **2853** (bu oturumda +114 test, 8 yeni dosya). **Her düzeltme, düzeltme olmadan düşen
bir testle bağlandı** — hazırlığın P0'ı ve monitör için bunu geri-alma koşusuyla fiilen doğruladım
(P0: 2 test düştü; monitör: *"blocked for 10.0s"*).

Faz 5 ayrıca **gerçek `@tool` nesnesi üzerinden uçtan uca** elle koşuldu (sahte Gmail/Calendar):
propose → **0 takvim yazımı**, create → 1, tekrar create → hâlâ 1.

Yeni test dosyaları:
```
tests/test_background_conversation_ownership.py   # P0 + proaktif kilit
tests/test_working_set_integrity.py               # hue/color, başlık, clear_fields, undo, aktiflik
tests/test_chart_tool_wiring.py                   # inline revize + enjekte store (gerçek @tool'lar)
tests/test_monitor_proactive_queue.py             # kuyruk, düşürme, worker dayanıklılığı
tests/test_calendar_from_mail.py                  # çıkarım, staged akış, idempotency, retry, restart
tests/test_calendar_from_mail_gating.py           # L1/L3 ayrımı, fail-closed, yönlendirme, monitör
tests/test_revision_gate_scoring.py               # eligibility, N/A, outcome-vs-coverage
```

**Yeni eval altyapısı:** `jarvis/evals/revision_scoring.py` (saf skorlama — script'in import
yan etkileri olmadan pytest'lenebilir), `jarvis/evals/results.py` (kalıcı, zaman damgalı,
atomik ham çıktı → `.eval-results/`), `scripts/plot_intent_ab.py` (2×2 harness).

**KURAL (hâlâ geçerli):** canlı harness (`revision_gate.py`, `briefing_gate.py`) başka hiçbir
şeyle aynı anda koşulmaz.

---

# Faz 4 kapısı yeniden ölçüldü (2026-08-03) — **GEÇMEDİ, ve kötüleşti**

`revision_gate.py --runs 5`, qwen3:8b, `cloud_policy=off`, tek başına koştu.
**Tam zincir: 5'te 1.** Hipotezim — kimlik düzeltmelerinin kapıyı iyileştireceği —
**desteklenmedi.**

**ÖNEMLİ — bu tablo bir kez yanlış raporlandı ve düzeltildi.** İlk hâli her adımı 5 paydayla
yazıyordu; oysa grafik yalnız **2 zincirde** oluştu ve grafiği olmayan üç zincirde
`spec == previous == {}` olduğu için "dokunmamalı" ve "undo" adımları **boşuna geçiyordu**.
Aşağıdaki sayılar `jarvis/evals/revision_scoring.py` ile yeniden hesaplandı:

| tur | demonstrated | eligible | N/A | raw (yalnız teşhis) |
|---|---|---|---|---|
| oluştur → nesne var | **2/5** | 2/5 | 0 | 2/5 |
| renk | 1/5 | 1/2 | 3 | 1/5 |
| başlık | 2/5 | 2/2 | 3 | 2/5 |
| tür | 2/5 | 2/2 | 3 | 2/5 |
| ilgisiz soru — dokunmamalı | 2/5 | 2/2 | 3 | *(5/5)* |
| "Teşekkürler" — dokunmamalı | 2/5 | 2/2 | 3 | *(5/5)* |
| geri al | 2/5 | 2/2 | 3 | *(5/5)* |

`GATE OUTCOME: FAIL` · `COVERAGE: INSUFFICIENT` — iki ayrı eksen: davranış başarısız **ve**
yeterince gözlenmedi.

**Önceki koşuyla karşılaştırma YAPILAMIYOR.** O koşunun ham JSON'u diskte yok (`--out`
göreliydi ve script `os.chdir(SCRATCH)` yapıyor), yani eski sayılar aynı düzeltilmiş kuralla
yeniden hesaplanamaz. *"undo 4/5 → 5/5 iyileşti"* iddiası bu yüzden **geri çekildi**, tashih
edilmedi.

**Zincir 0. turda ölüyor.** Başarısız 3 zincirin hiçbiri `plot_data`'yı **çağırmadı**:
`file_list`/`csv_read` çağırıp durdular, `spec={}`. Sonraki adımların düşük çıkması bir
revizyon sorunu değil — 2/5 tavanın altında kalmaları. **Nesne oluşan 2 zincirin 1'i
7/7 tam doğru.**

## Ölçümün bulduğu gerçek hata (düzeltildi)

Nesne oluşan zincirlerden biri, grafiği çizdikten hemen sonra **var olan bir nesne için**
*"düzenlenecek bir grafik yok"* aldı. Sebep: model `object_id="chart:9fd36f"` gönderdi — yani
**sistemin ona gösterdiği string'in aynısı**. `WorkingObject.ref`'in docstring'i onu
*"araç çağrısında modelin onu böyle adlandırdığı"* diye tanımlıyor, `render()` her tur prompt'a
basıyor, ama `get()` tüm string'i primary key olarak arıyordu. `working_set_control`'ün
`activate` dalı Faz 4'ten beri prefix'i elle soyuyordu — yani kod tabanı bunu **biliyordu**,
sadece revizyon yoluna uygulanmamıştı.

Normalizasyon artık **store'da** (`WorkingSetStore.bare_id`), her tüketici için: ileride
`email`/`report`/`table` araçları aynı boşluğu yeniden açamaz. 4 regresyon testi; fix olmadan
3'ü düşüyor.

## Araç sırası hipotezi ÖLÇÜLDÜ — ve çürüdü (2026-08-03)

Hipotez şuydu: `plot_data` alt kümede 6. sırada göründüğü için model dosyayı okuyup duruyor.
`scripts/plot_intent_ab.py --runs 5` ile 2×2, yalnız 0. tur, 20 canlı tur, dengeli blok
sırası (`A B C D / B C D A / ...`). Müdahale **yalnız harness'ta** — üretim kodu değişmedi.

| arm | sıra | `file_write` | nesne oluştu | `plot_data` çağrıldı |
|---|---|---|---|---|
| A (kontrol) | mevcut | var | 4/5 | 4/5 |
| B | `plot_data` öne | var | 3/5 | 3/5 |
| C | mevcut | yok | **5/5** | 5/5 |
| D | `plot_data` öne | yok | **0/5** | 1/5 |

```
object_created   sıra ana etkisi -0.60 | file_write presence effect -0.20 | etkileşim -0.80
plot_data_called sıra ana etkisi -0.50 | file_write presence effect -0.10 | etkileşim -0.60
```

**Sırayı öne almak yardım etmiyor, zarar veriyor** — ve `file_write` kaldırmasıyla birleşince
yıkıcı (D = 0/5). Mekanizma D'nin cevap metinlerinde görünüyor: model `plot_data`'ya **daha
erken uzanıyor ama argümanları eksik** — *"`path` veya `data_json` parametresi eksik"*,
`[INVALID_ARGS:y]`. Bu çağrılar args doğrulamasında reddedildiği için `on_tool_start` hiç
ateşlenmiyor; `plot_data_called` 1/5 görünmesinin sebebi bu. Yani aracı öne almak, modelin
**dosyayı tanımadan** çizmeye kalkmasına yol açıyor. Tutarlı bir açıklama, kanıtlanmış
mekanizma değil.

**Aksiyon çıkmıyor.** En iyi hücre C (5/5) ama kontrole farkı yalnız **+1/5** — önceden
yazılmış ilerletme eşiği (≥2/5) karşılanmıyor. Doğrulama koşusu tetikleyen tek fark D'nin
−4/5'i, o da negatif bir sonuç; "D kötü"yü doğrulamanın değeri yok. **Üretim ranking'i
değiştirilmemeli.**

**İki yan gözlem, ikisi de dürüstlük notu:**

- **Kontrol burada 4/5, gate koşusunda 2/5** — aynı sorgu, aynı model. n=5 vs n=5'te bu fark
  anlamlı değil (Fisher p≈0.24), ama iki ölçüm arasındaki farkın kendisi kayda değer:
  **0. tur başarısı yüksek varyanslı.** Gate'in 2/5'ini "sistemin gerçek oranı" diye okumak
  yanlış olurdu.
- **20 turun hiçbirinde kaynak mutasyonu yok** (`content_changed`/`write_attempted`/`deleted`
  hepsi 0), `file_write` sunulan 10 turda bile. Faz 4'te bir kez gözlenen *"CSV'yi yeniden
  yazdı"* davranışı demek ki **nadir**, imkânsız değil — ama sık olduğu varsayımı desteklenmedi.

**Sıradaki hipotez (bu koşunun ürettiği):** darboğaz sıra değil, **okuma araçları arası
rekabet ve terminal tamamlama sözleşmesinin yokluğu.** Çoğu turda ilk çağrılan araç
`csv_read` değil `data_analyze`. Model veriyi analiz edip *"hangi formatta istersiniz?"* diye
soruyor ve turu bitiriyor — hiçbir katman "kullanıcı grafik istedi, grafik yok" diyemiyor.
Bu, plandaki `required_outputs` maddesinin ölçümle desteklenen gerekçesi.

---

# SIRADAKİ İŞ — Faz 5'in canlı ölçümü

Faz 5'in tamamı **fixture** üzerinde yeşil. Gerçek posta kutusunda **hiç koşmadı**, ve bu ölçüm
yapılana kadar arka plan ingestion **açılmamalı**. Yapılacaklar:

1. **Owner OAuth onayı** (Gmail read + Calendar write) — Faz 2'den beri bekleyen aynı onay.
2. `calendar_from_mail_enabled=True` ile **bir hafta gerçek gelen kutusu**, ve şu iki sayı:
   - **yanlış pozitif:** aday üretilen ama toplantı olmayan mail (hedef: ~0; `_EVENT_HINTS`
     listesi bunun için var ve **ölçülmedi**)
   - **kaçırılan:** gerçek toplantı maili olup aday üretilmeyen (`ambiguous` satırlarını okuyun —
     ledger hepsini `candidate_json` ile saklıyor, yani neyin neden kaçtığı sorgulanabilir)
3. Sayılara göre `_EVENT_HINTS`'i genişletin **veya** çıkarımın ikinci katmanına LLM ekleyin —
   ama **yalnız başlık için**, tarih için asla (Faz 3'ün uydurma ölçümü).
4. Ancak bundan **sonra** güvenilir-gönderen otomatik oluşturma tartışılabilir.

Ledger sorgusu için hazır: `MailEventLedger.list_by_state("ambiguous")` /`("error")`.

## Faz 5'te bilinçli olarak YAPILMAYANLAR

1. **Güvenilir-gönderen otomatik oluşturma** (incelemenin 10. maddesi) — güven bandı **kaydediliyor
   ama harcanmıyor.** 0.99 bile aday üretiyor, etkinlik değil. Açmadan önce yukarıdaki canlı ölçüm
   şart; ledger zaten gerekli veriyi topluyor.
2. **`side_effect_type` ikinci filtresi** — `plot_data`/`chart_revise` de `local_write` taşıdığı
   için naif hâli grafik çizmeyi kapatır (yukarıda). Gereken ayrım "yeni artifact üretir" vs
   "kullanıcının kaynak verisini değiştirir" — ölçümsüz yamalanamaz, hâlâ açık.
3. **`_notified_email_ids` değiştirilmedi** ve değiştirilmemeliydi: bugünkü amacı (toast dedup) için
   doğru. Faz 5 onu işleme durumu olarak **kullanmıyor** — ayrı ledger kuruldu; ikisinin
   karıştırılmaması `mail_ledger.py`'nin modül docstring'inde yazılı.
4. **`duration_minutes` maildan çıkarılmıyor** — sabit 60 dk varsayılan. "14.00-15.30 arası"
   yazan bir mail bunu söylüyor; okunmuyor. Küçük ve iyi sınırlanmış bir sonraki iş.

## Hazırlık aşamasında bilinçli olarak yapılmayanlar (hâlâ açık)

- **`create_separate: bool` argümanı** (inceleme öneriyor) — **eklenmedi.** Bu kod tabanının en
  pahalı tekrar eden hatası modele belirsiz bir seçim sunmak (Faz 4'te `chart_new` altı revizyon
  turunu öldürdü). Kimlik semantiğini değiştiren model-görünür bir boolean aynı riski taşıyor ve
  ölçülmedi. `source_fingerprint` (size+mtime) de eklenmedi (inceleme "isteğe bağlı" diyor).
- **Conversational negatif korpus (50-100 ifade) + mutasyon intent kapısı** — inceleme haklı:
  *"Teşekkürler"* 5/5 temiz çıktı ama **tek ifade bir güvenlik sınırını doğrulamaz.** Canlı ölçüm
  işi (ifade başına 20-80 sn). Aktif nesne varken `conversation` turlarının `data`'ya geçtiği
  **doğrulandı** (router'la deterministik olarak): `Teşekkürler` + aktif grafik → `data` domain'i,
  subset'te `chart_revise` + `plot_data` **var**. Faz 5 bu riski **büyüttü**: aktif bir
  `calendar_candidate` varken çıplak bir tur artık `calendar` domain'ine gidiyor ve `create`
  L3 — ama L3 olduğu için **kapıya takılıyor**, sessizce çalışmıyor. Kapının değeri tam burada.

---

## Önceki oturumlardan taşınan, değişmeyen işler

- Faz 4'ten: **zincir kapısı hâlâ GEÇMEDİ** — yukarıda yeniden ölçüldü (5'te 1). Darboğaz artık
  net: modelin 0. turda `plot_data` çağırmaması. Revizyon ve undo adımlarının hepsi yalnız
  **2 uygun örnekten** ölçüldü — kapsam yetersiz, davranış hakkında güçlü bir şey söylenemez.
- Faz 3'ten: **brifing zamanlanmadı**; `audit_narration` kapı değil ölçüm aracı; denetim göreli gün
  sözcüklerine sessiz.
- Faz 2.75'ten: **P1-1** tanımsız araç fail-closed değil; **P1-2** critic hata durumunda sessizce
  kabul ediyor; **P1-4** `local` rolü gerçekte local-only değil.
- Faz 2'den: **Google Contacts kapalı**, entity resolver hiçbir canlı yola bağlanmadı — owner
  OAuth kararına bağlı.
- Faz 1'den: **`enforce_reversible` açılmadı**; doğrulama 5 araçta, 43'te değil; `EvidenceSet.facts` boş.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- 7 direct-Gemini modülün shared gateway'e migrasyonu.
- Electron `npm audit`; canlı HUD E2E'nin **Electron penceresi** ayağı.
- Alt+Space → `/voice/ptt/start`; wake-word modeli; alpha gate'i GEÇTİ'ye taşımak.
- Finans: boru hattı çalışıyor, tek gerçek kaynak PDF ekstre importu.
- **CI:** `gh run list --branch langgraph-migration` ile **job düzeyine** bakın
  (`gh run view <id> --json jobs`). `mobile` job'ı `continue-on-error: true` (owner kararı,
  2026-07-23) ve **en az 2026-07-25'ten beri başarısız** — 71 bulgunun tamamı info/warning.
  **Owner kararı bekliyor.**
- **CI teşhis notu — `chromadb: no such table: acquire_write` bir FLAKE'tir**, regresyon değil.
  `requirements.txt`'te `chromadb>=0.6` **pinsiz**. Yeniden görülürse önce koşuyu tekrarlayın.
- **`main` kaç commit geride sayısını okumayın, türetin:**
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`.
