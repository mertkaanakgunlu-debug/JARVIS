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

## Son oturum: 2026-08-01 — **Faz 2.5** + **Faz 2.75 (Paket A–F)**

**Durum tek cümlede:** dış review'ün altı Faz-3 blokerinin **hepsi koda karşı doğrulandı ve
kapatıldı**, ve bu sırada Faz 2.5'in kendi sonuçlarından biri **yanlış çıktı**.

## Sıra ve plan

Plan: `C:\Users\mertk\.claude\plans\c-users-mertk-downloads-jarvis-post-mvp-federated-kettle.md`
(Faz 0A + 0B + 1 + 2 + 2.5 bitti). **Sıra: Faz 3 — Daily Briefing MVP.**

## Review doğrulaması — uydurma bulgu çıkmadı

Review yazarı repoyu klonlayamadığını belirtmişti (raporun 39. satırı), yani bulgular GitHub
okumasına dayanıyordu. Sekizinin tamamı gerçek çıktı. Paketler bağımlılık sırasında yapıldı:
**C → E → F → D → A → B.**

## Bu oturumun en önemli bulgusu — Faz 2.5'in bir sonucu yanlıştı

`tool_router` slotları **rota sırasıyla** dolduruyordu. *"…satis.csv…oku ve grafiğini çiz"*
`[files, data]`'ya gidiyor; `files`'ın 7 aracı 8 slotun 7'sini alıyor, `data`'ya **tek** slot
kalıyor (`data_analyze`) ve **`plot_data` hiç sunulmuyor**.

Faz 2.5 bu senaryoyu *"model iki bağımlı çağrılık zinciri tamamlayamıyor, 0/10, her iki katmanda
da"* diye ölçmüş ve *"çözüm Faz 4'ün Working Set'i"* demişti. **Yarısı yanlıştı.** Aynı harness,
aynı fixture, n=10:

| | önce | sonra |
|---|---|---|
| `fast` | 0/10 · p50 31.9 sn | **8/10** · p50 **19.5 sn** |
| `reasoning` | 0/10 · p50 101.3 sn | **8/10** · p50 **47.0 sn** |

Model grafiği çizemiyor değildi — **çizen aracı hiç görmüyordu.** Artık düşen araçlar, onları
düşüren rotayla birlikte loglanıyor: *"model bu aracı gördü mü"* sorusu loglardan
cevaplanamıyordu, ve bu tam olarak bu hatanın bir tur boyunca fark edilmemesinin sebebiydi.

## Paket paket ne yapıldı

**C — ExecutionContext.** `interactive` bir denylist'ti (`not transport.startswith("monitor-")`),
yani kimsenin düşünmediği her transport "insan var" sayılıyordu — `task-async` dahil. Arka plan
işinin takvim create'i Faz 2'nin güven indirimini alıyordu: `risk=3`, `confirm=False`, **kimse
izlemiyor**. Artık giriş noktasında bir kez hesaplanıp state'te taşınıyor; tanınmayan her şey
güvenli köşeye düşüyor. `origin` ≠ `unattended`: proaktif tur salt-okunur kısıtlanıyor, ama
kullanıcının istediği arka plan işi raporunu **yazabiliyor** — ikisi de test edildi.

**E — action seviyesinde risk.** `todo`/`schedule`/`finance` araç düzeyinde L2/`local_write`
olduğu için okumaları da yazma sayılıyordu, ve proaktif kısıt `risk>=2 and not confirm` ile
ateşliyor. Ölçüldü: `todo("list")`, `schedule("list")`, `finance("summary")` proaktif turda
**bloklu**, `google_calendar("list")` ve `gmail("list_unread")` geçiyordu. Artık `ToolSpec.actions`
tek kaynak; **yalnız okumalar** bildiriliyor, listelenmemiş action aracın daha katı riskini
koruyor. `todo("done")` = tamamlandı işaretle (yazma), `schedule("done")` = tamamlananları listele
(okuma) — aynı kelime, zıt işlem, testi var.

**F — router.** Yukarıdaki slot bulgusu + jenerik fiiller (`\blistele`, `\bara\b`) alan
üretiyordu (16 istekte 4) + `\bpdf\b` kelime sınırı istediği için *"PDFteki"* hiç eşleşmiyordu ve
model **PDF okuyabilen hiçbir araç görmüyordu**. `\bcsv`/`\bexcel` aynı boşluğa sahipti **ve**
yanlış alandaydı; araçlarıyla birlikte `files`'a **taşındı** (kopyalanmadı — iki yerde olmak
kelimeyi iki kez sayardı).

