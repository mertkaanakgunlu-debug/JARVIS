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

## Son oturum: 2026-07-30 — MVP: "mailleri kontrol et → para akışı → Excel → grafik"

**Durum tek cümlede:** Owner tek bir MVP kabul hedefi koydu (*"Maillerimi kontrol et, hesabımdaki
para akışını analiz et, bir excel tablosuna dönüştür ve grafikle"*); görev **7 yapısal engel
yüzünden hiçbir modelle mümkün değildi**, engeller kapatıldı ve zincir **offline fixture lane'de
10 koşuda 10'unda uçtan uca tamamlanıyor** (`ALL STEPS 10/10`, qwen3:8b, CLOUD_POLICY=off).
**Ama MVP "hazır" DEĞİL: gerçek Gmail'e karşı hiç koşulmadı** — token iptal, aşağıya bakın. Owner
kararları: metin-öncelikli, **kesinlikle yerel**, Burgan mailleri, çok sayfalı analiz workbook'u.

### OAuth onarıldı, canlı koşu YAPILDI — ve MVP'nin veri kaynağı yokmuş

Gmail/Drive token'ları iptal olmuştu (`invalid_grant`; Gmail/Drive Google'ın *restricted*
scope'larını, Calendar yalnızca *sensitive* kullanıyor — Calendar bu yüzden sağ kalmıştı). Owner
`python scripts/auth_setup.py gmail drive` ile yeniden yetkilendirdi; **üç token da canlı**
(salt-okunur `_token_is_usable()` ile doğrulandı).

**Canlı koşu (`mvp_gate.py --live --runs 1`) en önemli özelliği kanıtladı: veri yokken uydurmuyor.**
`finance('sync')` → "📭 Banka bildirimi bulunamadı", **S5 GEÇTİ**, ne Excel ne grafik ne rakam
üretildi. Diğer adımlar doğru şekilde FAIL — üretecek veri yoktu.

**Kök bulgu: owner'ın gelen kutusunda hiç banka işlem bildirimi yok.** Salt-okunur tarama
(spam/çöp dahil, `in:anywhere`):

| sorgu | sonuç |
|---|---|
| `from:burgan` | 2 mail — "Logo", "FW: Kredi Kartı Görselleri" (iş yazışması, `burgan.com.tr`) |
| `from:on.com.tr` | 2 mail — ikisi de "ON e-posta doğrulaması" (15 May, 30 Tem 09:48) |
| `from:garanti` | 20 mail — hepsi bilgilendirme/pazarlama |
| "tutarında alışveriş" / "işlem gerçekleştirilmiştir" / "nakit çekim" | **0** |
| `in:spam` | 25 mail, hiçbiri banka |

**Bunun ortaya çıkardığı gerçek config hatası — düzeltildi.** Owner bankasının Burgan olduğunu ve
bildirimlerin açık olduğunu söyledi. Ama Burgan'ın bireysel dijital markası **ON** ve mailleri
`m.on.com.tr`'den geliyor — `from:burgan` bunu **asla** yakalamaz. Yani bildirimler gelmeye
başladığında bile sync sonsuza kadar "bulunamadı" derdi ve bu, yanlış yapılandırma değil boş
gelen kutusu gibi görünürdü. `finance_sender_filter` artık virgülle ayrılmış çoklu değer alıyor
(varsayılan `burgan,on.com.tr`) ve `from:(a OR b)` kuruyor; `_sender_clause()` + yeni
`tests/test_finance_sender_filter.py`. Canlı doğrulandı: yeni sorgu ON maillerine ulaşıyor
(4 mail, öncesinde 2). Doğrulama maili parser tarafından `no_amount` ile reddediliyor, deftere
kirlilik girmiyor.

**Yani MVP boru hattı hazır, veri kaynağı değil.** Hesap hareketlerine doğrudan bağlanma seçeneği
YOK: Türkiye'de açık bankacılık (BDDK/ÖHVPS) lisanslı üçüncü taraf sağlayıcı gerektiriyor, bireysel
bir script API kimlik bilgisi alamaz; Burgan/ON'un herkese açık müşteri API'si de yok. Teknik olarak
mümkün tek "doğrudan" yol internet bankacılığına kimlik bilgisiyle otomatik giriş (projede Playwright
MCP var) — **bu bilinçli olarak YAPILMADI ve yapılmamalı**: bankacılık şifresi/2FA taşımayı,
bankanın kullanım şartlarını ihlal etmeyi ve oturum açılmış bir bankacılık ekranını bir LLM'e
sürdürmeyi gerektirir.

### ✅ MVP GERÇEK VERİDE ÇALIŞIYOR — PDF ekstre importu

Owner ekstresini (9 sayfa, Burgan/ON PDF — ON yalnız PDF veriyor) sağladı. Yeni
`jarvis/finance_statement.py` + `finance('import_statement', path=...)`:

**Doğrudan hesap bağlantısı seçenek DEĞİL** ve bilinçle yapılmadı: Türkiye'de açık bankacılık
BDDK lisanslı üçüncü taraf sağlayıcı gerektiriyor, Burgan/ON'un müşteri API'si yok, ve tek teknik
alternatif olan kimlik-bilgisiyle internet bankacılığı otomasyonu (Playwright MCP mevcut) bankacılık
şifresi/2FA taşımayı ve oturum açılmış bir bankacılık ekranını LLM'e sürdürmeyi gerektirirdi.

**Ölçüm (gerçek veri, 2026-07-30):**
- Parser: **90 işlem, 0 red**, 91 satırdan (1'i sayfa geçişi tekrarı)
- **Bankanın kendi bakiye kolonuyla 89/89 tutarlı**; ayrıca en yeni satırın bakiyesi eksi tutarı
  = 1.978,90 = PDF başlığındaki kapanış bakiyesi. Bağımsız uçtan uca doğrulama.
- Temmuz 2026: Gelir 24.659,00 · Gider -28.583,68 · Net -3.924,68 · 76 işlem
- **Modelin kendisi zinciri sürdü: 3/3** (`finance(import_statement)` → `finance(export)`),
  doğru rakamlarla, Excel + grafik üretildi.

**Format bulguları (hepsi gerçek dosyada doğrulandı, tahmin değil):**
- Tutarlar **3 ondalıklı**: `-140,000` = −140,00 TL (−140000 değil). Yanlış okumak her rakamı
  1000 kat şişirirdi.
- pdfplumber sayfa 1'i 4 kolon, sayfa 2-8'i 6 kolon veriyor (aynı veri, kenarlarda boş hücre).
  `len(cells)==4` filtresi 91 satırın 10'unu buluyordu.
- **Sayfa geçişine denk gelen satır İKİ kez basılıyor** — biri yalnız açıklama öneki, diğeri
  satıcı adıyla. Kimlik anahtarı **(tarih, tutar, bakiye)** olmalı, açıklama OLMAMALI: yürüyen
  bakiye her harekette değiştiği için aynı gün aynı tutarlı iki gerçek işlemi ayırır ama kopyayı
  birleştirir. Açıklamayı anahtara katmak bir işlemi çift saydırıyordu (−151,44).
- Ekstre yolunda **hiç LLM yok** — satır, işareti bankaca verilmiş bir tablo hücresi.

**Ayrıca düzeltildi:** kategori kuralları fixture'ın hayali satıcılarından yazılmıştı, gerçek
veride 90 işlemin 71'i "other"a düşüyordu. Owner'ın gerçek satıcılarıyla genişletildi
(ESPRESSOLAB, MOKA/SWALLET, SOFRA BÖREK, MIGROS, Spotify, Disney, IYZICO/UBER, Google Claude,
İTÜ Strateji Geliştirme, PAYCEL/TALIMATLIFATURA …).

### Owner'ın KENDİ cümleleriyle, ÜRETİM modunda uçtan uca test

Owner haklı olarak "test geçti dedin ama çıktısını göstermedin" dedi. Önceki 3/3 ölçümü
**yanıltıcıydı: prompt'ta tam dosya yolunu ben veriyordum**, yani işin en zor kısmı atlanmıştı.
Owner'ın gerçek cümleleriyle, `--profile test` DEĞİL üretim modunda (gerçek ev dizini, gerçek
Gmail, `EXTERNAL_WRITES_ENABLED=false` zorunlu) tekrar koşuldu. `data/sessions.db` önce yedeklendi,
sonra geri yüklendi — gerçek defterde kalıcı iz yok (doğrulandı: transactions = 0).

**İlk koşu ikisinde de BAŞARISIZ oldu ve üç kusur ortaya çıkardı:**

1. **Sistem prompt'u "İndirilenler" klasörünü hiç söylemiyordu** (yalnız Home + Desktop vardı).
   Model `data/uploads/Hesap Hareketleri.pdf` diye uydurdu — hem var olmayan hem de
   `files.PROTECTED_DIRS` içinde bir yol, iki kez reddedildi. `_build_env_block`'a Downloads +
   Documents eklendi, "asla `data/uploads` gibi bir yol uydurma, emin değilsen önce `file_list`"
   talimatıyla.
2. **Boş-veritabanı hatası sebebi varsayıyordu** — yalnız "finance('sync') çağır" diyordu, oysa
   kullanıcı bir PDF göstermişti. Model mail yoluna itilip `import_statement`'ı hiç tekrar
   denemedi. Hata artık **iki yolu birden** adlandırıyor.
3. **Reddedilen import yolu çıplak bir "protected directory" hatası veriyordu** — düzeltilecek
   hedef yoktu. Artık ekstrelerin nerede olduğunu (`~/Downloads`) ve `file_list` ile adı
   doğrulamayı söylüyor.

**Düzeltmelerden sonra (üretim modu, tam çıktı görüldü):**

- **A — "indirilenlerdeki Hesap Hareketleri.pdf dosyasını oku, excel oluşturup grafikle":
  ÇALIŞIYOR.** Model yolu kendi çözdü (`C:\Users\mertk\Downloads\Hesap Hareketleri.pdf`),
  `import_statement` → 90 işlem, `export` → Excel + PNG üretildi, doğru rakamlar bildirildi
  (Gelir 24.659,00 · Gider -28.583,68 · Net -3.924,68 · 76 işlem). 40 sn.
- **B — "maillerimi oku, banka hesabımdaki hareketleri analiz et, excel oluşturup grafikle":
  DOĞRU davranıyor ama veri yok.** Boş defterle koşuldu: `sync` çalıştı, tek ON mailini bulup
  `no_amount` ile doğru reddetti, **uydurmadan** "işlem bulunmuyor" dedi ve iki kaynağı da önerdi.
  Excel üretmemesi doğru. Gerçek bildirim maili düştüğü an aynı zincir çalışacak.

**Bilinen boşluk (düzeltilmedi, bilinçli):** A ve B aynı oturumda arka arkaya koşulduğunda B,
mail'den 0 işlem gelmesine rağmen A'nın PDF'inden gelen Temmuz rakamlarını sundu — `sync` sonucu
"0 işlem kaydedildi" diyordu ama model bunu cevabına taşımadı. Yapısal çözüm veri kökeni
(provenance) takibi olurdu: `export` sonucu, aynı turdaki `sync` 0 kaydettiyse "bu rakamlar
mail'den değil, daha önce içe aktarılmış ekstreden geliyor" demeli. Gerçek iş, yapılmadı.

### Kapatılan 7 engel (hepsi kod üzerinde ölçülerek doğrulandı)

| # | Engel | Çözüm |
|---|---|---|
| B1 | `data` domain'i tam 8 araç, `MAX_TOOLS_PER_TURN` da 8 → `data` birincil olunca **hiçbir ikinci domain eklenemiyordu**; MVP prompt'unda `gmail` ve `finance` modele hiç görünmüyordu | `data`→`data`/`report`/`math` ayrıldı **+** `select_tool_names` artık her ek domain için slot rezerve ediyor (sınıfın çözümü, örneğin değil) |
| B2 | `max_tool_rounds_per_turn = 2`; zincir ≥3 sıralı tur istiyor | 2→4. F16/R20/R21/R23/B6 ile A/B ölçüldü: **iki kolda da 5/5**, regresyon yok |
| B3 | Excel/CSV **yazma** yeteneği hiç yoktu (`python_run` disabled, `file_write` metin) | `jarvis/tools/workbook.py` + `finance('export')` |
| B4 | `google-auth-oauthlib` kurulu değil **ve** requirements'ta tanımsız; `InstalledAppFlow` koşulsuz import ediliyordu → geçerli token bile kullanılamıyordu | 3 dep tanımlandı + import interaktif dala taşındı |
| B5 | `finance_extractor` yalnız-bulut + `cloud_policy` varsayılanı `off` → `sync` **kalıcı sessiz no-op** (transactions tablosu 0 satır) | `jarvis/finance_parser.py` (deterministik, birincil) + yerel LLM yalnız fallback |
| B6 | "para akışı" hiçbir finance pattern'ine uymuyordu | `\bhesab`, `\bpara ak`, `\bekstre`, … eklendi |
| B7 | `finance('chart')` plotly istiyor (kurulu değil) | Kapsam dışı bırakıldı; MVP `plot_data` motorunu kullanıyor |

### Yol boyunca bulunan 5 ek canlı bug

1. **API'nin async sezgisi Türkçe kısa kelimelerde substring eşliyor.** `"grafik"` `ASYNC_KEYWORDS`'te,
   yani owner'ın MVP cümlesi `/chat`'te sessizce arka plan `TaskExecutor`'a gidiyor ve ~0 sn'de
   `{"async": true, task_id}` dönüyordu. `cli.py` bu sezgiyi hiç kullanmıyor → **aynı cümle terminalde
   interaktif, HTTP'de asenkron**; etkilenen yüzey telefon/HUD. `ChatRequest.force_sync` eklendi
   (`_should_offload()` önceliği tutuyor). Keyword listesini daraltmak owner'a ait bir ürün kararı,
   yapılmadı.
2. **`summary()` kategori içinde netleyip sonra sınıflandırıyordu** → aynı kategoride maaş + kesinti
   varsa küçük taraf aydan tamamen kayboluyordu. Ayrıca **hiçbir yerde currency filtresi yoktu**;
   TRY/USD/EUR toplanıp "TRY" diye etiketleniyordu. `top_categories` ve `budget_status` da aynı
   kusurdaydı (bir USD harcaması TRY bütçesinden düşüyordu). Hepsi işaret-bazlı + currency-scoped.
3. **`months_back` ölü parametreydi** (tanımlı, geçiliyor, hiç kullanılmıyor) ve `gmail_control`
   `maxResults`'ı 25'e kırpıyor — finance 50 isteyip sessizce 25 alıyordu. Ayrıca `search` her mesajı
   `format="full"` çekip atıyor, finance sonra her birini **tekrar** okuyordu (2N çağrı). Yapısal
   `search_messages()` eklendi → N çağrı, `after:` gerçek tarih sınırı.
4. **60 sn tool timeout'u, yan etkisi işlenmiş bir çağrıyı izsiz bırakıyordu.** `no_amount`/`no_date`
   red'leri LLM'e tırmandırılıyordu; bunlar **kanıtlanabilir şekilde kurtarılamaz** (extractor her
   model değerini mailde geçen metne karşı doğruluyor), ama 5.2s+1.8s yerel çıkarım maliyeti gerçek
   tur çekişmesi altında sync'i 60 sn'yi aştırıyordu. Worker thread 7 işlemi yazdı, bekleyen taraf
   iptal edildi → `execution_end` yok, `tool_trace` satırı yok, model sync'in başardığını hiç
   öğrenmedi. Audit log'daki **3 `execution_start` / 2 `execution_end`** asimetrisiyle teşhis edildi.
   Gereksiz tırmanma kaldırıldı → sync 0.0 sn, sıfır çıkarım.
5. **Sistem prompt'u modele günün tarihini HİÇ söylemiyordu.** Timezone yazıyor, tarih yazmıyor —
   yani "bu ay", "yarın", "geçen hafta" modelin tahmin ettiği bir tarihe göre çözülüyordu. Ölçüm:
   30 Temmuz'da `finance('export', month=5)` **5/5**. `_build_now_block()` eklendi ve `_env_block`
   **property** yapıldı (sabit string olsa gece boyunca ayakta kalan sunucu tarihte kayardı).
   Owner'ın canlı takvim hatası ("Yarın öğlen saat 3'e … ekle") aynı aileden.

### Ölçüm: `scripts/mvp_gate.py` (yeni)

Her repetisyon **kendi `JARVIS_TEST_HOME`'unda** koşar (SQLite, PNG'ler, sidecar'lar, audit,
tool_trace hiçbir run arasında paylaşılmaz — paylaşılsa `upsert_transaction`'ın uid dedup'ı 2. run'ı
"0 kaydedildi" gösterip çıkarımı ölçülemez yapardı). Gmail, **yalnız `JARVIS_TEST_MODE=1` altında**
JSON fixture'dan servis edilir; production kodu test kodu import etmez ve env üzerinden Python
yüklemez (reddedilen alternatif: adapter'ı runtime path'ten import etmek — production'a enjeksiyon
yüzeyi).

