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

## Son oturum: 2026-07-24 — SES BORU HATTI SERTLEŞTİRME (GPU + onay + Türkçe + çıkış), CANLI TEST EDİLDİ; ASIL BLOCKER ARTIK SES-GİRİŞİ YAKALAMA GÜVENİLİRLİĞİ

**Durum tek cümlede:** Owner canlı `--voice` testi yaptı; bu tur hem düzeltmeleri doğruladı hem de
2 gizli bug'ı daha açığa çıkardı (onay döngüsü ancak gerçekten konuşarak kanıtlanır). GPU açıldı,
onay/Türkçe/çıkış düzeltildi ve test edildi — AMA canlı test **asıl blocker'ın artık ses-girişi
yakalama olduğunu** gösterdi ve owner bir strateji değişikliği yönlendirdi (önce metin, sonra ses).

### Bu oturumda yapılan + CANLI DOĞRULANAN

Plan dosyası: `C:\Users\mertk\.claude\plans\starry-wibbling-meerkat.md` (owner onayladı, 7 revizyonla).

1. **GPU açıldı (hız — owner'ın 1. önceliği).** RTX 4070 boştaydı, Whisper CPU'da (10-20 sn/cümle).
   ctranslate2 4.8.1 model-yükleme anında cuBLAS + cuDNN 9 istiyor (sürücü tek başına
   `get_cuda_device_count()`'i geçiriyor ama gerçek yükleme patlıyor → CPU fallback). `nvidia-cublas-cu12`
   + `nvidia-cudnn-cu12` (9.25) kuruldu — **kod değişikliği gerekmedi**, mevcut `_register_cuda_dll_dirs()`
   PATH mekanizması çalıştı. Zorlanmış-CUDA smoke: gerçek yükleme + transkripsiyon **RTF ~0.13 (~8×
   gerçek-zaman)**. Wheel'lar (~1.3 GB) ayrı `requirements-gpu-windows.txt`'te — CI'ın her run'da
   kurduğu lockfile'a bilinçli olarak KONMADI. **Canlı testte doğrulandı**: "CUDA libraries unavailable"
   satırı çıkmadı, Türkçe cümleler hızlı+doğru yazıldı.
2. **Onay arm-before-speak yarışı (CANLI).** `set_pending_confirmation()` soru TTS'inden SONRA
   çağrılıyordu; soru sırasında barge-in turu iptal edince arm atlanıyor, sonraki "evet" yeni tur
   oluyordu. Tek ortak `arm_and_speak_confirmation()` helper'ı (jarvis/voice/session.py) her
   await'ten ÖNCE arm ediyor; üç sesli yol (cli / voice_api / resolve_confirmation) buna bağlandı.
   **Canlı testte doğrulandı**: "Evet"/"Hayır" artık onay çözümüne ulaşıyor (audit'te user_denied
   satırları — önce hiç yoktu, alakasız tura gidiyordu).
3. **`is_affirmative("Evet.")` False dönüyordu (CANLI, audit-log ile yakalandı).** STT sondaki
   noktayla "Evet." üretiyor; tam "evet" eşleşmesi kaçırıyor → onay `deny:Evet.` olarak yönlendi
   (`data/audit_log.jsonl`: `user_denied reason "Evet."`). Artık sondaki noktalama (`.,!?…`)
   temizleniyor — exit-regex ile aynı sınıf düzeltme. (Bu bug'ı arm-before-speak düzeltmesi AÇIĞA
   ÇIKARDI: önce "Evet" hiç is_affirmative'e ulaşmıyordu.)
4. **Türkçe decoder kilidi.** `transcribe()` `language=` geçmiyordu → kısa Türkçe yanlış algılanıp
   Rusça'ya kayıyordu (`разденьемся`). `whisper_language: Literal["auto","tr","en"] = "tr"` decode'u
   sabitliyor; `_TR_CHARS` backstop yalnız "auto"da. **Canlı testte doğrulandı**: Rusça yok, Türkçe doğru.
5. **"Gülen" çıkış varyantı.** "güle güle" → Whisper "Gülen.", 0.82 fuzzy eşiği kaçırıyor. Kontrollü
   `fullmatch` regex eklendi (bare "Güle" hariç). **Not: aşağıdaki P0'a bak — canlı testte "güle
   güle" HİÇ yakalanmadı (transkript üretilmedi), yani bu düzeltme doğru ama asıl sorun girişte.**
6. **Telemetri (tahmin değil ölçüm).** Her transkripsiyonda `[stt] ... actual_device=cuda ... rtf=...`;
   onay yaşam döngüsünde `[confirm] registered/armed/claimed/expired age_sec/ttl_sec`.

**Test/lint (2026-07-24, bu değişikliklerle koşuldu):**
```powershell
python -m pytest -q       # 1314 passed, 0 failed (206s) — FULL suite (23+ yeni test)
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py scripts/alpha_gate.py   # All checks passed! (ruff 0.15.21 pinned)
# GPU smoke (gerçek model, gerçek Settings): actual_device=cuda, rtf=0.123
```

### 🔴 ASIL BLOCKER (canlı testte ortaya çıktı) — SES GİRİŞİ YAKALAMA GÜVENİLİRLİĞİ

Cümleler **düşüyor**, yanlış işlenmiyor: owner 3 kez "evet" demek zorunda kaldı ama audit'e yalnız
1'i düştü; "güle güle" iki kez söylendi, **hiç yakalanmadı** (Ctrl+C ile kapatıldı). Ayrıca
**"listening" göstergesi yok** — kullanıcı JARVIS'in dinleyip dinlemediğini anlayamıyor. Bu bir
VAD / continuous-listen / mic-gating sorunu, mantık sorunu DEĞİL (yukarıdaki onay/çıkış/dil
düzeltmelerinin hepsi izole olarak doğru). Detay + aday açılar ROADMAP.md'nin "Voice pipeline
follow-ups" bölümünde (P0). Wake-word modeli de hâlâ yüklenmiyor (ayrı openwakeword sorunu →
continuous-listen'e düşüyor).

### STRATEJİ (owner yönlendirmesi, 2026-07-24): ÖNCE METİN

Ses-giriş sorununa daha fazla dalmadan önce **beynin metin üzerinde kusursuz olduğunu kanıtla**
(alpha gate + `--profile test` harness'ı bunu zaten ölçüyor — owner'ın gerçek günlük komutlarına
genişlet). Metin kusursuz olunca kalan boşluk TAMAMEN ses/audio boru hattıdır ve izole halde
mantık bug'larıyla karışmadan çok daha kolay debug edilir. (ROADMAP'te işaretlendi.)

### Commit ve push durumu

Bu oturumda **3 ses-sertleştirme iş commit'i** + bu kapanış docs commit'i:
- `c0eb92c` — `feat(voice): enable GPU for Whisper STT + language lock, model/beam config, telemetry`
- `aeb2d6b` — `fix(voice): arm confirmation before speaking + accept punctuated affirmatives`
- `2ee56e5` — `fix(voice): recognize the "gülen" ASR exit variant`

Kalıcı kural gereği bu kapanış docs commit'inin kendi SHA'sı/CI'ı burada yok — uç CI'ına
`gh run list --branch langgraph-migration` ile bakın. Oturum sonunda local == origin senkrondu.
(`.claude/settings.local.json` her zamanki gibi hariç; `.env` gitignored, `APPROVAL_TTL_SEC=20`
test değeri kaldırıldı — commit'e girmedi.)

### Önceki turların özeti (aynı uzun oturum, detay: CHANGELOG.md + [[project-agent-runtime-rev2]])

Bu ses turundan ÖNCE aynı oturumda: atomik `claim_pending_confirmation()` + TTL-awareness düzeltmesi
(bir 3. review benim kendi yanlış "HMAC testi yeterli" gerekçemi yakaladı) + ruff sürüm-kayması CI
düzeltmesi — 5 commit (`ff357fc`..`b850346`) + docs `0fdd3fb`, hepsi push'lanıp CI-yeşil doğrulandı.

## SONRAKİ OTURUM — kalan iş (öncelik sırası)

1. **P0 — Ses girişi yakalama güvenilirliği** (yukarıda + ROADMAP). Cümle düşmesi + "listening"
   göstergesi. VAD/continuous-listen/mic-gating; `on_speech_started`/`on_mic_level` callback'leri
   zaten var ama CLI göstermiyor. Arayüz-tabanlı test (Electron HUD) ham-mic sorununu atlatabilir.
2. **Metin-önce güvenilirlik baseline'ı** (owner stratejisi). Alpha gate + `--profile test`'i
   owner'ın gerçek komutlarıyla koştur; `false_success_claim`'i 0'a indir.
3. Wake-word modeli neden yüklenmiyor (openwakeword) — ayrı, düşük öncelik.
4. Whisper beam 1 vs 5 A/B ölçümü (config hazır, default 5; GPU'da Türkçe corpus'la ölç — ROADMAP).
5. Weather aracı (yeni özellik, regresyon değil — ROADMAP'te tracked).
6. Branch ucunun CI'ı: `gh run list --branch langgraph-migration -L 3`.
7. Eski kalanlar: canlı HUD E2E (onay approve/deny tıklaması); `Memory()` testlerinin close() taraması;
   electron `npm audit` 8 advisory; 4 worktree branch; mobile flutter-analyze.

## Değişmeyen taşınan işler

- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