**D — `unknown_outcome`.** `execution_may_still_be_running=true` Faz 3'ten beri vardı — tam
`retryable=true`'nun altında. *"Bu olmuş olabilir"* ile *"tekrar dene"*yi aynı mesajda söylemek
aynı mailin iki kez gitmesidir. Artık dış yazmada zamanaşımı `retryable=false` + `outcome=unknown`
veriyor ve modele **ne söyleyeceğini** de söylüyor: *sonuç doğrulanamadı* — *başarısız* değil.

**A — terminal cevap.** `chat_stream` `"".join(chunks)`'ı geçmişe yazıyordu; bu, kritik revizyon
olduğunda **reddedilen taslak + yerine geçen** demek, doğrulama onarımı ise akışa hiç girmediği
için geçmişe **hiç** ulaşmıyordu. Artık graph'ın terminal `response`'u kalıcı oluyor;
`__jarvis_final__` işaretçisi ekranı düzeltiyor.

**B — conversation sabitleme.** Bekleyen onay, turun graph config'ini ve trace recorder'ını
sabitliyordu ama conversation'ını değil: A onay beklerken B mesaj gönderirse, A'nın onayladığı
cevap **B'nin geçmişine** yazılıyordu. `/chat/confirm` hiç conversation almıyordu, yani *"bekleyen
neyse onayla"* herhangi bir istemcinin başkasının L3 yazması hakkında söyleyebileceği bir cümleydi.
`TaskExecutor` da kaynak session'ı **worker başladığında** okuyordu, istek gönderildiğinde değil.

## Doğrulama (2026-08-01, bu oturumda çalıştırıldı)

```powershell
.venv\Scripts\python.exe -m pytest -q                        # 2549 passed, 5 deselected, 4 dk 01 sn
.venv\Scripts\python.exe -m ruff check jarvis scripts tests  # All checks passed!
npm test    --prefix electron                                # 27 passed
npm run build --prefix electron                              # 35 modül, hatasız
```

**Mutasyon turları:** C **10/10** · E **11/11** · F **10/10** · D **10/10** · B **9/9**
(+ Faz 2.5 rol router'ı 22/22, session id düzeltmesi 3/3).

**İki mutasyon turu önce kendisi hataliydi.** Paket F'nin ilk koşusu "hepsi SURVIVED" dedi çünkü
bir heredoc bir ters bölü seviyesi yemiş ve her mutasyon `\b` yerine **backspace karakteri**
yazmıştı — hiç mutasyon uygulanmamıştı. **Mutasyon uygulamayan bir harness mükemmel kapsam
raporlar.** O günden sonra bütün mutasyon betikleri dosyaya yazılıyor, heredoc'a değil.

Hayatta kalan mutasyonların çoğu **gerçek boşluktu**, eşdeğer mutant değil: yerel bir okumanın
`external_read` değil `local_read` dediğini hiçbir test iddia etmiyordu, `get_action_spec` doğrudan
hiç çağrılmıyordu, defter satırının hiç testi yoktu, ve `may_act_without_asking`'in üç koşulundan
hiçbiri tek tek sınanmıyordu.

## Bilinçli olarak yapılmayanlar

- **`ConversationRuntime` refactor'ü (review'ün Paket B önerisinin tam hali).** Per-conversation
  history/turn/lock, yalnız graph ve model havuzu paylaşımlı. Yapısal son durum bu ve istemciler
  arasında **gerçek paralellik** de kazandırırdı; bu oturum üç doğruluk deliğini onsuz kapattı,
  global kilit turları sıraya sokmaya devam ediyor.
- **Uzak mutabakat (reconciliation).** Gmail'in gönderilenlerini veya Calendar'ı sorgulayıp
  "gerçekten oldu mu" demek Paket D'nin doğru devamı, ama **canlı credential olmadan
  doğrulanamaz**; hiç gerçek API'ye karşı koşmamış bir hook yetenek değil iddiadır.
- **P1-1 tanımsız araç fail-closed değil.** `policy_guard.evaluate()` ToolSpec bulamazsa
  `allowed=True` + `requires_confirmation=True` dönüyor — "fail safe" değil "confirm by default".
  Doğrulandı, bu oturumda düzeltilmedi.
