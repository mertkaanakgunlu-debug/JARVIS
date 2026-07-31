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

## Son oturum: 2026-07-31 — Post-MVP **Faz 1: honesty kernel**

**Durum tek cümlede:** *"JARVIS yaptığını söylediği şeyi gerçekten yaptı mı?"* sorusu artık koda
bağlı — artifact üreten araçlar ürettikleri dosyayı **bildiriyor**, postcondition o dosyayı diskte
**doğruluyor**, ve turun son cevabı kanıta karşı sınanıyor. Varsayılan `shadow`: her şey ölçülüyor,
kullanıcının cevabına dokunulmuyor.

## Plan ve önceki faz

Plan: `C:\Users\mertk\.claude\plans\c-users-mertk-downloads-jarvis-post-mvp-federated-kettle.md`
(Faz 0A + 0B bir önceki oturumda bitti). Sıra: **2** Clock/temporal/entity → 2.5 rol seçimi →
3 Sabah Brifingi → 4 Working Set → 5 proaktif → 6 hafıza → 7 render/harita → 8 web_download →
9 Kimi K3.

## Ne yapıldı

**Yeni bir grounding sistemi kurulmadı** — GPT'nin plan düzeltmesi doğruydu.
`VerifiedExecutionSummary` zaten vardı, iki kapı kapalıydı, ikisi de açıldı.

