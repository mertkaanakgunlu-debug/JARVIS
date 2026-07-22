# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-22 (9. oturum, devamı) — FAZ 6 KISIM 2 KODLANDI VE TEST EDİLDİ (COMMIT'LENMEDİ); FAZ 4/5/6-K1 COMMIT'LENDİ VE PUSH'LANDI

**Durum tek cümlede:** Bu oturumda Faz 4, Faz 5, Faz 6 Kısım 1 owner'ın ayrı ayrı "commit et"
isteğiyle dört commit'te (`9c0ca15`/`5eae027`/`cb2b1a2`/`8613bfc`) landed ve push'landı (owner
kendisi push'ladı); ardından dış bir review Faz 6 Kısım 1'i, sonra Faz 6 Kısım 2'nin İLK halini
sorguladı — ikisini de değerlendirdim (bazı noktaları kabul edip farklı/daha güvenli şekilde
çözdüm, bir P0 iddiasını AMPİRİK KANITLA çürüttüm, ayrıntı aşağıda) — ve **Faz 6 Kısım 2
tamamlandı + test edildi, henüz commit'lenmedi** — owner'ın kararını bekliyor. **853 pytest
yeşil, ruff temiz, `git diff --check` temiz** (bu sayı TÜM dosya değişiklikleri, dokümantasyon
dahil, TAMAMLANDIKTAN SONRA çalıştırıldı — önceki turun "rapor dokümantasyon güncellemesinden
önceydi" eleştirisi haklıydı, bu kez değil).

## Faz 6 Kısım 2 — dış review + tasarım + implementasyon

**Review'in 4 ana noktası ve benim değerlendirmem:**

1. **"Tek şema kaynağı kullan (`@tool(args_schema=...)`)"** — KISMEN katıldım, FARKLI çözdüm.
   `@tool` fonksiyon imzalarına (`jarvis/graph/tools.py`) DOKUNMADIM — LangChain `args_schema`'yı
   modelin GERÇEKTEN gördüğü şema olarak kullanıyor, yani bunu bağlamak modelin canlı davranışını
   DEĞİŞTİRİR (Kısım 1'in kendi bulguları — geo_math'ın gizli action'ları, spotify'ın
   dokümante edilmemiş alias'ları — yalnız TAM dispatch kodu okunarak yakalandı, docstring
   yetmiyordu). Bunun yerine `jarvis.execution.args_schemas` TEK dahili doğrulama kaynağı oldu —
   `prepare_execution_node` ondan okuyor, ikinci bir Literal seti YAZILMADI.
2. **"Normalize → validate → sign → execute sırası, canonical args her yerde aynı olmalı"** —
   KATILDIM ama DAHA BASİT çözdüm. Canonical args'ı imzalama/fingerprint/execution'a
   PROPAGATE etmek yerine (mesaj listesi cerrahisi gerektirirdi, güvenlik-kritik bir yolda),
   **validation SADECE bir GEÇİT** — hiçbir zaman ham args'ı değiştirmiyor. Normalizer'lar
   (`.strip().lower()`) yalnız kabul/red KARARINI doğru vermek için var — imzalanan/çalıştırılan
   args HER ZAMAN ham. Bu güvenli çünkü doğruladığım HER dispatch fonksiyonu (calendar/gmail/
   drive/itu_mail/finance/spotify/schedule/todo/gcp_quota/geo_math — TAMAMI okundu) zaten KENDİ
   `.strip().lower()`'ını yapıyor — digest/execution ayrışma riski TASARIMLA kapatıldı, dikkatli
   propagasyonla değil.
3. **"Action Literal tek başına yetmez, action-özel zorunlu alan kontrolü gerekiyor"** —
   KATILDIM, GERÇEK KODDAN doğrulayarak (docstring'den değil — review'in KENDİ önerdiği bazı
   gereksinimler yanlış çıktı: `spotify`'ın `play`'i query'siz de GEÇERLİ, `gcp_quota`/
   `hud_panels`'ın hiç zorunlu alanı yok, `geo_math`'ın yalnız analyze/reason/derive/explain
   ailesi bir gereksinim taşıyor — 9 dispatch dosyasının TAMAMI satır satır okunarak doğrulandı).
4. **"Structured validation_errors + açık bounded-repair sayacı"** — TAMAMEN katıldım.
   `validate_args()` pydantic'in kendi `ValidationError.errors()`'ını `loc`/`type`/`msg`'e
   kırpıyor (`input`/`url`/`ctx` DÜŞÜRÜLDÜ — `input` ham argüman değerini yankılıyor, bu
   codebase'in redaksiyon disiplini bunun redakte edilmeden kalıcı olmasını yasaklıyor). Yeni
   `JarvisState["args_repair_attempted"]: bool` (turn başına sıfırlanıyor) — `max_tool_rounds_
   per_turn`'e GÜVENMİYOR (review'in doğru yakaladığı gerçek risk: reddedilen bir batch de
   tool_rounds'u tüketiyor, "tek repair" implicit garantisi config değişince sessizce bozulurdu).

**Yapılanlar:**
1. `jarvis/execution/args_schemas.py`: 12 şemanın hepsine `field_validator(mode="before")`
   normalizer + (8 tanesine) `model_validator(mode="after")` action-özel zorunlu alan kontrolü +
   yeni `validate_args(schema_cls, args) -> (ok, errors)` fonksiyonu eklendi.
2. `prepare_execution_node` (`jarvis/graph/nodes.py`): her tool call için `spec.args_schema`
   varsa `validate_args()` çalıştırıyor — geçersizse ExecutionRequest MINT ETMİYOR, yeni
   `invalid_args_calls` state alanına kaydediyor (ham args DEĞİŞMEDEN).
3. `confirmation_node`: yeni bir pre-gate (duplicate-fingerprint kontrolünden hemen sonra) —
   `invalid_args_calls` varsa TÜM batch reddediliyor (`[INVALID_ARGS:field]` + `[SKIPPED]` stub'ları,
   mevcut whole-batch-reject deseniyle aynı), `args_repair_attempted=True` set ediliyor, "agent"a
   dönüyor. Bu turda İKİNCİ bir geçersiz batch gelirse — dönmüyor, doğrudan dürüst bir final
   cevap composing edip yeni `confirmation_result="invalid_args_exhausted"` ile END'e gidiyor.
4. `route_from_confirmation` + `graph.py`: yeni üçüncü dal (`confirmation → END`).
5. **Mevcut testler düzeltildi** — `test_prepare_execution_node.py`'nin paylaşılan gmail fixture'ı
   (`{"action": "send", "to": "a@b.c"}`, subject/body eksik) artık YENİ validation'ı gerçekten
   ihlal ediyordu (9 test kırıldı) — subject/body eklenerek düzeltildi (bu, doğrulamanın
   ÇALIŞTIĞININ kanıtı, bir regresyon değil).
6. **54 yeni test (792→846)** — `test_args_schemas.py` genişletildi (normalizer + action-özel
   required-field testleri, `validate_args()` testleri — dosya 40'tan 84'e çıktı, +44), yeni
   `tests/test_bounded_repair.py` (10 test — 3 katman: prepare_execution_node izole, confirmation_
   node zincirlenmiş, GERÇEK derlenmiş graf'ta scripted bir modelin AYNI geçersiz gmail çağrısını
   iki kez yapıp turun onurlu bir cevapla END'e ulaştığını kanıtlayan uçtan uca test).

**Dürüst kalan boşluk (değişmedi):** `@tool` fonksiyon imzaları hâlâ `action: str` — model hâlâ
serbest metin görüyor. Bu şemalar yalnız `prepare_execution_node`'un dahili kapısında aktif.

## Faz 6 Kısım 2 — ikinci review turu: bir P0 iddiası ampirik olarak çürütüldü

Owner review'i başka bir GPT'ye gönderdi; o da GERÇEK bir P0 doğruluk sorunu iddia etti: pydantic
(`_StrictArgs` yalnız `extra="forbid"`, `strict=True` DEĞİL) `"60"` → `60`, `"false"` → `False`
gibi coercion yapıyor, ama `validate_args()` bu canonical sonucu ATIYOR ve ham args tool'a
gidiyor — iddiaya göre `ItuMailArgs(reply_all="false")` validation'dan geçer ama tool ham
`"false"` string'ini görür, Python'da bu truthy olduğu için `if reply_all:` yanlışlıkla True
çalışır.

**Bu iddiayı AMPİRİK OLARAK test ettim (JARVIS'in GERÇEK, registry'deki tool objelerine karşı,
varsayımla değil) ve İDDİA BU CODEBASE İÇİN YANLIŞ ÇIKTI:**

```python
from jarvis.graph import tools as graph_tools
calendar = by_name["google_calendar"]
itu_mail = by_name["itu_mail"]
itu_mail.args_schema(action="reply", uid="1", body="hi", reply_all="false").reply_all
# → False (bool), STRING DEĞİL
calendar.args_schema(action="create", ..., duration_minutes="60").duration_minutes
# → 60 (int), STRING DEĞİL
```

Sebep: `jarvis/graph/tools.py`'deki HER `@tool` fonksiyonu için LangChain KENDİ pydantic şemasını
fonksiyonun type hint'lerinden OTOMATİK üretiyor (`tool.args_schema` — benim
`jarvis.execution.args_schemas`'ımdan TAMAMEN BAĞIMSIZ, ve Faz 1'den beri, `@tool` kullanan HER
fonksiyon için var). `ToolNode`'un GERÇEK dispatch'i (`jarvis/graph/safe_tools.py`'nin
`make_safe_tool_node`'u — `ToolNode`'u SARIYOR, yerine geçmiyor, doğrulandı) bu şemadan GEÇEREK
tool fonksiyonunu çağırıyor — yani `"false"` string'i asla fonksiyon gövdesine ulaşmıyor, ondan
ÖNCE LangChain'in kendi (Faz 6'dan tamamen bağımsız, ondan çok önce var olan) coercion katmanında
`False`'a dönüşüyor. Benim `jarvis.execution.args_schemas` katmanım bu coercion'ı YAPMIYOR —
ihtiyacı yok, çünkü execution zaten LangChain'in kendi coercion'ından geçiyor. Benim katmanımın
GERÇEK, ÇAKIŞMAYAN katkısı: action-Literal enum kısıtı (LangChain'in auto-şeması `action: str`'ı
serbest bırakıyor), action-özel zorunlu alan kontrolü (her parametrenin default'u var, LangChain
her şeyi opsiyonel sanıyor), ve unknown-field reddi (**ampirik olarak doğrulandı: LangChain'in
auto-şeması bilinmeyen bir field'ı SESSİZCE KABUL EDİYOR** — `_StrictArgs`'ın `extra="forbid"`'ı
GERÇEK, tekrarsız bir güvenlik katkısı, süsleme değil).

Bunu kanıtlayan 5 yeni test: `tests/test_langchain_dispatch_coercion.py` (gerçek `google_calendar`/
`itu_mail` tool objelerine karşı, coercion + reddedilen-değer + bilinmeyen-field davranışını +
`make_safe_tool_node`'un gerçekten `ToolNode`'u sardığını doğruluyor).

**Review'in TEK gerçek ama Faz 6'dan BAĞIMSIZ, ÖNCEDEN VAR OLAN bulgusu:** `tool_call_fingerprint`
ham args üzerinden hash'liyor, yani `"send"` ile `" SEND "` farklı fingerprint üretir — semantic
duplicate koruması bunu yakalamaz. Bu GERÇEK ama (a) Faz 1B'nin fingerprint tasarımının bir
özelliği, Faz 6'nın YENİ bir regresyonu değil, (b) düşük ciddiyette (bir modelin AYNI mantıksal
çağrıyı rastgele büyük/küçük harf farkıyla retry etmesi alışılmadık). Bu oturumda DÜZELTİLMEDİ —
ayrı, kapsam dışı bir iyileştirme olarak not edildi.

**İkinci gündem: 841 vs 846/853 test sayısı "tutarsızlığı" — YENİ bir hata değil.** "841" bu
oturumun DAHA ÖNCEKİ bir aşamasında (Faz 6 Kısım 2 başlamadan önce) zaten yakalanıp `b7d03c6`
commit'iyle 792'ye düzeltilmişti — ama o commit hiç PUSH edilmemişti, dış review'in GitHub okuması
hâlâ eski (yanlış) sayıyı içeren `8613bfc`'yi görüyordu. Yeni bir tutarsızlık değil, push
edilmemiş bir düzeltme. `--collect-only` ile istenen kesin dosya bazlı sayım:
```
tests/test_args_schemas.py               -> 84 tests
tests/test_bounded_repair.py              -> 12 tests
tests/test_tool_registry_schemas.py       -> 5 tests
tests/test_blocked_reason_code.py         -> 12 tests
tests/test_langchain_dispatch_coercion.py -> 5 tests   (yeni)
Toplam bu 5 dosya: 118 test
```

**Ayrıca eklenen 2 test** (review'in listesindeki, gerçekten eksik olan iki nokta): turn başına
`args_repair_attempted`'ın gerçekten `False`'a sıfırlandığını `chat()` seviyesinde kanıtlayan test,
ve `invalid_args_exhausted` yolunun `chat()`'in döndürdüğü GERÇEK, boş olmayan cevaba ulaştığını
kanıtlayan test — ikisi de `tests/test_bounded_repair.py`'ye eklendi (10→12).

## Ortam / komutlar — TÜM dosya değişiklikleri (dokümantasyon dahil) TAMAMLANDIKTAN SONRAKİ gerçek çıktı
```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q                      # 853 passed, 288 warnings in 200.51s
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py
                                          # All checks passed!
git diff --check                         # (temiz, exit 0)
git status --short
#  M jarvis/agent.py
#  M jarvis/execution/args_schemas.py
#  M jarvis/graph/graph.py
#  M jarvis/graph/nodes.py
#  M jarvis/graph/state.py
#  M tests/test_args_schemas.py
#  M tests/test_prepare_execution_node.py
# ?? tests/test_bounded_repair.py
# ?? tests/test_langchain_dispatch_coercion.py
#  M HANDOFF.md / ROADMAP.md               (bu dokümantasyon güncellemesinin kendisi)
#  M .claude/settings.local.json           (ilgisiz, bu oturumdan önce de duruyordu)
```
HEAD şu an `b7d03c6` (Faz 6 Kısım 2 henüz commit'lenmedi, yukarıdaki diff working tree'de).

## SONRAKİ OTURUM — kalan iş

1. **Owner'ın Faz 6 Kısım 2 commit kararı bekliyor.**
2. **Faz 6, Kısım 3 (hâlâ yapılmayanlar, bilinçli):**
   - `@tool` fonksiyon imzalarını doğrulanmış Literal'lere terfi ettirmek (modelin GÖRDÜĞÜ şemayı
     sıkılaştırmak) — Kısım 2 sonrası hâlâ ertelendi, şimdi dahili validation canlı trafikte bir
     süre gözlemlenip (audit_log'daki `blocked_invalid_args` oranı) ölçüldükten sonra gündeme
     gelmeli.
   - "Alternatif capability" (planın "tek repair → alternatif capability → açık hata" ladder'ının
     ortadaki basamağı) hiç yapılmadı — yalnız "tek repair → açık hata" var.
   - `tool_call_fingerprint`'in ham args üzerinden hash'lemesi (Faz 1B, Faz 6'dan bağımsız,
     ÖNCEDEN VAR OLAN bir tasarım) `"send"` ile `" SEND "`'i farklı fingerprint yapıyor —
     semantic duplicate koruması case/whitespace farkını yakalamıyor. İkinci review turunda
     bulundu, gerçek ama düşük ciddiyette (bir modelin aynı çağrıyı rastgele biçim farkıyla retry
     etmesi alışılmadık); bu oturumda düzeltilmedi, ayrı bir iyileştirme olarak not edildi.
3. CI kırmızı (bu oturumun regresyonu değil — bkz. bir önceki oturum notu, `test_ab_harness_
   guards.py` + `flutter analyze`, ikisi de pre-existing).
4. Diğer Faz 5 kalan işleri (değişmedi): API'nin gerçek per-client conversation_id desteği yok;
   `run_manifest.json`'ın prompt hash/registry version alanları boş; `plot_data` dışındaki
   artifact tool'ları run-scoped değil.
5. Canlı A/B'nin B6 sorusu hâlâ açık (değişmedi). Şampiyon 62/65 referansı kontamine (değişmedi).
6. Bilinçli ertelenenler (değişmedi): W4b, `[BLOCKED]` sunum katmanı, qwen3.5/ministral-3
   thinking-on, `stoic-spence` rolling summarization, `docs/ARCHITECTURE.md` orchestrator bölümü.

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — değişmedi.
