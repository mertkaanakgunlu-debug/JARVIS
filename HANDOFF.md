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

## Son oturum: 2026-08-01 — Post-MVP **Faz 2.5: otomatik rol seçimi**

**Durum tek cümlede:** hangi modelin turu göreceği artık kodun kararı, ve o kararı **kimin
verdiği** ekranda yazıyor — ama ölçüm, fazın planlandığı sayıların yanlış şeyi ölçtüğünü de
gösterdi.

## Plan ve sıra

Plan: `C:\Users\mertk\.claude\plans\c-users-mertk-downloads-jarvis-post-mvp-federated-kettle.md`
(Faz 0A + 0B + 1 + 2 önceki oturumlarda bitti). Sıra: **3 Sabah Brifingi** → 4 Working Set →
5 proaktif → 6 hafıza → 7 render/harita → 8 web_download → 9 Kimi K3.

## Kayıttaki taban sayı rolü ölçmüyordu

Fazın dayandığı sayılar — `fast` ≈ 0.9 sn / 19 token, `reasoning` ≈ 12–33 sn / 524–1360 token —
bir **sohbet cevabını** bir **araç işiyle** kıyaslıyordu. İkisi de eski kuralın seçtiği rolde
koştuğu için o sayılar rolü değil **isteğin zorluğunu** ölçüyor.

Doğru ölçüm: aynı sorgu, iki kol, n=10, gerçek qwen3:8b · Ollama · `CLOUD_POLICY=off`, gerçek
graph ve araçlar, Google Calendar yerine capture nesnesi, ayrı `JARVIS_HOME` — **0 gerçek Google
çağrısı**.

| Senaryo | `fast` p50 | `reasoning` p50 | Oran | Araç doğruluğu |
|---|---|---|---|---|
| *"Merhaba, bugün nasılsın?"* | **5.4 sn** | 8.4 sn | 1.6× | 10/10 · 10/10 (doğru şekilde sıfır araç) |
| *"Bugünkü takvimimi göster"* | **9.6 sn** | 14.8 sn | 1.5× | 10/10 · 10/10 |
| *"Masaüstündeki dosyaları listele"* | **9.1 sn** | 19.1 sn | 2.1× | 10/10 · 15/15 |
| *"Yarın 15:00'te Baran'la toplantı ekle"* | **16.8 sn** | 23.0 sn | 1.4× | 10/10 · 10/10 |

**Kazanç 1.4–2.1×, ~20× değil.** Burada iki rol de aynı qwen3:8b; fark yalnız düşünme kanalı.
Bu faz saniye kazandırıyor, mertebe değil — açıkça söylenmeli.

**Asıl önemli sütun doğruluk.** Düşünme kapalıyken araç çağırmanın bozulmadığına dair tek kanıt
n=1'di (`test_local_thinking.py` docstring'i, tek bir `file_write`). Dört tek-araçlık senaryoda
`fast` **45/45** turda doğru aracı çağırdı ve takvim tarihini her seferinde doğru yazdı
(2026-08-02T15:00+03:00) — yani Faz 2'nin clock/temporal/event-text makinesi düşünme kanalı
olmadan da tutuyor.

`file_list` reasoning hücresi n=15: ilk koşu ortada öldü (aşağıdaki session id hatası), devam
koşusu onu baştan ölçtü. Fazladan 5 örnek atılmadı, bildirildi.

## Fazın en önemli bulgusu — zincir sorunu **rolle çözülmüyor**

İki bağımlı çağrılık istek (*"csv'yi oku ve grafiğini çiz"*) hiçbir kolda tamamlanmıyor.

| | araç dizisi | grafik |
|---|---|---|
| `fast` | `csv_read` (9/10), `csv_read → data_analyze` (1/10) | **0/10** |
| `reasoning` | `csv_read → data_analyze` (9/10), `csv_read` (1/10) | **0/10** |

`reasoning` bir adım daha derine iniyor — ikinci çağrı 9/10'a karşı 1/10 — ama grafiğe hiç
ulaşmıyor; `data_analyze` yalnız istatistik
üretiyor, grafik aracı sadece `plot_data`. `fast` kolunda bir turda model **ham tool-call JSON'unu
cevap metni olarak** bastı, bir turda da çizmek yerine *"dilerseniz oluşturabilirim"* dedi.