**2026-07-30, `python scripts/mvp_gate.py --runs 10` (nihai ölçüm):**

| adım | sonuç |
|---|---|
| S1 mail okundu | **10/10** |
| S2 rakamlar mutabık (TRY) | **10/10** |
| S3 workbook (openpyxl ile içerik) | **10/10** |
| S4 grafik (+ sidecar içerik) | **10/10** |
| S5 uydurma başarı yok | **10/10** |
| **ALL STEPS** | **10/10** |

**Buraya gelmeden önceki yanlış rapor — kaydı önemli.** Bu oturumda bir ara "ALL STEPS 4/5" diye
rapor edildi; owner çıktıdaki "12 yeni mesaj var" ifadesini sorgulayınca iki şey ortaya çıktı:
(a) gate'in S5'i **iddia edilen SAYIYI** araç çıktısıyla karşılaştırmıyordu (yalnız adımın arkasında
araç var mı diye bakıyordu), yani o cümle gerçekten uydurmaydı ve gate onu geçirmişti — trace'te hiç
`gmail` çağrısı yoktu ve aracın kendisi "toplam 10 mail tarandı" demişti, model 12 dedi;
(b) 4/5 rakamı yüksek varyanslı bir sistemin tek örneğiydi. `FABRICATED COUNT` kontrolü eklendi ve
ölçüm 10 koşuya çıkarıldı.

