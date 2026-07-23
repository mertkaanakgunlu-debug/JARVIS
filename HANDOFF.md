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

## Last session: 2026-07-23 (17. oturum) — FAZ 8 (SON FAZ) + ELECTRON CONFIRMATION UI

**Durum tek cümlede:** Agent Runtime rev.2'nin son fazı Faz 8'in offline yarısı komple inşa
edilip test edildi (5 yeni test dosyası, 234 yeni test; full suite **1246 passed, 0 failed**,
2026-07-23 canlı koşuldu; alpha kapısı artık çalıştırılabilir bir enstrüman ve mevcut şampiyon
kaydına dürüstçe **KALDI** diyor); ardından owner'ın seçtiği sıradaki iş — **Electron HUD'un
L3 confirmation prompt'u** — da aynı oturumda inşa edildi (SAFETY.md'nin en eski known-limit'i;
build + simüle-stream parser doğrulaması yapıldı, CANLI HUD E2E'si bilinçli olarak İDDİA
EDİLMİYOR — sıradaki manuel adım o).

**COMMIT DURUMU:** owner "commit + push et" dedi — bu oturumda **3 iş commit'i (`7d6a7af`
feat: Faz 8; `8b451dc` docs: Faz 8 kayıtları; `52d0b72` feat: Electron confirmation UI) ve bu
kapanış docs commit'i** push'landı; oturum sonunda local == origin senkrondu. Kalıcı kural
gereği kapanış commit'inin kendi SHA'sı/CI'ı burada yok — uç CI'ına `gh run list --branch
langgraph-migration` ile bakın. (`.claude/settings.local.json` her zamanki gibi hariç.)

### Electron confirmation UI (`52d0b72`) — ne yapıldı, ne YAPILMADI

- Ortak SSE reader (`electron/src/renderer/src/lib/chatStream.js`): `/chat/stream`,
  `/chat/upload`, `/chat/confirm/{id}` için TEK parser — structured `confirmation_required` ve
  `{"async":true}` frame'leri artık transcript'e ham JSON olarak akmıyor.
- `ConfirmationOverlay` (App.jsx): her bekleyen çağrı `policy_guard.describe_call()` açıklamasıyla
  listeleniyor; APPROVE / DENY + opsiyonel `deny:<gerekçe>`; Escape = deny (dismiss yok,
  fail-closed). Karar `/chat/confirm/{id}`'ye gidiyor, devam aynı reader'dan akıyor; aynı-turn
  İKİNCİ interrupt prompt'u yerinde değiştiriyor.
- `useJarvisSocket` artık Phase 3'ten beri yayında olan `{type:"confirmation_required"}` WS
  broadcast'ini de dinliyor — BAŞKA transportlarda (ses dahil) başlayan onaylar da HUD'da
  görünüyor; çift-resolve sunucu tarafında güvenli (pending tek pop, kaybeden temiz hata).
- HUD artık kalıcı `conversation_id` gönderiyor (localStorage) — Faz 5'in "her server restart
  HUD konuşmasını yetim bırakır" bilinçli regresyonu kapandı.