**Sonuç:** `multi_domain → reasoning` kuralı ölçümle *doğrulanmadı* — temkinli olduğu için duruyor,
kazandırdığı için değil. Zincir tamamlamanın çözümü **Faz 4'ün Working Set'i**, model katmanı değil.

İlk koşuda ayrı bir bulgu daha çıktı: *"Masaüstündeki satis.csv"* denmesine rağmen model
**10/10 Downloads'a** gitti. Temiz ölçüm için dosya iki klasöre birden konuldu.

## Ne yapıldı

1. **`jarvis/graph/role_router.py`** — `reasoning` varsayılan, `fast` **hak edilmeli**. Okunamayan
   bir tur bugünkü davranışını aynen koruyor, yani değişiklik yalnız *azaltabiliyor*.
   Gerekçeler: `explicit_think`, `no_route`, `multi_domain`, `deliberative_domain`,
   `composes_prose`, `sequenced_steps`, `multiple_imperatives`, `unattended` → `reasoning`;
   `conversation`, `single_domain_tool` → `fast`.
2. **Turun kendi rolünün bilinçle ezildiği iki yer.** `for_unattended_turn`: proaktif kontroller ve
   arka plan işleri asla `fast` almıyor — orada bekleyen kimse yok, ve proaktif tur, yanlış karar
   veren bir modelle izlenmeyen bir L2 yazma arasındaki tek şeyin bir prompt cümlesi olduğu yol
   (`docs/SAFETY.md` bunun kapatılmadığını açıkça yazıyor). Davranış değişimi **sıfır** — o turlar
   zaten `reasoning` alıyordu. `nodes.py`'deki unbacked-claim onarımı **her zaman** yükseliyor:
   kanıtlanmış yanlış bir cevabı üreten katmanda tekrar denemek tek onarım hakkını boşa harcamak.
3. **Gerekçe ize ve ekrana akıyor** — `LlmTraceRecorder` → `last_turn_trace["role_reason"]` →
   CLI'nin `Last turn:` satırı, `GET /status`, HUD'ın Routing satırı.
