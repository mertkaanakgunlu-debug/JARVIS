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

## Son oturum: 2026-08-01 — **Faz 3: Daily Briefing MVP**

**Durum tek cümlede:** *"JARVIS bugün neler var"* çalışıyor ve **30 canlı turda 0 uydurma kalem**
üretti; ama planın uçtan uca gecikme kapısı **karşılanmadı** ve ölçüm bunun brifingin değil model
katmanının maliyeti olduğunu tam olarak gösteriyor.

## Sıra ve plan

Plan: `C:\Users\mertk\.claude\plans\c-users-mertk-downloads-jarvis-post-mvp-federated-kettle.md`
(Faz 0A + 0B + 1 + 2 + 2.5 + 2.75 + **3** bitti). **Sıra: Faz 4 — Working Set.**

## Taşıyıcı karar: brifing bir LLM workflow'u değil

```
DailyBriefingService
   ├─ takvim (şimdi → yerel gece yarısı)   ├─ todo (önceliğe göre ilk N)
   ├─ hava   (Open-Meteo, anahtarsız)      └─ haber (RSS/Atom, anahtarsız)
                       ↓  eşzamanlı, bölüm başına süre sınırı
                 BriefingFacts             ← deterministik
                       ↓  tek araç sonucu
                      LLM                  ← yalnız anlatım
```

Model veriyi toplamıyor, *"bugün"*ü çözmüyor, hangi etkinliğin var olduğuna karar vermiyor. Kapalı
bir kesin-metin listesi alıyor. `jarvis/briefing.py`'deki her şey bu ayrımı sistem prompt'una
yazılmış bir dilek olmaktan çıkarıp gerçek yapmak için var.

**İki kaynak da bilinçli olarak anahtarsız.** Her sabah çalışan bir brifing, bir kota bittiği için
bozulabiliyor olmamalı; ayrıca "anahtar süresi doldu" ile "hava durumu bilinmiyor" modelin
tarafından ayırt edilemez — `BriefingFacts`'in tüm amacı bu ikisinin farklı olgular olması.

## Ölçüm (n=10/senaryo, qwen3:8b, `cloud_policy=off`, makine boştayken)

| senaryo | p50 | p95 | **veri** p50 | **veri** p95 | araç disiplini | uydurma |
|---|---|---|---|---|---|---|
| `full` | 22.91 sn | 25.19 sn | **0.32 sn** | **0.79 sn** | 10/10 | **0** |
| `degraded` (takvim zorla hata) | 20.68 sn | 22.72 sn | 0.30 sn | 0.55 sn | 10/10 | **0** |
| `weather_only` | 15.87 sn | 17.30 sn | — | — | 10/10 | **0** |

Araç-hatası dürüstlüğü **10/10**.

**Gecikme kapısı (p50<5, p95<10) KARŞILANMADI ve ayrıştırma sebebini söylüyor.** Veri toplama kendi
bütçesini ~15 kat aşımla geçiyor; oradan 22.9 saniyeye kadarki her şey modelin Türkçe yazmasıdır.
Bu katmanın maliyeti, brifingin değil. Ancak daha hızlı bir katman (Kimi K3 — Faz 9) veya daha kısa
bir anlatım sözleşmesi düşürür; ikisi de kendi ölçümünü ister. **İki eksen olarak raporlanıyor tam
da bu yüzden** — tek bir sayı kimsenin üzerine iş yapamayacağı bir sayı olurdu.

**İlk ölçüm çöpe atıldı:** `pytest -q` aynı anda koşuyordu ve bir `full` turu 43 saniye geldi
(temiz koşuda maksimum 25 sn). Planın kendi kuralı — canlı harness başka hiçbir şeyle aynı anda
koşmaz — tam olarak bunun için var. Yukarıdaki rakamlar makine boşken yeniden koşulmuş halidir.

## Bu oturumun genellenebilir bulgusu — `_FAST_DOMAINS`'in gerekçesi yanlışmış

`weather` `_FAST_DOMAINS`'e o kümenin kendi ilkesiyle eklendi: *yapılandırılmış bir kaynağa tek
deterministik çağrı.* Aynı öğleden sonra **ölçümle çıkarıldı.** Aynı sorgu, kol başına n=5, başka
hiçbir değişiklik yok:

