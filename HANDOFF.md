# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-22 (9. oturum, devamı) — FAZ 5 COMMIT'LENDİ, FAZ 6 KISIM 1 (TYPED SCHEMAS — TANIM, HENÜZ BAĞLANMADI) KODLANDI VE TEST EDİLDİ

**Durum tek cümlede:** Faz 5 owner isteğiyle commit'lendi (`5eae027`, push'lanmadı), ardından
Faz 6'nın ("typed schemas + bounded repair") plan'ın kendi önceliklendirdiği kısmı — 12 tool için
gerçek `args_schema` tanımı + `[INVALID_ARGS:<field>]` parser — kodlandı ve test edildi (**841
pytest yeşil**, ruff temiz). **Bilinçli ve açık kapsam kararı: bu şemalar henüz HİÇBİR YERE
BAĞLANMADI** — ne modelin gördüğü canlı `@tool` şemalarına, ne `prepare_execution_node`'un
validation adımına. Sebebi aşağıda detaylı. **Henüz commit'lenmedi.**

## Bu oturumda yapılanlar

### Faz 5 commit'i
Owner "commit et sonra devam et" dedi. `CHANGELOG.md`'ye Faz 5 girdisi eklendi, 25 dosya
commit'lendi (`.claude/settings.local.json` hariç): `5eae027`.

### Faz 6, Kısım 1 — Typed schemas (plan §Faz 6'nın TANIM kısmı, "bounded repair" HENÜZ DEĞİL)

**Neden "tanımlandı ama bağlanmadı" — bu bilinçli bir kapsam kararı, gözden kaçırma değil.** Her
action-dispatch tool'un GERÇEK kabul ettiği action kümesini çıkarmak yalnızca docstring'i okumakla
yetmiyor — bu oturumda **iki gerçek, dokümante edilmemiş keşif** oldu:
- `geo_math` docstring'indeki "Actions:" listesinde OLMAYAN `analyze`/`reason`/`derive`/`explain`
  action'larını, kendi dispatch zincirinin EN BAŞINDA, ayrı bir branch'te kabul ediyor.
- `spotify_control` (jarvis/tools/spotify.py), docstring'in yalnız `previous` dediği yerde
  `prev`/`back` alias'larını da kabul ediyor.