- **P1-4 `local` rolü gerçekte local-only değil.** `cloud_policy=auto` altında `get_llm("local")`
  bulut fallback katmanı istiyor. Doğrulandı; owner'ın konfigürasyonunda (`off`) etkisiz, ama
  isimlendirme tuzağı gerçek.
- **P1-2 critic hata durumunda sessizce kabul ediyor** (fail-open) ve **critic turun rolünü
  izlemiyor** — `llm_pro` `graph.py`'de bir kez kuruluyor. Ölçüldü: `fast` bir takvim-oluşturma
  turunda **10/10** reasoning katmanında bir çağrı harcanıyor. Hangi modelin cevabı yargıladığını
  değiştirmek bir kalite kapısını değiştirmektir, kendi ölçümünü ister.
- **`/model` pin'i artık daha çok tura ulaşıyor** (Faz 2.5'in yan etkisi). `pin_cloud_model` yalnız
  `fast` rolüne uygulanıyor. `cloud_policy="off"` altında yapısal olarak imkânsız (doğrulandı).

## Çözülmemiş — tekrarlanamayan test flake'i

Bir noktada dört tam koşudan biri 5-6 hata verdi, diğerleri temiz. Yakalanan tek isim
`test_todo_bg_analysis.py::test_bg_task_is_tracked_then_pruned_after_completion`, izole halde 3/3
geçiyor. Teşhis: `graph_tools._todo_bg_tasks` **modül düzeyinde global** ve o test başlangıçta boş
olmasını şart koşuyor; aynı dosyadaki ilk test arka plan görevi bitmeden dönüyor, yani done-callback
henüz budamamış olabilir. `pytest-randomly` **kurulu değil**, sıra rastgele değil — değişken
zamanlama. **Kapatılmadı.** Tekrar görülürse o dosyaya `_todo_bg_tasks`'i her testten önce boşaltan
bir autouse fixture eklemek doğru düzeltme.

## Oturum sonu

**16 iş commit'i ve bu kapanış HANDOFF commit'i** (Faz 2.5 ve Faz 2.75 birlikte; ara HANDOFF/docs
commit'leri de bu sayıya dahil). Her paket kendi commit'i, bağımlılıklar ileri akıyor, hiçbir ara
commit kırık ağaç bırakmıyor. Oturum sonunda **local == origin senkrondu**.

**Yeni araç: `scripts/role_ab.py`** — aynı sorguyu iki rolde koşup **gecikme ve doğruluğu birlikte**
raporluyor. `JARVIS_HOME` repo dışında bir temp dizin (`ROLE_AB_HOME` ile ezilir), Calendar yerine
capture nesnesi, fixture'ı kendi kuruyor.

```powershell
.venv\Scripts\python.exe scripts\role_ab.py --runs 10
.venv\Scripts\python.exe scripts\role_ab.py --runs 10 --arm fast --only multi_step
```

## SONRAKİ OTURUM — Faz 3: Daily Briefing MVP

Plan dosyasındaki Faz 3, 1. kabul kilometre taşı: *"JARVIS bugün neler var"* → saate duyarlı,
**uydurmasız** brifing. Brifing bir LLM workflow'u **değil**: `DailyBriefingService` deterministik
`BriefingFacts` üretir, LLM yalnız anlatır.

Faz 2.75'in oraya bıraktıkları:

- **Proaktif yol artık takvim, mail, todo, schedule ve finansı okuyabiliyor** (Paket E). Faz 3'ün
  doğrudan engeli buydu.
- **Gözetimsiz tur semantiği tipli** (Paket C) — zamanlanmış brifing `origin`'ini alır, transport
  string'i tahmin etmez. Push bildirimiyle teslim edilen bir brifing `can_confirm=True` ama
  `human_present=False` olabilir; `ExecutionContext` bunu ifade edebiliyor ve testi var.
- **Gate metrikleri için harness hazır** — Faz 3'ün eşiği (medyan < 5 sn **ve** p95 < 10 sn **ve**
  0 uydurma kalem) tam olarak `scripts/role_ab.py`'nin raporladığı iki eksen.
- **Brifingin `tool_router`'da kendi alanı yok**, eklenmesi gerekecek (hava + haber araçları da bu
  fazda geliyor).
- `jarvis/clock.py` Faz 2'de kuruldu ve brifing hâlâ onun **bağlanmamış** tüketicisi.

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
- **`main` kaç commit geride sayısını okumayın, türetin** — her commit'te bayatlıyor:
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`.