| kol | aracı çağırdı | diğer turlar ne yaptı |
|---|---|---|
| `fast` | **1/5** | *"sıcak ve güneşli, 32°C"* diye uydurdu (gerçek: 27.4°C, çok bulutlu) |
| `reasoning` | **5/5** | p50 16.2 sn |

`news` de tıpkı öyle tek çağrılık ve `fast`'te **5/5**. Yani çağrı şekli hiç eksen değilmiş. Kararı
veren şey **modelin cevabı zaten bildiğini sanıp sanmadığı**: qwen3:8b'nin ağırlıklarında bu
kullanıcının takvimi de bugünün manşetleri de yok ve model buna göre davranıyor — ama yılın her
günü için makul bir hava durumu raporu vardır, düşünme adımı olmadan model onu yazıveriyor.

**`_FAST_DOMAINS`'e alan eklemeden önce sorulacak soru "bu tek araç çağrısı mı" değil, "model bunu
inandırıcı şekilde uydurabilir mi".** Ölçülmemiş yedi üye (`tasks`, `media`, `memory`, `mail`,
`drive`, `finance`, `math`) artık yalnız ölçülmemiş değil, **gerekçeleri de şüpheli.**

Aynı bulgu önce bir **docstring** üzerinden geldi: `city:` argümanı *"Leave EMPTY for the user's own
location"* diyordu ve model bunu *"hangi şehirde hava durumunu öğrenmek istiyorsun?"*a çevirdi — bir
varsayılanı sorulacak bir soru olarak okudu. Planın risk tablosu tam bunu öngörüyor.

## Denetim (`audit_narration`) — her iki yönü de canlıda kazanıldı

Dört kontrol, hepsi karşılaştırmayla kararlanabilir: **uydurulmuş saat** (14:00'lük toplantıyı
15:00 diye anlatmak, atlamaktan daha kötü), **uydurulmuş sayı**, **sessizce atlanan başarısız
bölüm**, ve **başarısız bölümü BOŞ diye sunmak**.

Sonuncusu varsayımsal değil: canlı bir tur *"Bugün takvimde bir etkinlik bulunmuyor, bu nedenle
takvim bölümüne giriş yapılamadı"* üretti — hatayı kabul ediyor **ve** aynı nefeste boşluk iddia
ediyor, yani yalnız kök-tabanlı bir dürüstlük kontrolü bunu geçiriyor. *"Takvimini okuyamadım"*
kullanıcıyı gidip bakmaya iter; *"bugün hiçbir şeyin yok"* düşünmeyi bıraktırır.

- **Yanlış pozitif** de üretti: *"o günün etkinlikleri hakkında bilgi bulunmuyor"* — **bilgi** yok
  diyor, ki bu tam olarak doğru. Sıkılaştırıldı; yanlış pozitif üreten bir uydurma metriği hiç
  olmamasından kötüdür.
- **Üç yanlış negatif** de üretti: model `### Takvim (bugün)` / `DURUM: ALINAMADI — ...` yazdı —
  üretebileceği en dürüst çıktı — ve satır-bazlı bölme bunu sessiz atlama saydı, çünkü başlık ile
  itiraf satırsonunun iki yanına düştü. Artık markdown başlığı altındaki satırlara taşınıyor.

Denetimin **bilinçle sessiz kaldığı** bir sınıf var: göreli gün sözcükleri. Canlı bir turda model
bugünkü 11:00 etkinliğine *"Dün"* dedi. Sayı doğru, saat doğru, yanlış olan tek şey sözcük.
Karşılaştırılabilir bir jetona indirgenemeyen iddia karşılaştırmayla kararlanamaz; kaçırmak
uydurmaktan iyidir.

## Doğrulama (2026-08-01, bu oturumda çalıştırıldı)

```powershell
.venv\Scripts\python.exe -m pytest -q                        # 2681 passed, 5 deselected, 4 dk 06 sn
.venv\Scripts\python.exe -m ruff check jarvis scripts tests  # All checks passed!
npm test --prefix electron                                   # 27 passed
.venv\Scripts\python.exe scripts\briefing_gate.py --runs 10  # yukarıdaki tablo
```

Suite 2549 → **2681** (bu oturumda +132 test: brifing, hava, haber, yönlendirme).

