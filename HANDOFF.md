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

## Son oturum: 2026-07-31 — yön değişikliği + Post-MVP Faz 0

**Durum tek cümlede:** Owner kapsamı yeniden çerçeveledi (finans zinciri **ürün hedefi değil, bir
kapasite testiydi**; JARVIS'in amacı kişisel asistan/sekreter), 9 fazlı bir Post-MVP planı
onaylandı, ve **Faz 0 (ölçülebilirlik) bitirildi**: HUD ve terminaldeki her gösterge artık ya
gerçek veriye bağlı ya da bilmediğini söylüyor.

## Owner'ın yön kararları (plan bunlara göre kuruldu)

| Konu | Karar |
|---|---|
| Amaç | **Kişisel asistan / stajyer / sekreter.** Finans zinciri sadece zor bir testti |
| Öncelik | **Mimari doğruluk**, özellik genişliği değil |
| Model | qwen3:8b'yi maksimum kullan. **Kimi K3 cloud sonra** (GPT-5.6 ile planlanıyor), şimdi maliyet çıkmasın |
| Yüzey | **Electron HUD** — owner terminale aşina değil. Canlı testleri Claude sürer |
| Tempo | Açık uçlu, **her faz yeni session** (context şişmesin) |
| Revizyon | 5+ ardışık, açık uçlu diyalog |
| Yetki | Takvim/todo serbest — **ama takvim tarih hatası kapandıktan sonra**. Mail hep onaylı |
| Proaktiflik | Önce güvenilirlik; saat başı mail kontrolü sonraki fazda |
| Grafik | Herhangi bir kaynak. Önce doğruluk, **sonra** MATLAB kalitesi (owner jeofizik mühendisi) |
| Ses | Acelesi yok |
| Repo | Public kalıyor (GPT review için); private geçiş ertelendi |

**Yeni kabul kilometre taşları:** (1) *"JARVIS bugün neler var?"* → sabah brifingi ·
(2) *"Bu grafiği değiştir"* → 5-10 tur revizyon · (3) *"Ben sormadan önemli şeyi fark et"*.

## Onaylanan plan

`C:\Users\mertk\.claude\plans\c-users-mertk-downloads-jarvis-post-mvp-federated-kettle.md`

Sıra: **0A** baseline senkronu → **0B** canlı veri dürüstlüğü → **1** honesty kernel →
**2** Clock/temporal/entity → **2.5** otomatik rol seçimi → **3** Sabah Brifingi (MVP) →
**4** Working Set → **5** proaktif → **6** hafıza → **7** render/harita → **8** web_download →
**9** Kimi K3.

Plan bir GPT review'ünden geçti; **14 maddesinin 13'ü koda karşı doğrulanıp işlendi**. Reddedilen
tek madde (`force_sync` yok) **senkron artefaktıydı** — GPT remote'u okumuştu, alan yereldeydi.
Bu yüzden Faz 0A (push edip `local == origin` yapmak) planın ilk gate'i oldu.

GPT'nin en değerli düzeltmesi: **Faz 1 yeni bir grounding sistemi kurmamalı.**
`jarvis/execution/summary.py`'deki `VerifiedExecutionSummary` zaten var ve `compose_node`'a bağlı
(`nodes.py:309`); iki kapı yüzünden hiç çalışmıyor — `execution_contract_mode` varsayılanı `off`,
ve **sıfır-araç turunda envelope listesi boş** olduğu için uydurma vakasında mekanizma tamamen
atlanıyor. Faz 1 bu iki kapıyı açacak, sıfırdan yazmayacak.

## Faz 0'da ne yapıldı

**Değişmez kural (artık testle zorunlu):** *bağlıyken hiçbir alan sentetik, placeholder,
durumdan-türetilmiş veya rastgele değer gösteremez. Veri yoksa `—`/"veri yok", yetenek yoksa
"YAKINDA". Demo veri yalnız bağlantı yokken, `○ OFFLINE · DEMO DATA` rozetiyle.*

Owner'ın bildirdiği "terminalde hâlâ Gemini yazıyor" tek bir etiket hatası değil, bir sınıf çıktı:

| Katman | Neyi uyduruyordu |
|---|---|
| `ws.py` | psutil yokken `random.uniform()` ile CPU/RAM/GPU; NVML başarısızken `gpu=0.0`; sabit `latency=0` |
| `HudPanels`/`App` | Model adını **animasyon durumundan** türetiyordu; `REASONING · CLOUD`; `EDGE-TTS` (gerçek motor Piper); sabit Tailscale IP |
| `App.jsx` | `connected && X.length ? X : fake` — gerçek akış boşsa demo veri; sahte mikrofon, hiç olmamış tool çağrıları, vault 2847, senaryolu diyalog, animasyondan türetilen "meşgul" ajanlar |
| `useJarvisSocket` | metrics'i uydurma değerlerle seed'liyordu |
| `cli.py` | `CLOUD_POLICY=off` iken Vertex ilan ediyordu; menüde "Qwen2.5 7B" (gerçek: qwen3:8b); emekli `text-embedding-004`; yerel kurulumda gereksiz API-key uyarısı |

**psutil bu makinede gerçekten kurulu değil** — sunucunun kendi açılış logundan doğrulandı. Yani
owner'ın HUD'da bugüne dek gördüğü her metrik uydurmaydı; varsayımsal bir dal değil, canlı yolun
kendisiydi.

Bağlanacak gerçek veri artık var: `ws.py` `model_status` yayınlıyor,
kaynağı `JarvisAgent.last_turn_trace` (yeni ölçüm yok — `turn_summary` zaten "cevabı hangi çağrı
yazdı"yı çözüyor). Tek etiket sözlüğü `jarvis/providers/labels.py` + renderer ikizi
`lib/display.js`; iki sözlük **zaten ayrışmıştı** (runtime `ollama`, menü `local` diyor).

**Ayrıca Faz 0'da:**
- Async sezgisi gündelik Türkçeye çıplak substring eşliyordu → `grafik`/`rapor`/`finansal`/
  `araştır`/`3d` çıkarıldı, eşleşme kök-çıpalı + diakritik-fold (`tool_router._fold` yeniden
  kullanıldı). HUD ayrıca `force_sync` gönderiyor.
- **Metin modunda markdown açıldı.** Ses kuralı koşulsuz yükleniyordu çünkü `prompt_loader` tüm
  `core/*.md`'yi glob'luyor; `PromptContext.surface` mevcut `transport` id'sinden türetiliyor.
  **Bu yarım kalsaydı hiç açmamaktan kötü olurdu** — Transcript `{t.text}` basıyordu, yani owner
  ham `**kalın**` ve `| tablo |` görecekti. `lib/markdown.jsx` cevapları **React elementi** olarak
  render ediyor, asla `dangerouslySetInnerHTML` değil (model çıktısı güvenilmez sayfa/dosya
  alıntılıyor).
- `scripts/start_jarvis.ps1` + `.cmd`: çift tıkla Ollama'yı HTTP ile yokla (process kontrolü asılı
  bir listener'ı sağlıklı sayardı), API'yi başlat, HUD'u aç.

**Canlı doğrulama (yalnız test değil):** API + HUD gerçek komut çubuğundan uçtan uca sürüldü.
*"bugün ayın kaçı"* → doğru tarih; grafik isteği **interaktif** cevaplandı (arka plana düşmedi) ve
PNG'yi bildirdiği yolda gerçekten üretti; tablo isteği gerçek `<table>` olarak render edildi
(2 başlık + 6 hücre, ham `|` yok). Routing `qwen3:8b · Ollama · fast`, round-trip gerçek 13324 ms,
ve canlı DOM'da `Gemini|Vertex|CLOUD ROUTE|EDGE-TTS|Tailscale` taraması **boş** döndü.

**Guard testinin ayırt etme gücü ölçüldü, varsayılmadı:** enjekte edilen 8 regresyonun 8'i de
kırmızıya döndürüyor. 8.'si guard'ın kendi kusurunu açığa çıkardı — bir kontrol ham dosyayı
okuyordu ve rozet metnini silen mutasyonu geçiriyordu, çünkü yakındaki bir yorum aynı dizgiyi
alıntılıyordu. Artık yorumları soyuyor.

## CI — canlı bakın, ama bilinen durum şu

`gh run list --branch langgraph-migration` ile bakın. Bu oturumda öğrenilen iki şey:

1. **`gh run list`'in "success" dediği bir koşuda bir job başarısız olabilir.** 2026-07-25 koşusu
   listede success görünüyor ama `--json jobs` ile bakınca **mobile job'ı failure**. Job durumuna
   bakın, satır özetine değil.
2. **`mobile` job'ı en az 2026-07-25'ten beri kırmızı** — 71 sorunun tamamı info/warning
   (65× `withOpacity` deprecation, + `assets/fonts/` ve `assets/wake/` git'te hiç yok; boş dizin
   izlenmiyor). Kod regresyonu değil, ve owner mobil yüzeyi geriye aldı. **Owner kararı bekliyor.**

**`python` job'ı 07-25'te geçip 07-31'de kaldı — bu gerçek bir regresyondu ve düzeltildi.**
`test_explicit_output_is_confined_to_the_workspace` **ortama bağlıydı**: `files._resolve()` ev
dizini altındaki her yolu bilerek kabul ediyor (Desktop/Downloads erişimi bu), dolayısıyla
`../escape.xlsx`'in reddedilip reddedilmemesi pytest'in tmp dizininin ev dizinine göre nerede
olduğuna bakıyordu.

```
bu makine   TEMP=C:\Temp                            -> ev DIŞINDA -> red  -> test geçer
CI runner   TEMP=C:\Users\RUNNERADMIN\AppData\...   -> ev İÇİNDE  -> kabul -> test kalır
```

Aynı kod, zıt sonuç — ve varsayılan Windows kurulumunda (temp profil altında) herkes için
kalırdı. Test, yol politikasını değil işletim sisteminin temp'i nereye koyduğunu ölçüyormuş.
`jarvis_home` fixture'ı ile `JARVIS_HOME` sabitlendi (kaçış hedefi artık her hostta ispatlı
şekilde ikisinin de dışında), test **gerçek politikaya göre yeniden adlandırıldı**, ve politikanın
diğer yarısını belgeleyen bir eş test eklendi (ev dizini içi **bilerek** serbest — biri
`_resolve()`'u "düzeltip" gerçek kullanımı kırmasın diye).

## Test / lint (2026-07-31, bu oturumun sonunda çalıştırıldı)

```powershell
.venv\Scripts\python.exe -m pytest -q                     # 1722 passed, 5 deselected, 3 dk 47 sn
.venv\Scripts\python.exe -m ruff check jarvis scripts tests  # All checks passed!
npm test    --prefix electron                             # 27 passed (13 chatStream + 14 markdown)
npm run build --prefix electron                           # 35 modül, hatasız
```

Yeni test dosyaları: `tests/test_no_synthetic_live_data.py` (14 test, kaynak taraması),
`electron/src/renderer/src/lib/markdown.test.jsx` (14 test, yarısı enjeksiyon güvenliği).
Güncellenenler: `test_chat_force_sync.py` (eski hatalı davranışı sabitleyen 2 test yeni
davranışa çevrildi), `test_workbook_export.py` (ortama bağlı test düzeltildi + eş test),
`test_confirmation_resume_trace.py` ve `test_reset_per_conversation.py` (stub'lar yeni
arayüzü taşıyor).

**Mutasyonla doğrulanan:** `test_no_synthetic_live_data.py` — 8 ihlal enjekte edildi, 8'i de
kırmızıya döndü. `test_workbook_export.py`'nin ortam bağımlılığı `_resolve()` doğrudan iki farklı
`JARVIS_HOME` ile çağrılarak kanıtlandı (aynı kod, zıt sonuç).

## SONRAKİ OTURUM — Faz 1: honesty kernel

Plan dosyasındaki Faz 1'i uygulayın. Özet:

1. `execution_contract_mode`: `off → shadow → enforce_reversible`, **global switch değil rollout
   metriğiyle** (`verification_total/verified/unverified/failed/false_positive`; "100 gerçek
   artifact işleminde 0 false block" görülmeden enforce yok).
2. **Sıfır-araç uydurma sınıfını kapat** — envelope listesi boşken cevap artifact/işlem iddia
   ediyorsa. Postcondition bunu yakalayamaz: postcondition *çalışmış bir aracı* doğrular.
   `summary.py`'deki `audit_claims()` (bugün yalnız gözlem) bu tek vakada karar verici olur.
3. **Typed evidence, regex sayı taraması DEĞİL** — *"Bunu 3 adımda yapabiliriz"* ile
   *"89 işlemin 45'i gelir"* aynı extractor'dan geçer. `EvidenceSet` (artifact/operation/facts).
4. Artifact bildirimini standartlaştır (~5 araç: `plot_data`, `finance('export')`,
   `report_compose`, `report_compile`, `workbook`) — `plot_data.output` bir **stem** olduğu için
   bugün doğrulanamıyor. Bugün yalnız `file_write` postcondition tanımlıyor
   (`tool_registry.py:130`).
5. Başarısızlıkta bir sınırlı onarım turu, sonra doğal dilde dürüst rapor
   (*"Efendim, istediğiniz dosyayı oluşturamadım"*). Sessiz düzeltme yok.

**Test rejimi (GPT düzeltmesi kabul edildi):** deterministik kod (Clock, resolver, postcondition,
WorkingSet patch) **bir kez** koşar; model davranışı içeren her şey **n=10–20** ve ikili geç/kal
değil **oran** ölçülür (başarı, uydurma, tool-call doğruluğu, gecikme p50/p95).

## Bu oturumda değişmeyen taşınan işler

- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- 7 direct-Gemini modülün shared gateway'e migrasyonu.
- Electron `npm audit`; canlı HUD E2E'nin **Electron penceresi** ayağı (bu oturumda HUD tarayıcı
  panelinden sürüldü — renderer'da sıfır Electron IPC bağımlılığı var, `window.jarvis` yoksa
  `127.0.0.1:8000`'e düşüyor; **Electron pencere kontrolü ve PTT kısayolu bu yolla test edilemez**).
- Alt+Space → `/voice/ptt/start`; wake-word modeli; alpha gate'i GEÇTİ'ye taşımak.
- Finans: boru hattı çalışıyor, tek gerçek kaynak PDF ekstre importu (mail kutusunda banka
  bildirimi yok — canlı doğrulandı). Owner ayda bir manuel importu kabul etti.