4. **Üç regex daraltması**, her biri bu projenin sürekli söylediği bir kelimeye çarpıyordu:
   `\bindir` → **"indirilenler"** (her system prompt'ta geçen klasör), `\bciz` → **"çizgi"**,
   ve sıralayıcı olarak `"en son"` → üstünlük anlamındaki **"en son"**. Üçü de tek adımlık isteği
   iki adımlık yapıyordu. Yalın **"ve"** hiç adım sınırı sayılmadı.

## Yol üstünde bulunup düzeltilen gerçek hata — session id çakışması

`SessionStore.new_session()` `YYYYMMDD-<4 hex>` çekip **tek** INSERT yapıyordu, tekrar için hiçbir
yakalama yoktu. Tarih öneki uzayı her gün sıfırladığı için bu bir doğum-günü problemi: bir günde
~150 oturum ≈ **6'da 1** çakışma olasılığı, ve biri 100 turluk ölçümü **75. turda**
`sqlite3.IntegrityError` ile öldürdü. Elle nadir, otomatik her şey için neredeyse kesin.

Artık 5 denemeye kadar retry + 8 hex hane. **Retry düzeltme, haneler ise retry'ın hiç
çalışmamasını sağlayan şey** — entropi tek başına doğru yapmıyor, iki çağıran yine aynı id'yi
çekebilir. Çakışma testi ilk çekimi zorla tekrarlatıyor: 8 haneyle doğal bir çakışma bir daha
gözlenmeyeceği için ancak zorlanmış olan retry'ı kanıtlar.

## Doğrulama (2026-08-01, bu oturumda çalıştırıldı)

```powershell
.venv\Scripts\python.exe -m pytest -q                        # 2281 passed, 5 deselected, 5 dk 08 sn
.venv\Scripts\python.exe -m ruff check <değişen 10 dosya>     # All checks passed!
npm test    --prefix electron                                # 27 passed
npm run build --prefix electron                              # 35 modül, hatasız
```

GPT review düzeltmelerinden sonra tekrar: **2287 passed** (3 dk 58 sn). Taban 2235 +
`test_role_router.py` 43 + `test_session_store.py` 3 + review düzeltmelerinin testleri 6 = 2287;
aritmetikle doğrulandı, hiçbir eski test sessizce düşmedi.

**Çözülmemiş tek nokta — tekrarlanamayan bir flake.** Dört tam koşudan **biri** 5 hata verdi;
sonraki **üç koşu temiz** (2287). Yakalanan tek isim
`test_todo_bg_analysis.py::test_bg_task_is_tracked_then_pruned_after_completion` — izole halde
3/3 geçiyor. `pytest-randomly` **kurulu değil**, yani sıra rastgele değil; muhtemel neden
zamanlama (o koşu, CPU'yu doyuran bir mutasyon turunun hemen ardından başladı). Kod
regresyonuna bağlanamadı ama **kapatılmış da sayılmamalı** — tekrar görülürse önce arka plan
görev testlerinin zamanlama varsayımlarına bakın.

**Mutasyon turu: 22/22 yakalandı.** Faz 1 ve Faz 2'nin aksine ilk turda hayatta kalan olmadı —
ama bunun dürüst nedeni şu: üç regex çakışması mutasyon turundan **önce**, kuralları gerçek
cümlelere karşı okurken bulundu ve her biri düzeltmesiyle **birlikte** testlendi. Yani mutasyon
turu bu kez boşluk keşfetmedi, kapsamı doğruladı.

Session id düzeltmesine ayrıca 3 mutasyon koşuldu: 2 yakalandı, 1'i davranışsal olarak **eşdeğer**
mutanttı (8→4 hane hiçbir davranışı bozmuyor, retry emiyor) — onun için entropi bütçesini açıkça
sabitleyen ayrı bir test yazıldı.

## Faz 3 öncesi kod gözden geçirmesinde çıkan iki nokta

**1. Kritik (critic) turun rolünü izlemiyor — yapısal, bilinçli değil.** `llm_pro`, `graph.py`'de
**bir kez** `get_llm("reasoning", ...)` ile kuruluyor, yani `make_critic_node` agent/compose gibi
`state["use_pro_agent"]`'ı okuyamıyor. Her tur `reasoning` iken bu görünmezdi; artık değil.
`_is_simple_exchange` 40 kelimeyi aşan **her cevabı** (veya 15 kelimeyi aşan soruyu, ya da veri
dosyası adı geçen soruyu) tam yola sokuyor — ve *"Yarın saat 15:00'te Baran'la toplantı ekle"*
gibi bir `fast` turda **10/10** reasoning katmanında bir çağrı harcanıyor. Ölçüldü, varsayılmadı.

**Bilerek düzeltilmedi:** hangi modelin cevabı yargıladığını değiştirmek bir **kalite kapısını**
değiştirmektir, kendi öncesi/sonrası ölçümünü ister (`scripts/role_ab.py` tam bu iki ekseni
raporluyor), ve "daha zayıf katmanla yargıla" hızlı olduğu için doğru olmuyor. `nodes.py` ve
`docs/ARCHITECTURE.md`'ye maliyeti görünür olsun diye yazıldı.

**2. `/model` pin'i artık daha çok tura ulaşıyor.** `pin_cloud_model` yalnız **fast** rolüne
uygulanıyor, `reasoning` onu yok sayıyor. Yani bu fazın `fast`'e taşıdığı her tur, bir pin varken
ücretli bulut modeline gidebilecek bir tur — önceden `reasoning`'e gidip yerel kalıyordu.
Owner'ın konfigürasyonunda **yapısal olarak imkânsız** (`cloud_policy="off"` pinli olsa bile her
bulut katmanını reddediyor — doğrulandı), ama `explicit`/`auto` altında gerçek. `_FAST_DOMAINS`'i
genişletmeye gelen biri görsün diye `role_router.py`'ye yazıldı.

Aynı geçişte düzeltilen bayat iddialar (hepsi yorum, davranış değişimi yok): kritik'in docstring'i
"Gemini Pro" diyordu (aslında `reasoning` neye çözülüyorsa o — yerelde qwen3:8b),
`JarvisState.use_pro_agent` "route agent node to Gemini Pro" diyordu, ve `ARCHITECTURE.md` fast
rolünün birincil modelini `qwen2.5:7b-instruct` gösteriyordu.

## Bilinçli olarak yapılmayanlar

- **`tool_router.py`'de iki örüntü boşluğu bulundu, düzeltilmedi.**
  (1) Jenerik bir fiil belirli bir alana aitse hayalî ikinci alan doğuruyor: `\blistele` bir
  *files* örüntüsü, yani *"Son 3 mailimi listele"* → `['mail','files']` ve tek çağrılık istek
  `multi_domain` ile reasoning'e gidiyor — 16 gerçekçi istekte **4 kez** (`listele`, `ara`).
  (2) `\bpdf\b` Türkçe ekli *"PDFteki"*'yi kaçırıyor, bu yüzden model **PDF okuyabilen hiçbir araç
  görmüyor** — bu ikincisi rol seçiminden bağımsız, daha ciddi bir **araç görünürlüğü** hatası.
  İkisi tek bir ölçümlü `tool_router` turunda düzeltilmeli; planın risk tablosu model-görünürlüğü
  değişikliklerinin yeniden ölçüm istediğini söylüyor.
  `tests/test_role_router.py::test_a_generic_verb_can_split_one_request_into_two_domains`
  bugünkü davranışı sabitliyor ve düzeltildiğinde **kırmızıya dönecek** — kasıtlı, doğru dosyayı
  gösteriyor.
- **`_FAST_DOMAINS`'in yalnız iki üyesi canlı ölçüldü** (`calendar`, `files`). `tasks`, `media`,
  `memory`, `mail`, `drive`, `finance`, `math` gerekçeyle eklendi. Hepsi bugünkü davranışı koruyan
  yönde, yani yanlışlarsa bedeli gecikme.
- **`composes_prose` ölçülmedi.** Mail *göndermenin* düzyazı ürettiği için reasoning'de kalması
  mantıkla türetildi; hangi katmanın daha iyi mail yazdığı ölçülmedi. Onay kapısı her iki durumda
  da dokunulmamış: gönderim yine duruyor ve soruyor.
- **HUD satırı yalnız compile+build doğrulaması.** Electron'da component test altyapısı yok
  (jsdom/testing-library yok) ve eklemek bu fazın işi değildi. Sözleşmenin **backend yarısı**
  testli (`role_reason` frame'de var; tur koşmamışken uydurma yerine null).
- **Sınıflandırıcının kendisi canlı A/B'de sürülmedi.** Ölçüm rolü *zorlayarak* yapıldı, yani
  "hangi rolün ne kazandırdığı" ölçüldü; "sınıflandırıcı gerçek trafikte doğru rolü seçiyor mu"
  deterministik testlerle ve 37 istekli bir dağılım taramasıyla gösterildi (**%73 fast**), canlı
  turlarla değil.

## Oturum sonu

**5 iş commit'i ve bu kapanış HANDOFF commit'i.** Yapı bilinçli olarak bağımsız ve tek tek geri
alınabilir, bağımlılıklar ileri akıyor (hiçbir ara commit kırık ağaç bırakmıyor):
session id düzeltmesi → rol router + kablolama + testler → sunum katmanı (CLI/`/status`/HUD) →
ölçüm harness'ı → docs. Oturum sonunda **local == origin senkrondu**.

**Yeni araç: `scripts/role_ab.py`.** Aynı sorguyu iki rolde koşup **gecikme ve doğruluğu birlikte**
raporluyor. `JARVIS_HOME` repo **dışında** bir temp dizin (`ROLE_AB_HOME` ile ezilebilir), Calendar
yerine capture nesnesi, fixture'ı kendi kuruyor.

```powershell
.venv\Scripts\python.exe scripts\role_ab.py --runs 10
.venv\Scripts\python.exe scripts\role_ab.py --runs 10 --arm fast --only calendar_list
```

Faz 3'ün gate'i (medyan < 5 sn **ve** p95 < 10 sn **ve** 0 uydurma kalem) tam olarak bu iki ekseni
istiyor — yalnız gecikme raporlayan bir harness o soruyu cevaplayamaz.

## GPT review (2026-08-01) — koda karşı doğrulandı

Review yazarı repoyu klonlayamadığını belirtti (raporun 39. satırı), yani bulgular **GitHub
okumasına** dayanıyor. Her birini gerçek koda karşı çalıştırdım. **Uydurma bulgu çıkmadı** —
aşağıdakilerin hepsi doğrulandı.

| # | Bulgu | Durum | Kanıt |
|---|---|---|---|
| P0-4 | `task-async` "insan mevcut" sayılıyor | ✅ **BU OTURUMDA DÜZELTİLDİ** | `evaluate(google_calendar, create, interactive=True)` → `requires_confirmation=False, risk=3` |
| P1-6 | Onaylı turda kullanıcı metni hafızaya yazılmıyor | ✅ **BU OTURUMDA DÜZELTİLDİ** | `_schedule_memory_extraction("", ...)`, `resumed_user_query` zaten okunmuşken |
| P0-1 | Stream, terminal cevabı değil biriken chunk'ları geçmişe yazıyor | ✅ doğrulandı, açık | `agent.py`: `full_response = "".join(chunks)` → `_compact_completed_turn_for_history` |
| P0-2 | Onay sonucu global aktif session'a yazılıyor | ✅ doğrulandı, açık | pending kaydı `{config, recorder, created_at}`; `ConfirmRequest` yalnız `decision` taşıyor |
| P0-3 | Arka plan görevi conversation ID'yi kaybediyor | ✅ doğrulandı, açık | `submit(query)` → `AsyncTask(task_id, user_query)`; `body.conversation_id` hiç geçmiyor |
| P0-6 | Salt-okuma `todo`/`schedule`/`finance` proaktif yolda bloklanıyor | ✅ doğrulandı, açık | üçü de `risk=2, confirm=False, local_write`; `_READ_ACTIONS` yalnız 4 Google/mail aracını kapsıyor |
| P1-1 | Tanımsız araç fail-closed değil | ✅ doğrulandı, açık | `policy_guard.py`: ToolSpec yoksa `allowed=True` |
| P1-4 | `local` rolü gerçekte local-only değil | ✅ doğrulandı, açık | `cloud_policy=auto` altında `get_llm("local")` bulut fallback katmanı istiyor |

**P0-6'nın somut kanıtı** (Faz 3'ü doğrudan bloklayan bulgu):

```
todo    {'action':'list'}     risk=2 confirm=False -> proaktif yolda BLOKLANIR
schedule{'action':'list'}     risk=2 confirm=False -> proaktif yolda BLOKLANIR
finance {'action':'summary'}  risk=2 confirm=False -> proaktif yolda BLOKLANIR
google_calendar {'action':'list'}   risk=1 -> geçer
gmail   {'action':'list_unread'}    risk=1 -> geçer
```

Yani bugünkü haliyle proaktif yolda koşan bir Daily Briefing takvimi ve maili okuyabilir,
**yapılacakları ve finansı okuyamaz**.

**Owner kararı bekleyen soru:** review "Faz 2.75 — Runtime & Session Hardening" öneriyor
(Paket A–F). Doğrulama bunu destekliyor: P0-1/2/3 tek bir kök nedenin üç yüzü — **conversation
runtime'ın global olması**. Faz 3 gözetimsiz çalışan ilk özellik olacağı için bu üçü orada
sistematik hâle gelir. Ama bu bir faz büyüklüğünde iş; sıraya alınması owner'ın kararı.

## SONRAKİ OTURUM — Faz 3: Daily Briefing MVP

Plan dosyasındaki Faz 3, ve 1. kabul kilometre taşı: *"JARVIS bugün neler var"* → saate duyarlı,
**uydurmasız** brifing (takvim · yapılacaklar · hava · haber). Brifing bir LLM workflow'u
**değil**: `DailyBriefingService` deterministik `BriefingFacts` üretir, LLM yalnız anlatır.

Faz 2.5'in oraya bıraktığı iki şey: (1) brifing tek deterministik iş olduğu için `fast` katmanına
uygun — ama `tool_router`'da kendi alanı yok, eklenmesi gerekecek; (2) `jarvis/clock.py` Faz 2'de
kuruldu ve brifing onun henüz bağlanmamış tüketicisi.

## Önceki oturumlardan taşınan, değişmeyen işler

- Faz 2'den: **Google Contacts kapalı**, entity resolver **hiçbir canlı yola bağlanmadı** (Part 1);
  ikisi de bir OAuth yeniden onayına, yani owner kararına bağlı.
- Faz 1'den: **`enforce_reversible` açılmadı** (varsayılan `shadow`); terfi
  `rollout.enforce_gate_status()`'a bağlı — 100 gerçek artifact işlemi, 0 bildirilmiş yanlış blok.
  Doğrulama 5 araçta, 36'da değil. `EvidenceSet.facts` boş.
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