1. **Artifact bildirimi** (`jarvis/execution/artifacts.py`). Bu faza kadar **kayıttaki tek
   postcondition'lı araç `file_write`'tı** ve sebebi ihmal değil yapıydı: çıktı yolu **argüman
   olan** tek araç o. `plot_data`'nın `output`'u bir **stem** (çakışmada `_1`/`_2` ekleniyor),
   `report_write` yolu `title`'dan türetiyor, `finance('export')` varsayılanı hesaplıyor.
   Artık araç dosyayı **gerçekten diske yazdığı anda** yolunu bildiriyor; bildirim
   `ToolMessage.artifact` üzerinden **bant dışı** gidiyor, yani **modele giden dönüş dizgisi bayt
   bayt aynı** (bir docstring cümlesinin gate'i 10/10 → 0/10 yaptığı kayıt yüzünden bu bilinçli).
2. **`declared_artifacts_exist` postcondition'ı** — `plot_data`, `report_write`, `report_compose`,
   `report_compile`, `finance`. Hepsi yerinde → `confirmed`; **bildirilmiş ama yoksa → aracın kendi
   başarı beyanı "doğrulama BAŞARISIZ"a düşürülüyor** ve kullanıcıya çıkıyor; hiç bildirim yoksa
   → `unverified`, asla bedava geçiş.
3. **Karşılıksız iddia gate'i** (`jarvis/execution/evidence.py`). Yalnız **kanıtlanabilir
   çelişkide** ateşliyor: bu turda bildirilmemiş **ve** diskte olmayan bir dosya adı, ya da hiçbir
   aracın çalışmadığı turda bir **yan etki** iddiası. Sayı taraması **yok**.
4. **Rollout metriği** (`jarvis/execution/rollout.py`) → `data/execution_verification.jsonl`.
   `enforce_gate_status()` planın eşiğini sayıya çeviriyor: 100 gerçek artifact işlemi, 0
   bildirilmiş yanlış blok. **Okumak için:**

   ```powershell
   .venv\Scripts\python.exe scripts\verification_status.py
   ```

   Yanlış bir blok görülürse `--mark-false-positive "sebep"` ile kaydedilir — bu sayı **asla
   türetilmiyor** (kod tespit edebilseydi gate zaten ateşlemezdi), yani 0 "bildirilmedi" demek,
   "olmadı" demek değil. Owner'ın gerçek `data/`'sı şu an **boş**: canlı ölçümler ayrı bir
   `JARVIS_HOME` altında koşturuldu, sayaç gerçek kullanımla dolacak.

## Oturumun en önemli bulgusu — gate yanlış düğümdeydi

Gate ilk olarak `compose_node` içine yazıldı (cevabın yazıldığı yer, bariz ev). **Canlı graph
bağlantılarını okuyunca compose'un END'e giden her yolda olmadığı görüldü:**

```
agent -> critic -> END                                (hiç araç çağrılmadı)
agent -> ... -> tools -> ... -> compose -> critic -> END
agent -> ... -> tools -> ... -> agent  -> critic -> END
```

İlk satır sıradan bir sohbet turu — ve *"dosya iddia etti, araç çağırmadı"* **en çok tam olarak o
şekilde** olur. Yani kontrol **kendi manşet vakasına yapısal olarak kördü**, ve bütün testler
geçiyordu çünkü hepsi düğümü doğrudan çağırıyordu.

Çözüm: **terminal `verification_node`**, END'e giden her yolda. Bedava gelen ikinci fayda: tur
başına **tam bir** gate değerlendirmesi ve tek bir operasyon satırı seti — yoksa terfi metriği
turların bilinmeyen bir kesri üzerinden ölçülürdü. `tests/test_unbacked_claim_gate.py` artık
**edge haritasının kendisini** doğruluyor.

`jarvis/graph/nodes.py` bu taşımadan sonra **saf ekleme**: 220 satır eklendi, 0 silindi —
`compose_node` bayt bayt eski hâlinde.

## Diğer bulgular (hepsi doğrulamadan çıktı, özellik yazarken değil)

- **Onarım turu akışa sızacaktı.** İki taslak aynı düğümden aynı `langgraph_step`'te geliyordu, yani
  BUG-12'nin adım-sınırı ayracı ikisini ayıramıyordu. `REPAIR_STREAM_TAG` eklendi.
- **Sistemin kendi durum bloğu gate'i tetikleyebilirdi.** enforce'ta `compose_node` kendi kod
  yazımı bloğunu ekliyor ve doğrulama-başarısız vakada o blok **bilerek diskte olmayan** bir yolu
  alıntılıyor. Artık `summary.USER_STATUS_MARKER`'dan bölünüp yalnız modelin yarısı yargılanıyor.
  (Negation guard bunu zaten yakalıyordu — ama iki alakasız metnin tesadüfen örtüşmesiyle.)
- **Akan bir taslağı geri alamayız.** enforce'ta blok, state'i ve geçmişi düzeltir ama HUD'un
  gördüğü tokenları geri almaz. `docs/SAFETY.md`'de **terfi engeli** olarak yazıldı.
- **`tests/conftest.py`'ye autouse fixture eklendi.** Varsayılanın `shadow`'a çıkması, gerçek tur
  süren her mevcut teste yeni bir yan etki verdi: owner'ın gerçek
  `data/execution_verification.jsonl`'ına yazmak — yani terfi kararının dayandığı sayıları bozmak.
- **Mutasyon turu iki gerçek boşluk buldu:** negation guard'ın **hiç testi yoktu** (iki "dürüst
  hata" satırı ona ulaşmadan erken dönüyordu), ve o guard'daki çıplak `\byok\b` gerçek bir recall
  deliğiydi ("sorun yok" tek başına gate'i kapatıyordu).

## Test / lint (2026-07-31, bu oturumun sonunda çalıştırıldı)

```powershell
.venv\Scripts\python.exe -m pytest -q                        # 1836 passed, 5 deselected, 4 dk 50 sn
.venv\Scripts\python.exe -m ruff check jarvis scripts tests  # All checks passed!
npm test    --prefix electron                                # 27 passed
npm run build --prefix electron                              # 35 modül, hatasız
```

Yeni: `test_declared_artifacts.py` (24), `test_declared_artifact_postcondition.py` (23),
`test_unbacked_claim_gate.py` (57), `test_rollout_metrics.py` (11).
**Mutasyon: 16/16 enjekte regresyon yakalandı** (`verify`'ı critic'in END yolundan almak ve cevabı
yalnız `state["response"]`'tan okumak dahil).

## Canlı ölçüm (gerçek qwen3:8b, gerçek graph, n=10/senaryo, 40 tur)

`JARVIS_HOME` ayrı dizine alındı: dosyalar **gerçekten** yazıldı, owner'ın `data/`'sına dokunulmadı.

| Senaryo | Sonuç |
|---|---|
| A — artifact zinciri | **10/10 her eksende**: araç · bildirim · `verified` · `confirmed` · PNG diskte · cevap **gerçek yolu** yazdı · gate sessiz. p50 21.0 sn, p95 39.8 sn |
| B — revizyon yemi (önceki grafik yok) | **0/10 uydurma** |
| C — başarısızlık dürüstlüğü (olmayan CSV) | **0/10 uydurma**, 8/10 hatayı açıkça bildirdi |
| D — yanlış-pozitif probu | **0/10 gate tetiklenmesi** |

**En değerli sayı tabloda değil:** B+C'nin 20 turunda model **15 kez dosya adı andı**, **0 kez
tamamlama iddiası** yaptı. "Yol anıyorsa uydurmuştur" diyen naif bir dedektör orada **15 yanlış
pozitif** üretirdi.

Gate'in ateşleme yolu da canlı sürüldü (composer sözleri zorlandı, gerisi gerçek): shadow tespit
eder/dokunmaz · enforce + temiz onarım → onarılmış cevap · enforce + kirli onarım → dürüst rapor.
**Kapsam canlı doğrulandı:** `conversation_no_tools` / `artifact_turn` / `failing_tool` →
üçünde de tur başına **tam 1** `claim_gate` satırı (taşımadan önce ilki 0 üretirdi).

## SONRAKİ OTURUM — Faz 2: Clock + temporal + entity

Plan dosyasındaki Faz 2. Takvim hatasının mekanizması doğrulanmış durumda:
`calendar.py:94` `datetime.now(timezone.utc)` ile "yarın" hesaplıyor, `:329` naive wall-clock +
`timeZone: Europe/Istanbul` gönderiyor — **İstanbul'da 00:00–03:00 arası UTC hâlâ önceki gündür**,
yani "yarın" bir gün erken çözülüyor. Regresyon testi tam olarak
`FrozenClock(2026-07-31 23:30 Europe/Istanbul)`.

Sıra: injectable `Clock` → temporal resolver (model yalnız `date_expression` üretir, timestamp'i
**kod** hesaplar) → entity resolver (güven bantları: ≥0.95 otomatik, 0.75–0.95 sor, <0.75 dokunma)
→ başlık/açıklama disiplini → confidence-based takvim onayı.

## Faz 1'den taşınan, bilinçli olarak yapılmayanlar

- **`enforce_reversible` açılmadı** — varsayılan `shadow`. Terfi bir ölçüme bağlı:
  `rollout.enforce_gate_status()` → 100 gerçek artifact işlemi, 0 bildirilmiş yanlış blok.
  **Ek terfi engeli:** akan bir taslağı geri alamama (yukarıda).
- **Doğrulama 5 araçta, 36'da değil.** Diğerleri dürüstçe "bağımsız doğrulanmadı" diyor.
- **`EvidenceSet.facts` boş** — hiçbir araç yapılandırılmış olgu döndürmüyor; sayı taramasıyla
  doldurulmadı (dış review'ün reddettiği tam olarak buydu).
- **Bir araç yazmadığı yolu bildirebilir** — bildirim aracın dönüş değeriyle aynı güvende.
  Postcondition dosyanın varlığını kanıtlar, o çağrının onu ürettiğini değil.
- `audit_claims()` bu vaka için **kullanılamadı** — `summary.any_failed` olmadan erken dönüyor,
  boş operasyon listesi bunu asla sağlayamaz. Kendi yerinde kaldı.

## Önceki oturumlardan taşınan, değişmeyen işler

- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- 7 direct-Gemini modülün shared gateway'e migrasyonu.
- Electron `npm audit`; canlı HUD E2E'nin **Electron penceresi** ayağı (pencere kontrolü ve PTT
  kısayolu tarayıcı panelinden test edilemez).
- Alt+Space → `/voice/ptt/start`; wake-word modeli; alpha gate'i GEÇTİ'ye taşımak.
- Finans: boru hattı çalışıyor, tek gerçek kaynak PDF ekstre importu. Owner ayda bir manuel
  importu kabul etti.
- **CI:** `gh run list --branch langgraph-migration` ile **job düzeyine** bakın
  (`gh run view <id> --json jobs`). `mobile` job'ı `continue-on-error: true` (owner kararı,
  2026-07-23) ve **en az 2026-07-25'ten beri başarısız** — 71 bulgunun tamamı info/warning
  (65× `withOpacity`, + `assets/fonts/` ve `assets/wake/` boş dizin olduğu için git'te yok).
  Kod regresyonu değil, bloke etmiyor. **Owner kararı bekliyor.**
