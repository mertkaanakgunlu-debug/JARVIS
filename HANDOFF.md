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

## Son oturum: 2026-08-01/02 — **Faz 3 (Daily Briefing)** + **Faz 4 (Working Set)**

**Durum tek cümlede:** iki kabul kilometre taşı da kuruldu ve ölçüldü; **Faz 3'ün uydurma ekseni
temiz (30 turda 0), Faz 4'ün zincir kapısı geçmedi (5'te 3)** — ve Faz 4'ün kalan iki hatası da
model tarafı, yapısal değil.

## Sıra ve plan

Plan: `C:\Users\mertk\.claude\plans\c-users-mertk-downloads-jarvis-post-mvp-federated-kettle.md`
(Faz 0A + 0B + 1 + 2 + 2.5 + 2.75 + **3** + **4** bitti). **Sıra: Faz 5 — Proaktif mail → takvim.**

---

# Faz 4 — Working Set (bu oturumun ikinci yarısı)

## Sorun, tam olarak

Biten tur geçmişe `[insan mesajı + kısa özet + final cevap]` olarak sıkıştırılıyor ve bu kural
**doğru** — ham araç state'i mesaj listesinde birikmemeli. Bedeli şu: *"çizgiyi kırmızı yap"*
geldiğinde ortada grafik yok. Model göremediği bir şeyi revize etmeye çalışıyor, tek cümleden
sıfırdan kuruyor, kaynağı/kolonu/türü yanlış yapıyor.

Working Set bu kuralın **dar istisnası**: ham çıktı değil, **spec** — nesneyi yeniden üretebilecek
yapılandırılmış tarif. Aktif nesnenin spec'i her tur sistem prompt'una ekleniyor, böylece revizyon
**tek argümanlık patch** oluyor. Ölçülmüş model sınırı tam buna elveriyor.

**Nereye asıldı:** konuşma başına, SQLite'ta, `conversation_id` anahtarlı. `JarvisAgent`'a değil
(tek agent tüm istemcilere hizmet ediyor, A'nın revizyonu B'nin grafiğine düşerdi), graph state'e
de değil (compaction onu buduyor — sıkıştırmadan sağ çıkacak şeyi sıkıştırmayı yapan şeyin içine
koymak olurdu). **Bu yüzden `ConversationRuntime` refactor'üne gerek kalmadı.**

## Ölçümle var olan üç düzeltme

**Grafik çizen TEK araç var.** İlk hal `plot_data`'nın yanına `chart_new` ekledi ve modele seçtirdi.
Canlı koşuda model `plot_data`'yı seçti, nesne oluşmadı, ardından gelen **altı revizyon turunun
hepsi düştü**. Belirsiz araç çifti bu kod tabanının en pahalı tekrar eden hatası; yetenek
`plot_data`'nın **içine** taşındı — aynı isim, aynı argümanlar, aynı dönüş değeri (120+ yerde
okunuyor), artı bir yan etki: çizdiğini saklıyor.

**Aynı grafiğin yeniden çizimi onu PATCH'liyor.** Sadece başlığı değiştirmesi istenen canlı bir tur
`plot_data` çağırdı, spec'i kendi prompt'undan kopyaladı, bir tur önce konan **rengi atladı** — ve
kullanıcıya grafiğin hâlâ kırmızı olduğunu söyledi. Değildi. Kimlik `(source, x, y)`; gerisi
sunum. Bu çağrıyı patch yapmak, modelin yanlış aracı seçmesinin **sonucunu** kaldırıyor — seçimi
engellemeye çalışmaktan daha dayanıklı.

**Uydurulan kolon düzeltiliyor, bildirilmiyor.** Önceki hal `plot_data`'nın hata vermesine izin
veriyordu; hata zaten gerçek kolonları listeliyor, model tekrar dener diye. Listeliyor. **Model
tekrar denemiyor:** `'Tarih'` diye bir kolon istedi, `[ERROR] Column 'Tarih' not found.
Available: ['ay','satis','gider']` aldı, kullanıcıya cevap verdi ve hiç çizmedi — **5 canlı
zincirin 3'ünü 0. turda öldürdü.** Artık her ikame dönüş değerinde bildiriliyor, çünkü asıl hata
yanlış kolonu **sessizce** çizmek olurdu.

## Yönlendirme: canlı nesne, kimsenin sahiplenmediği turu sahipleniyor