`--contract-mode enforce_all` ile 1 koşu: **1/1 hepsi geçti**; o koşuda ilk `finance(export)`
BAŞARISIZ oldu ve S5 yine geçti — yani başarısız tool sonucu kullanıcıya başarı olarak
sunulamadı (istenen doğrulama).

Geçen bir koşunun cevabı: *"📊 Para akışı analizi tamamlandı (Temmuz 2026): Gelir 46.799,90 TRY ·
Gider -7.100,75 TRY · Net 39.699,15 TRY · İşlem sayısı 6. Excel ve grafik dosyaları hazır."*

### qwen3:8b'nin ölçülen sınırı — mimariyi bu belirledi

**2 bağımlı tool çağrısı tutuyor, 3 tutmuyor.** `sync → export` güvenilir; üçüncü hop her şekilde
başarısız oldu: uydurulmuş İngilizce kolon adları, grafik sayfası yerine defter sayfası, literal
`path='path_to_file'`, ve bir kez çağrının tamamı cevaba JSON bloğu olarak basıldı (HANDOFF'un zaten
kayıtlı "plot_data kod basıyor" örüntüsü). Tur başına bir argümanı düzeltebiliyor, seti bir arada
tutamıyor. Bu yüzden **grafiği `finance('export')` kendi üretiyor**. `plot_data` her şey için açık;
yalnızca para yolundaki zorunlu üçüncü hop kaldırıldı.