Her ikisi de bu oturumda YAKALANDI ve doğru şekilde şemalara girdi (çünkü her tool'un TAM dispatch
zinciri okundu, yalnız docstring'i değil) — ama tam bu deneyim, canlı/model-facing şemalara
(LLM API'nin ne kabul edeceğini SESSİZCE değiştiren) dokunmama kararının gerekçesi: yalnız iç
validation için kullanılan bir şemadaki hata ucuza düzeltilir; canlı bir tool-calling şemasındaki
hata sessiz bir regresyon olur. `jarvis/graph/tools.py`'deki HİÇBİR `@tool` fonksiyon imzası bu
oturumda değiştirilmedi — hepsi hâlâ `action: str` (serbest string).

**Yapılanlar:**
1. **`jarvis/execution/args_schemas.py`** (yeni) — 12 pydantic `BaseModel` (`extra="forbid"` ortak
   temel sınıf `_StrictArgs` üzerinden — plan'ın ayrı "unknown-field rejection" maddesi bu ortak
   temelden ücretsiz geliyor): `PlotDataArgs` (plan'ın öncelik #1'i — `path` XOR `data_json`
   cross-field validator'ı, `kind: Literal[...]`), `SpotifyArgs`, `GoogleCalendarArgs`,
   `GmailArgs`, `HudPanelsArgs`, `ScheduleArgs`, `TodoArgs`, `GoogleDriveArgs`, `ItuMailArgs`,
   `FinanceArgs`, `GcpQuotaArgs`, `GeoMathArgs` — her biri kendi tam dispatch zincirinden çıkarılan
   `action: Literal[...]` taşıyor (alias'lar dahil).
2. **`tool_registry.py`'nin `TOOL_SPECS`'ine kablolandı** — her 12 tool'un ORİJİNAL `ToolSpec(...)`
   constructor çağrısına `args_schema=` eklendi. Doğrulandı: `TOOL_SPECS` üç ayrı `replace()`
   geçişinden geçiyor (domain, contract_status, timeout_class — hepsi Faz0/2A/3'ten) — `args_schema`
   HİÇBİRİNDEN etkilenmeden hayatta kalıyor (her `replace()` yalnız KENDİ alanına dokunuyor, testler
   bunu doğrudan kanıtlıyor).
3. **`[INVALID_ARGS:<field>]` reason code parser** — `tool_accounting.py`'nin `parse_blocked_code`
   ailesine `parse_invalid_args_field()` eklendi (aynı desen), `_FAILURE_PREFIXES`'e
   `"[INVALID_ARGS"` eklendi (ledger/audit'in "ok" yargısı için — henüz hiçbir üretici yok ama
   tuple'ı gelecekte senkronize bir ikinci değişiklik gerektirmeden hazır tutuyor).
4. **49 yeni test** — `tests/test_args_schemas.py` (40, saf pydantic model testleri — geo_math'ın
   gizli action'ları, spotify'ın alias'ları, her tool için bilinen action'ların kabulü + bilinmeyen
   action'ın reddi + bilinmeyen field'ın reddi), `tests/test_tool_registry_schemas.py` (5, üç
   `replace()` geçişinden sağ çıktığını kanıtlayan + şemasız tool'ların hâlâ `None` kaldığını
   kilitleyen), `tests/test_blocked_reason_code.py` +4. **841 pytest yeşil (792+49), ruff temiz.**

**Plan'ın "her capability" hedefine göre kapsam:** yalnız plan'ın kendi belirttiği ÖNCELİK
sırasındaki 12 tool (`plot_data` + 11 action-dispatch tool) şema aldı. Geri kalan ~20 basit
tool (file_read, file_write, web_search, csv_read, math_solve, ...) hâlâ `args_schema=None` —
Faz 1'in kendi sözü ("None = not yet typed (every tool today)") kademeli bir tiplendirmeyi zaten
öngörüyordu, tek seferde atomik bir geçiş değil.

## SONRAKİ OTURUM — kalan iş

1. **Owner'ın Faz 6 Kısım 1 commit kararı bekliyor.**
2. **Faz 6, Kısım 2 (asıl bağlama işi) — henüz YAPILMADI:**
   - Doğrulanmış `action: Literal[...]` kümelerini (artık dispatch zincirinden gerçek olarak
     doğrulanmış) `jarvis/graph/tools.py`'deki GERÇEK `@tool` fonksiyon imzalarına terfi ettirmek
     — bu, modelin gördüğü şemayı gerçekten sıkılaştırır (bugün yalnız iç `args_schema` bunu yapıyor,
     model hâlâ serbest string görüyor).
   - `prepare_execution_node`'un "normalize" adımına gerçek validation bağlamak (kendi docstring'i
     Faz1'den beri bunu "Faz 6 destination" olarak bekliyor).
   - **Bounded repair pipeline'ın kendisi HENÜZ TASARLANMADI**: plan "normalize → validate → tek
     repair → alternatif capability → açık hata" diyor ama "repair" 30+ farklı tool arg şekli için
     ne anlama gelir, plan metninde net değil. Bu oturumda düşünülen (ama KODLANMAMIŞ) bir yön:
     validation başarısız olursa `[INVALID_ARGS:<field>]` + açık rehberlik içeren bir stub mesajı +
     modelin YENİDEN denemesi (mevcut `max_tool_rounds_per_turn` bütçesiyle sınırlı) —
     `confirmation_node`'un mevcut `_reject_batch` deseniyle aynı şekil, YENİ bir "tek repair
     sayacı" icat etmeden. Owner'la bu yorumun doğru olup olmadığı teyit edilmeli.
   - `[INVALID_ARGS:<field>]`'ın gerçek bir üreticisi yok — yalnız parser hazır.
3. Diğer Faz 5 kalan işleri (değişmedi): API'nin gerçek per-client conversation_id desteği yok;
   `run_manifest.json`'ın prompt hash/registry version alanları boş; `plot_data` dışındaki artifact
   tool'ları run-scoped değil.
4. Canlı A/B'nin B6 sorusu hâlâ açık (değişmedi). `gh` CLI yetkilendirmesi — owner aksiyonu
   bekliyor (değişmedi). Şampiyon 62/65 referansı kontamine (değişmedi).
5. Bilinçli ertelenenler (değişmedi): W4b, `[BLOCKED]` sunum katmanı, qwen3.5/ministral-3
   thinking-on, `stoic-spence` rolling summarization, `docs/ARCHITECTURE.md` orchestrator bölümü.

## Ortam / komutlar
```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q                      # 841 test, offline, ~2.5-3 dk (pytest-timeout KURULU DEĞİL)
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py
```
Env: `LOCAL_MODEL=qwen3:8b`, `LOCAL_REASONING_EFFORT=none` — CONFIRMED. `EXECUTION_CONTRACT_MODE`
default `off`. Faz 6 Kısım 1'in yeni kodu (args_schemas.py, TOOL_SPECS'e kablolama,
parse_invalid_args_field) **hiçbir çalışma zamanı davranışını değiştirmiyor** — hiçbir yerden
çağrılmıyor/okunmuyor, yalnızca tanım + test. Production'da tamamen inert.
Koşum artifact'leri: `C:\Temp\jarvis-ab\`. **`ab_analyze.py`'yi PowerShell'den çalıştırın, Git
Bash'ten değil.**

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — değişmedi.