Çıplak bir revizyonda yetenek ismi geçmiyor, o yüzden `conversation`'a düşüyor ve model sıfır araç
alıyor. Revizyon kelime dağarcığını tahmin etmek sınırsız bir iş — bu router zaten üç jenerik fiili
hayalî alan ürettiği için silmişti. Onun yerine **state** cevaplıyor: aktif nesne, **hiçbir şeye
uymayan** turu sahipleniyor. Bilinçli olarak yalnız o durumda; her tura uygulasaydım konuşmanın
kalanındaki her tur multi-domain, yani reasoning katmanı olurdu. Ölçüldü: aktif grafik varken
*"Teşekkürler"* **5/5** hiçbir araç çağırmadı.

## Ölçüm (n=5 zincir × 7 tur = 35 canlı tur, qwen3:8b, `cloud_policy=off`)

Puanlama **saklanan spec** üzerinden, cevap metni üzerinden değil — akıcı bir modelin uyduramadığı
tek eksen bu. Her revizyon turu ayrıca **kullanıcının söylemediği alanların hayatta kaldığını** da
doğruluyor.

| tur | sonuç | kullanılan araç |
|---|---|---|
| oluştur → nesne var | 4/5 | `plot_data` |
| renk (source/x/y korunuyor) | 4/5 | `chart_revise` |
| başlık (**renk** korunuyor) | 4/5 | `chart_revise` |
| tür (**renk + başlık** korunuyor) | 4/5 | `chart_revise` |
| ilgisiz soru — dokunmamalı | **5/5** | — |
| "Teşekkürler" — dokunmamalı | **5/5** | — |
| geri al (tür geri döner, gerisi kalır) | 4/5 | `working_set` |

**Tam 7 turluk zincir: 3/5.** Revizyon turları 16/20; dokunmamalı turları **10/10**. Tur başına
gecikme p50 25.7 sn / p95 78.8 sn.

**KAPI GEÇMEDİ.** Kalan iki hata da model tarafı:

- Bir zincir 0. turda hiç çizmedi — model CSV'yi okudu, **dosyayı büyük harfli kolon adlarıyla
  yeniden yazdı**, tekrar okudu, `plot_data`'yı hiç çağırmadı. Grafik üreten 4 zincirin **3'ü tam
  doğru.**
- Bir zincirin `undo`'su beklenenden farklı bir revizyonu geri aldı. **Bilinen sınır, yazıldı:**
  zincir ortasında bir redraw `(source, x, y)`'yi değiştirirse **yeni** nesne doğuyor ve `undo` onun
  geçmişine uygulanıyor — kullanıcının aklındaki değişiklik orada olmayabilir.

## Faz 4'te bilinçli olarak yapılmayanlar

- **Yalnız `chart` kind'ının araçları var.** Store kind-agnostik (`email`/`report`/`table` destekli)
  ama araçları yok. Planın *"aynı primitive sonra mail taslağı, rapor, tablo için"* maddesi **yarım**:
  altyapı hazır, ikinci tüketici yazılmadı.
- **`undo` nesne düzeyinde, konuşma düzeyinde değil** (yukarıdaki ölçülmüş sınır).
- **Spec enjeksiyonunun gecikme maliyeti ayrıca A/B'lenmedi.** Blok `MAX_PROMPT_CHARS` ile sınırlı,
  boş küme `""` döndürüyor, ama "aynı istek, working set var/yok" koşusu yapılmadı.
- **`ConversationRuntime` refactor'ü** — artık Working Set için **gerekli değil** (store anahtarlı).
  Kalan değeri gerçek paralellik, doğruluk değil.

---

# Faz 3 — Daily Briefing (bu oturumun ilk yarısı)

`DailyBriefingService` (`jarvis/briefing.py`) takvim/todo/hava/haberi **kodda**, eşzamanlı, bölüm
başına süre sınırıyla topluyor; LLM yalnız anlatıyor. İki yeni kaynak da **anahtarsız**
(`jarvis/tools/weather.py` Open-Meteo, `jarvis/tools/news.py` RSS/Atom) — her sabah çalışan bir
brifing bir kota bittiği için bozulamamalı.