**Yeni araç: `scripts/briefing_gate.py`** — canlı model, gerçek graph, sahte olan yalnız Google
Calendar (capture nesnesi) ve to-do DB'si (tohumlanmış scratch DB). `JARVIS_HOME` repo dışında bir
temp dizin (`BRIEFING_GATE_HOME` ile ezilir).

```powershell
.venv\Scripts\python.exe scripts\briefing_gate.py --runs 10
.venv\Scripts\python.exe scripts\briefing_gate.py --runs 10 --only degraded
.venv\Scripts\python.exe scripts\briefing_gate.py --runs 3 --offline   # ağ değişkenliği olmadan
```

## Bilinçli olarak yapılmayanlar

- **Brifing zamanlanmadı.** Üç araç da L1, yani Faz 2.75'in proaktif kıskacını geçiyorlar —
  **yapısal engel kalktı.** Ama 07:00 işini gerçekten kurmak ayrı bir değişiklik ve kendi teslimat
  sorusu var (push mu, toast mu, ikisi mi).
- **`audit_narration` çalışma zamanında bir kapı değil**, bir ölçüm aracı. Doğrulama düğümünün
  `enforce_*` onarım turuna bağlamak doğru devam, ama `EvidenceSet` bugün brifing olgularını
  taşımıyor.
- **Uzaktan mutabakat yok** — brifing takvimin ne dediğini raporluyor, doğru olduğunu değil.
- **`web_download` yok** (plan gereği kapsam dışı).

## Önceki oturumlardan taşınan, değişmeyen işler

- Faz 2.75'ten: **`ConversationRuntime` refactor'ü** (per-conversation history/turn/lock) yapılmadı;
  global kilit turları sıraya sokmaya devam ediyor. **P1-1 tanımsız araç fail-closed değil**
  (`policy_guard.evaluate()` ToolSpec bulamazsa `allowed=True`). **P1-2 critic hata durumunda
  sessizce kabul ediyor** ve turun rolünü izlemiyor. **P1-4 `local` rolü gerçekte local-only değil**
  (`cloud_policy=auto` altında; owner'ın `off` konfigürasyonunda etkisiz).
- Faz 2'den: **Google Contacts kapalı**, entity resolver **hiçbir canlı yola bağlanmadı** (Part 1);
  ikisi de bir OAuth yeniden onayına, yani owner kararına bağlı.
- Faz 1'den: **`enforce_reversible` açılmadı** (varsayılan `shadow`); terfi
  `rollout.enforce_gate_status()`'a bağlı — 100 gerçek artifact işlemi, 0 bildirilmiş yanlış blok.
  Doğrulama 5 araçta, 41'de değil. `EvidenceSet.facts` boş.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- 7 direct-Gemini modülün shared gateway'e migrasyonu.
- Electron `npm audit`; canlı HUD E2E'nin **Electron penceresi** ayağı.
- Alt+Space → `/voice/ptt/start`; wake-word modeli; alpha gate'i GEÇTİ'ye taşımak.
- Finans: boru hattı çalışıyor, tek gerçek kaynak PDF ekstre importu.
- **Çözülmemiş test flake'i (Faz 2.75'ten taşındı):** bir noktada dört tam koşudan biri 5-6 hata
  verdi. Yakalanan tek isim `test_todo_bg_analysis.py::test_bg_task_is_tracked_then_pruned_after_completion`.
  Teşhis: `graph_tools._todo_bg_tasks` modül düzeyinde global. **Bu oturumun iki tam koşusunda
  görülmedi.** Tekrar görülürse doğru düzeltme o dosyaya autouse bir temizleme fixture'ı eklemek.
- **CI:** `gh run list --branch langgraph-migration` ile **job düzeyine** bakın
  (`gh run view <id> --json jobs`). `mobile` job'ı `continue-on-error: true` (owner kararı,
  2026-07-23) ve **en az 2026-07-25'ten beri başarısız** — 71 bulgunun tamamı info/warning.
  Kod regresyonu değil, bloke etmiyor. **Owner kararı bekliyor.**
- **CI teşhis notu — `chromadb: no such table: acquire_write` bir FLAKE'tir, regresyon değil.**
  `requirements.txt`'te `chromadb>=0.6` **pinsiz**. **Yeniden görülürse önce koşuyu tekrarlayın**,
  kod aramayın.
- **`main` kaç commit geride sayısını okumayın, türetin** — her commit'te bayatlıyor:
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`.