Modeli ölçülebilir şekilde iyileştiren 3 şey, tekrar kullanılmaya değer: sıradaki talimatı (ve
bildirmesi gereken rakamları) tool sonucunun **ilk iki satırına** koymak (5. satırdaki ipucu okunmadan
geçildi); workbook'ta **grafiğe uygun sayfayı ilk sıraya** almak (`plot_data` `sheet=` yoksa ilkini
okur); ve "kolon bulunamadı" hatasının **diğer sayfaları adlandırması** (çıkmaz sokağı kendini
düzelten hale getiriyor).

**Oturumun en pahalı bulgusu — tool DESCRIPTION'ında olumsuz/koşullu dil tool-calling'i tamamen
bastırıyor.** `finance` docstring'ine *"REQUIRES data in the store … export on an empty store fails"*
eklenmesi gate'i **0/10'a ve her koşuda SIFIR tool çağrısına** düşürdü — model hiçbir şey çağırmayıp
ne yapacağını anlatan düzyazı üretti. Aynı bilgi olumlu kurulunca (*"If the request mentions
mail/e-posta, call sync first in the same turn, then export"*) **10/10**'a döndü. Aynı model, aynı
router, aynı araçlar; fark yalnız ifade. Küçük bir modele aracın neye ihtiyacı olduğunu söylemek
sorun değil; aracın nasıl **başarısız olduğunu** söylemek onu araçtan kaçırıyor. Sonuç: bir
description düzenlemesi kod değişikliği kadar davranış değişikliğidir, yeniden ölçüm gerektirir.

**Tool-calling öldüğünde hızlı yer bulan teşhis merdiveni** (tekrar kullanın): araçları modele
doğrudan tek satırlık system prompt ile bağla (model yeteneğini izole eder) → tam system prompt
(prompt'u izole eder) → `fast` vs `reasoning` rolü (provider yolunu izole eder) → graph.
`tool_trace.jsonl` **ve** `audit_log.jsonl` ikisi birlikte boşsa model hiç tool çağrısı üretmemiştir;
graph'ın kapılayacağı bir şey olmamıştır.

**Yan bulgu (düzeltilmedi, bilinmesi gerekir):** `_route_query()` konuşma dışı her sorguyu
`reasoning` rolüne yönlendiriyor; bu rol `CLOUD_POLICY=off` altında **thinking AÇIK ve
max_output_tokens=2048** ile yerel Ollama demek (`fast` rolü `reasoning_effort="none"` + 4096
geçiyor). Ölçüm: aynı çağrı için `fast` ~0.9 sn / 19 çıktı token, `reasoning` 12–33 sn /
524–1360 token. İkisi de doğru tool çağrısı üretiyor, yani tool-calling çöküşünün sebebi bu DEĞİLDİ —
ama yalnız-yerel bir kurulumda her araç içeren tur bu bedeli ödüyor.

**qwen3:8b'nin tool çıktısı sıcaklık 0'da bile deterministik değil:** aynı prompt+araçlar dakikalar
arayla `[sync, export]` ve `[export]` döndü. Aradaki iki 5'li partide üretim tarafında hiçbir
değişiklik yokken skor 4/5 → 0/5 saldı. **n=1'den asla sonuç çıkarma.**

**Ayrıca öğrenilen bir anti-örüntü:** modelin argümanını **sessizce düzeltmek** işi bozar. Yanlış
dönem adlı dosyayı sessizce yeniden adlandıran ilk deneme, modelin kendi istediği yolu araması →
"dosya bulunamadı" → "export başarısız" raporuna yol açtı (5 koşudan 2'si doğru yazılmış bir workbook
için başarısızlık bildirdi). Artık **reddediliyor**, `args_schemas` doğrulamasının zaten izlediği
ilkeyle aynı: düzeltilmiş biçimi çağrıya geri koymak yok. Aynı sebeple modelin dosya adı seçme
imkânı tamamen kaldırıldı (`output` ne `@tool` imzasında ne `FinanceArgs`'ta var).

### Güvenlik / sözleşme dokunuşları

- **Excel formül enjeksiyonu bu repoda hiç korunmuyordu.** Satıcı/açıklama metni herhangi birinin
  gönderebildiği mailden geliyor ve Excel başta `=`/`+`/`-`/`@` görünce çalıştırıyor →
  `workbook.sanitize_cell()`. Gelecekteki her spreadsheet yazıcısı bunu kullanmalı.
- `finance` artık `side_effect_type="local_write"` (eskiden `external_read`) — `export` dosya
  yazıyor; alan aracın **en kötü** etkisini tanımlamalı. L2 kaldı, `_READ_ACTIONS` girdisi
  eklenmedi. `workflow_engine._had_side_effect()` bunu doğru yönde sıkılaştırıyor.
- Export yolu **deterministik + üzerine yazılıyor** (`exports/cashflow_<YYYY-MM>.xlsx`), çünkü
  `finance`'in kayıtlı `idempotency="natural"`ı bunu gerektiriyor. `RunContext.for_execution()`
  kullanılmadı (çağrı başına yeni dizin üretir) ve `data/` altına yazılamaz (`files._resolve()`
  `PROTECTED_DIRS` ile reddediyor).
- **XLSX idempotency'si semantik olarak** iddia ediliyor, byte-byte değil (zip, gömülü timestamp).

### Test/lint (2026-07-30, bu oturumun sonunda)

```powershell
python -m pytest -q                      # 1703 passed, 5 deselected (voice_e2e), 3 dk 43 sn
python -m ruff check jarvis scripts tests # All checks passed!
python scripts/mvp_gate.py --runs 5      # ALL STEPS 4/5 (yukarıdaki tablo)
```

Yeni test dosyaları: `test_google_auth_lazy_flow.py`, `test_fake_gmail_gate.py`,
`test_chat_force_sync.py`, `test_mvp_gate_scorers.py`, `test_finance_parser.py`,
`test_finance_store_aggregates.py`, `test_finance_extractor_local.py`, `test_workbook_export.py`,
`test_now_block.py`; `test_domain_closure.py`, `test_finance_tool.py`, `test_cloud_policy.py`
genişletildi. `voice_e2e` katmanı koşulmadı (gerçek Whisper/Piper cache'i gerekiyor).

**Mutasyonla doğrulanan testler** (düzeltme geri alınıp kırmızı olduğu ölçüldü): lazy OAuth import
(tam olarak 2 gmail vakası kırmızı, calendar/drive yeşil kaldı), `summary()` netleme bug'ı
(42200 ≠ 42500), currency çapraz-toplama (-371.25 ≠ -250.75).

**Kendi gate'imde bulunan bir scorer bug'ı:** S2, cevabın sayısını `abs(target)` ile karşılaştırıyordu,
yani doğru işaretli `-7100.75` hiç eşleşmiyordu — bir kabul koşusunda 5 cevabın 4'ü doğruyken S2 0/5
skorlandı. Artık iki tarafta da büyüklük karşılaştırılıyor.

### Commit durumu

Bu oturumun değişiklikleri **commit EDİLMEDİ** — owner commit istemedi. `git status` ile bakın;
kapsam: `requirements.txt`, `jarvis/{agent,api,config,finance_extractor,finance_parser,finance_store,
tool_registry}.py`, `jarvis/tools/{calendar,drive,gmail,finance,plotting,workbook}.py`,
`jarvis/graph/{tools,tool_router}.py`, `jarvis/execution/args_schemas.py`,
`scripts/{auth_setup,mvp_gate,seed_finance_fixture}.py`, `docs/TOOLS.md`, `MEMORY.md`, `tests/`.

## OWNER'IN AÇIK SORULARI (2026-07-30, sonraki oturumda ele alınacak)

Owner grafiği inceleyip dört soru sordu. Üçünün cevabı ölçülerek verildi; biri açık iş.

1. **"30 gün yok, 16 bar görüyorum"** — haklıydı, aslında **18 bar**tı (07-07 net tam 0,00 ve
   07-28 −20 TL olduğu için görünmüyorlardı). Dönem 30 gün, **12 günde hareket yok** ve o günler
   grafikten atılıyordu. Asıl kusur owner'ın fark ettiğinden ağırdı: **x ekseni zamansal değil
   kategorikti**, yani 3 günlük boşlukla 1 günlük boşluk aynı genişlikte görünüyor, grafik paranın
   ne zaman hareket ettiğini yanlış anlatıyordu. `daily_flow(fill_period=True)` eklendi, export
   bunu kullanıyor → ayın 31 günü de eksende, hareketsiz gün gerçek bir sıfır.
2. **"tarihler okunmuyor"** — düzeltildi. `_make_x_axis_readable()` (plotting.py, TÜM grafiklere
   uygulanır): ISO tarihler `GG.AA`ya kısaltılıyor, etiketler 45° döndürülüp sağa yaslanıyor,
   24'ten fazla kategori varsa her N'inci etiket gösteriliyor, işaretli seride sıfır çizgisi
   çiziliyor.
3. **"bar yerine çizgi/nokta yapabilir mi?"** — Yetenek **var** (`finance('export',
   chart_kind='line'|'scatter'|'bar')`, testlerle sabit), ama **takip turunda model bunu
   kullanmıyor**: iki ayrı koşuda "grafiği çizgi grafik yap" deyince `plot_data`'ya gidip
   uydurma kolon adlarıyla patladı. **Kök neden mimari** (aşağıdaki 1. maddeye bakın), prompt
   ayarı değil. Owner'a pratik tavsiye: **tek cümlede iste** ("... çizgi grafik olarak").
4. **"kalitesi nasıl"** — cevap aşağıdaki "kalite değerlendirmesi" bölümünde.

### Aynı incelemede bulunan CİDDİ bulgu — uydurma, takip turunda geri geldi

3. turda ("grafikteki tarihler okunmuyor, düzelt") JARVIS **hiç araç çağırmadan**, cevabına elle
`[Tool execution summary: plot_data ok]` yazıp — bu JARVIS'in KENDİ iç geçmiş işaretçisi —
"✅ Çizgi grafiği tamamlandı: exports/cashflow_2026-07_line.png" dedi. **O dosya hiç yaratılmadı.**
`strip_internal_markers()` eklendi (agent.py + `tests/test_internal_marker_leak.py`): baştaki
işaretçi kullanıcıya giden cevaptan siliniyor. **Bu uydurmayı DURDURMUYOR**, yalnızca uydurmanın
sistem rozeti takmasını engelliyor. Gerçek çözüm cevapta iddia edilen dosya yollarının diske karşı
doğrulanması — yapılmadı, aşağıda 1. sıradaki iş.

### Kalite değerlendirmesi (dürüst)

**Güçlü:** ilk tur uçtan uca güvenilir (fixture 10/10, gerçek ekstre 3/3); rakamlar bankanın kendi
bakiyesiyle 89/89 doğrulanıyor; veri yokken uydurmuyor (canlı kanıtlandı); Excel içeriği openpyxl
ile, grafik içeriği sidecar ile bağımsız doğrulanabiliyor.

**Zayıf:** (a) takip turları — tool sonucundaki yönlendirme bir sonraki tura taşınmıyor;
(b) uydurma tamamen kapatılmadı, yalnızca MVP turunda gate'le yakalanıyor; (c) `plot_data`'nın
workbook üzerindeki kullanımı hâlâ güvenilmez; (d) grafik tek renk — gelir/gider günleri renkle
ayrılsa çok daha okunur olurdu; (e) kategorilerin %79'u "other"dan kurtarıldı ama POS
açıklamalarındaki satıcı adları hâlâ ham (`ESPRESSOLAB ISTANBUL TR` gibi şehir/ülke ekli).

## SONRAKİ OTURUM — kalan iş (öncelik sırası)

1. **Cevapta iddia edilen dosya yollarını diske karşı doğrula.** Oturumun en ciddi açık bulgusu:
   model hiç araç çağırmadan "grafik hazır: exports/...png" diyebiliyor. Somut tasarım: compose
   adımından sonra cevaptaki `exports/...`, `data/runs/...` gibi yol benzeri dizgileri çıkar,
   diskte var mı bak, yoksa ya cevabı düzelt ya da açık bir uyarı ekle. `mvp_gate`'in S5'i bunu
   tek turluk MVP için yapıyor; asıl çalışma zamanında yok.
2. **Takip turu yönlendirmesini sistem prompt'una taşı.** Tool sonucundaki "sıradaki çağrı" ipucu
   tur sınırını geçmiyor (`_compact_completed_turn_for_history` tam tool sonucunu geçmişten
   düşürüyor). En az şu iki kural `jarvis/prompts/core/02_tool_policy.md`'ye girmeli: finans
   grafiğinin türünü değiştirmek için `finance('export', chart_kind=...)` — `plot_data` DEĞİL; ve
   çok sayfalı bir workbook'u çizerken `sheet=` zorunlu.
3. **Owner kararı: gerçek finans verisi nereden gelecek?** Boru hattı hazır, veri yok.
   Önerilen: **ekstre dosyası importu** — `finance('import_statement', path=...)` benzeri bir giriş
   yolu; `iter_transactions`/`upsert_transaction` zaten var, tek eksik ekstre formatını okuyan
   parser. Format sabit olduğu için mail parser'ından belirgin şekilde güvenilir olur ve GEÇMİŞ
   veriyle hemen çalışır. Alternatif/ek: bildirim mailleri gelmeye başlayınca `sync`
   (filtre artık doğru — ON dahil).
2. **Bildirim maili geldiğinde parser'ı gerçek metne ayarlamak.** Fixture owner'ın anlattığı
   formatlardan türetildi, gerçek ON/Burgan mail metni değil. `finance('sync')` red sebeplerini
   sayıyor — `no_amount`/`not_a_transaction` yığılırsa `finance_parser.py` pattern'leri gerçek
   gövdeye göre güncellenmeli. **Doğrudan hesap bağlantısı bir seçenek DEĞİL** (yukarıdaki
   gerekçe); kimlik bilgisiyle internet bankacılığı otomasyonu bilinçle kapsam dışı.
3. **İlk `export` çağrısının bazen month=5 ile gelmesi** — self-correcting hata 2. denemede
   düzeltiyor ama bir tur boşa gidiyor. Tarih bloğu prompt'un en sonunda; bu bir model-yeteneği
   sınırı, bilgi eksikliği değil.
4. Async keyword listesini daraltmak (telefon/HUD UX kararı, owner'a ait).
5. `_route_query()`'nin her araçlı turu `reasoning` rolüne (thinking AÇIK, 2048 token) göndermesi —
   yalnız-yerel kurulumda gereksiz gecikme. Ölçüm yukarıda.
6. Eski kalanlar (değişmedi): 4 worktree branch read-through; Electron `npm audit`;
   mobile flutter-analyze; canlı HUD E2E; Alt+Space → `/voice/ptt/start`; wake-word modeli;
   alpha gate'i GEÇTİ'ye taşımak (W18/R24 `workflow_start` güvenilirlik boşluğu).

## Değişmeyen taşınan işler

- 7 direct-Gemini modülün shared gateway'e migrasyonu (`finance_extractor` bu oturumda taşındı;
  kalanlar kapsam dışı).
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