**Ölçüm (n=10/senaryo, 30 canlı tur, makine boşken):** uydurma **0**, araç disiplini 10/10,
araç-hatası dürüstlüğü **10/10**. Veri toplama p50 **0.32 sn** / p95 0.79 sn (kapı 5/10 sn).
Uçtan uca p50 **22.9 sn** — **gecikme kapısı KARŞILANMADI**, ve aradaki her şey modelin Türkçe
yazması. Katman maliyeti, brifingin değil.

**Genellenebilir bulgu:** `_FAST_DOMAINS`'in gerekçesi ("yapılandırılmış kaynağa tek deterministik
çağrı") yanlışmış. `weather` tam olarak oydu ve `fast`'te **1/5** araç çağırdı, kalan dördünde
*"sıcak ve güneşli, 32°C"* uydurdu (gerçek: 27.4°C, çok bulutlu); `reasoning`'de 5/5. `news` da tek
çağrılık ve `fast`'te 5/5. Gerçek eksen: **model cevabı zaten bildiğini sanıyor mu.** Ölçülmemiş
yedi üye (`tasks`, `media`, `memory`, `mail`, `drive`, `finance`, `math`) artık yalnız ölçülmemiş
değil, **gerekçeleri de şüpheli.**

---

## Doğrulama (2026-08-02, bu oturumda çalıştırıldı)

```powershell
.venv\Scripts\python.exe -m pytest -q                        # 2739 passed, 5 deselected, 4 dk 14 sn
.venv\Scripts\python.exe -m ruff check jarvis scripts tests  # All checks passed!
npm test --prefix electron                                   # 27 passed (2026-08-01)
.venv\Scripts\python.exe scripts\briefing_gate.py --runs 10  # Faz 3 tablosu
.venv\Scripts\python.exe scripts\revision_gate.py --runs 5   # Faz 4 tablosu
```

Suite 2549 → **2739** (bu oturumda +190 test).

**İki yeni harness — ikisi de canlı model, izole `JARVIS_HOME`:**

```powershell
.venv\Scripts\python.exe scripts\briefing_gate.py --runs 10 [--only degraded] [--offline]
.venv\Scripts\python.exe scripts\revision_gate.py --runs 5  [--arm fast|reasoning]
```

**KURAL (bu oturumda ihlal edildi, bir ölçüm çöpe gitti):** canlı harness başka hiçbir şeyle aynı
anda koşulmaz. `pytest` eşzamanlı koşarken bir `full` turu 43 sn geldi (temiz koşuda maksimum 25).

## Bu oturumda üç hata, üç farklı şey tarafından yakalandı

- **Türkçe `casefold` hatası** kendi unit testi tarafından: `"AYNI".casefold()` → `ayni`,
  `"Aynı".casefold()` → `aynı`, yani aynı manşet iki yazımda eşleşmiyordu.
- **`fast` katmanının uydurması** canlı harness tarafından (yukarıdaki hava durumu ölçümü).
- **Hermetik olmayan test** CI tarafından: enjekte edilmiş görünen kaynakların ikisi gerçek ağa
  gidiyordu; makinede ağ olduğu için geçiyor, CI'da düşüyordu.
- (**Dördüncüsü:** `test_background_turn`'ün duck-type stub'ı yeni bir metodu bilmediği için test
  **sonsuza kadar askıda kaldı** — `AttributeError`, testin beklediği event set edilmeden fırladı.
  Stub artık gerçek metodu ödünç alıyor.)

## Önceki oturumlardan taşınan, değişmeyen işler

- Faz 3'ten: **brifing zamanlanmadı** (üç araç da L1, proaktif kıskaç artık engel değil — iş
  kurulmadı); `audit_narration` çalışma zamanında kapı değil, ölçüm aracı; denetim **göreli gün
  sözcüklerine sessiz** (canlı bir turda bugünkü 11:00 etkinliğine *"Dün"* dedi).
- Faz 2.75'ten: **P1-1** tanımsız araç fail-closed değil; **P1-2** critic hata durumunda sessizce
  kabul ediyor ve turun rolünü izlemiyor; **P1-4** `local` rolü gerçekte local-only değil.
- Faz 2'den: **Google Contacts kapalı**, entity resolver **hiçbir canlı yola bağlanmadı** — ikisi de
  bir OAuth yeniden onayına, yani owner kararına bağlı.
- Faz 1'den: **`enforce_reversible` açılmadı** (varsayılan `shadow`); doğrulama 5 araçta, 43'te
  değil; `EvidenceSet.facts` boş.
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