- **YAPILMADI:** canlı HUD round-trip'i (gerçek server + model + gerçek tıklama) — `npm run
  build` + 7 kontrollü simüle-stream parser testi var, canlı E2E yok. Mobil (Flutter) tarafı
  hâlâ hiçbir şey çizmiyor.

### Ne inşa edildi (detay: CHANGELOG.md'nin Faz 8 girdisi)

1. **Registry sweep** (`tests/test_registry_sweep.py`, 157 test): tüm ToolSpec alan
   vokabülerleri; `L3+ ⇒ requires_confirmation` invariant'ı; dynamic-MCP fail-closed
   default'ları; ve daha önce HİÇ denetlenmeyen TOOL_SPECS ↔ `make_tools()` eşleşmesi
   (spec'siz @tool VEYA tool'suz spec artık gürültüyle düşer; tek meşru yokluk alpha-disabled
   `python_run`).
2. **Per-capability contract testleri** (`tests/test_capability_contracts.py`, 38 test): 12
   şemalı tool'un her biri için gate üzerinden valid ⇒ imzalı ExecutionRequest / ihlal ⇒
   `invalid_args_calls` + istek YOK (gövde yapısal olarak koşamaz) / bilinmeyen alan reddi /
   karışık batch izolasyonu; 13. şemalı tool eklenirse completeness guard'ı patlar.
3. **Property-fuzz** (`tests/test_property_fuzz.py`, hypothesis 6.160.0): `validate_args` hiç
   raise etmez + raw input'u hiç echo etmez; extra=forbid ve Literal-dışı action evrensel;
   redaction'dan tanınabilir secret sızmaz; `digest_args` deterministik. Profil
   `derandomize=True, deadline=None` — `dd339b9`'un kapattığı CI-flake sınıfını hypothesis
   üzerinden geri açmamak İÇİN.
4. **13 sınıflı hata taksonomisi** (`jarvis/execution/taxonomy.py`, saf stdlib, tek kaynak):
   `eval_oracle.score()` artık her Verdict'e `error_classes` ekliyor (mapping gerçek score()
   çıktısına karşı `tests/test_taxonomy.py`'de pinli; "expected X to succeed" formatı
   missing_tool_call/wrong_tool/execution_failure olarak ÜÇE ayrışıyor; B6 şekli =
   execution_failure + false_success_claim); driver kaydediyor; `ab_analyze.py` rapora
   invariant-etiketli sınıf kırılımı bölümü ekledi (eski kayıtlar aynı fonksiyonla yeniden
   türetiliyor — geriye uyumlu).
5. **Alpha gate enstrümanı** (`scripts/alpha_gate.py`; CI ruff satırında birinci-sınıf):
   `evaluate` kayıtlı ab_run sonuçlarını plan tablosuna map'leyip
   `results/alpha_gate_report.md` yazar (VERİ YOK satırları bilinçli boşluk); `isolation`
   ≥20 ardışık tek-tool sızıntı döngüsünü canlı koşar (fresh session + benzersiz tracer;
   sızıntıda non-zero exit). Saf mantık `tests/test_alpha_gate.py`'de (14 test).

### Canlı smoke'lar (2026-07-23, bu oturumda koşuldu)

- `ab_analyze.py C:\Temp\jarvis-ab champ --runs 5` → yeni taksonomi bölümü GERÇEK şampiyon
  kaydında: `missing_tool_call 1, execution_failure 2, false_success_claim 2 (invariant!),
  wrong_artifact 1` — 14. oturumda canlı gözlemlenen "tool çağırmadan başarı uydurma"
  probleminin ilk sayısal görünümü.
- `alpha_gate.py evaluate ... champ --runs 5` → **KALDI** (5 run < 10 hedef; B6 3/5;
  false_success_claim ≠ 0) + `exit=0` doğrulandı. Gate pohpohlamıyor — tasarım bu.

```powershell
python -m pytest -q       # 1246 passed, 0 failed (3:51) — FULL suite, 2026-07-23
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py scripts/alpha_gate.py   # All checks passed! (CI satırı birebir)
git log --oneline -3      # (en üstte bu dosyayı yazan kapanış commit'i) 7d6a7af (feat: Faz 8), e694819 — 16. oturum ucu
```

### Alpha kapısının YEŞİLE gitmesi için kalanlar (owner-koşusu ölçüm + 2 kapsama boşluğu)

1. `ab_run_config.ps1 -Config champ -Effort none -Runs 10` (10/10 satırları için) →
   `alpha_gate.py evaluate <root> champ --runs 10`.
2. Server ayaktayken `alpha_gate.py isolation` (≥20 run sızıntı denetimi).
3. İki dürüst VERİ YOK satırı için senaryo eklenmeli: uzun-workflow E2E (workflow_start'ı
   süren skorlu driver senaryosu yok) ve block/veto-dışı hata-recovery sınıfları.
4. Bilinen içerik engeli: false_success_claim invariant'ı şampiyonda ŞU AN 2 — model
   tool-calling güvenilirliği işi (aşağıda, taşınan #2) çözülmeden gate yeşil OLAMAZ.

## SONRAKİ OTURUM — kalan iş

1. **CANLI HUD E2E'si (Electron confirmation UI'ın kabulü):** server'ı başlat
   (`python -m jarvis --api`), Electron'u aç, L3 bir aksiyon iste (örn. "test@... adresine
   mail gönder"), overlay'de APPROVE/DENY'ı gerçekten tıkla; ikinci-interrupt ve
   WS-kaynaklı (sesle başlayan) onay senaryosunu da dene. Bu yapılmadan `52d0b72`
   "çalışıyor" SAYILMAZ — bkz. docs/SAFETY.md'nin güncellenmiş known-limit'i.
2. **Model tool-calling güvenilirliği** (14. oturumdan; artık taksonomiyle ÖLÇÜLEBİLİR):
   false_success_claim'i 0'a indirme işi — muhtemelen sistem promptu / tool-seçim talimatları;
   Faz 4'ün enforce modu (hâlâ default off) yapısal çözüm adayı, önce shadow ölçümü planın
   kendi sıralaması. Alpha gate bu iş bitmeden yeşil olamaz.
3. Alpha gate'in owner-koşusu ölçümleri (yukarıdaki blok) + 2 kapsama boşluğu senaryosu.
4. Branch ucunun CI'ı: `gh run list --branch langgraph-migration -L 3` ile kontrol et.
5. Eski kalanlar (değişmedi): `workflow_start` JSON `steps` güvenilirliği; gerçek CLI REPL
   E2E; Faz 6 Kısım 3 Literal-terfi; Faz 5 kalanları; canlı A/B B6 sorusu; 4 worktree
   branch; mobile flutter-analyze info/warning; chromadb yerel flakiness izlemesi (17.
   oturumun full-suite koşusunda da GÖRÜLMEDİ — 1246/1246).

## Değişmeyen taşınan işler

- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Pre-first-turn kozmetik model label — değişmedi.
