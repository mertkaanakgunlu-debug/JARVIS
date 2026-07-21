# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-21 (7. oturum) — ÖLÇÜM DÜZENEĞİ TAMİR EDİLDİ + FAZ 1 KABULÜ KAPANDI

**Durum tek cümlede:** Agent Runtime rev.2'nin **Faz 0 ve Faz 1'i bitti, commit'li ve push'lu**;
sıradaki iş **Faz 2**. Çalışan ağaç temiz (yalnız `.claude/settings.local.json`'da lokal izin
eklemeleri duruyor). `559 pytest yeşil` (bu oturumda yeniden koşularak doğrulandı), ruff temiz.
`langgraph-migration` == `origin/langgraph-migration`; `main` 47 commit geride (kasıtlı).

## Bu oturumda yapılanlar

Oturum Faz 2'ye geçmek üzere başladı, ama Faz 1'in kabul koşusu **ölçüm düzeneğinin kendisinin
bozuk olduğunu** ortaya çıkardı. Oturumun tamamı bunu kapatmakla geçti — ve buna değdi:
önceki iki oturumun "açıklanamaz" bulguları bu hatanın eseriymiş.

1. **`1a22d7f`** — 6. oturumun bekleyen Faz 1 kodu commit + push edildi (owner onayıyla).
2. **`5941f63` — kök hata:** `ab_run_config.ps1 -Port` parametresi driver'a hiç ulaşmıyormuş
   (`JARVIS_TEST_BASE_URL` set edilmiyordu, driver varsayılan 8132'ye gidiyordu). Varsayılan port
   dışındaki **her koşu sessizce 0 alıyordu**: dolu görünen bir sonuç dosyası yazıp `exit 0`
   veriyor, her satır "trace tools=none" diyordu. Ayırt edilemez bir toplam kayıp.
   **Bu, 6. oturumun "izole koşular Gemini 2.5 Pro diye cevaplıyor" anomalisini de çözdü:** smoke
   koşusu 8133'te sunucu başlatırken driver hâlâ açık olan **şampiyon baseline sunucusuna** (8132)
   bağlanmış. Kanıt: smoke'un B4 cevabı yalnız `home-champ` içinde var olan `jarvis_test.txt`'i
   listeliyor, ve `home-shadow-smoke`'ta hiç `audit_log.jsonl` yok. → **Faz 1 kodu suçsuzdu.**
   Yan etki: o smoke istekleri şampiyon baseline'ın 5. run'ının içine düştü — **62/65 referansı
   küçük bir kontaminasyon taşıyor.**
3. **`5342e86`** — hatanın *sınıfını* kapatan korumalar: driver'da `preflight()` (skorlamadan önce
   `GET /status`, başarısızsa exit 3), hiçbir senaryo sunucuya ulaşmadıysa exit 4 + "bu sonuçlar
   ölçüm DEĞİL, atın" mesajı, kısmi transport kaybında gürültülü uyarı, ve her koşuya
   `results/manifest_<config>.json` (run_id, git_sha, branch, dirty, port, mode, model, effort...).
   `HTTPError` kasten transport hatası sayılmıyor — sunucu cevap verdi, yani koşu hâlâ bir ölçüm.
4. **`513eadd`** — dış reviewer'ın iki pre-push kontrolü. İkincisi gerçek bir delikti: `.ps1`
   wrapper `exit=$LASTEXITCODE`'u log'a yazıp **yine de 0 dönüyordu**, yani driver'ın "bu bir ölçüm
   değil" kararını bir üst katmanda geçersiz kılıyordu. Artık geçersiz run varsa wrapper da
   non-zero dönüyor, manifest `valid_measurement` ile finalize ediliyor.
   Taksonomi netleşti: **semantik/tool hatası → geçerli ölçüm, pass/fail; transport/server hatası →
   ölçüm değil, run geçersiz.** Ulaşılamayan bir tur *başarısız* değil, **yok**.
5. **`aade56e`** — instance handshake. Preflight "bir sunucu cevap veriyor"u kanıtlıyordu,
   "**bu** sunucu" olduğunu değil. `GET /internal/test-identity` (yalnız `JARVIS_TEST_MODE=1` ile
   mount) run_id nonce'unu, mode'u, config fingerprint'ini ve git_sha'yı döndürüyor. Path
   döndürmüyor (kasten — teşhis yüzeyi keşif yüzeyine dönüşmesin). Uyuşmazlıkta driver exit 3.
   Faydalı yan etki: shadow modun açık olduğu artık checkpoint DB'sini taramadan doğrulanabiliyor.
6. **`6796043` + `9dd1405` — Faz 1'in asıl kabul testi.** Canlı A/B bu soruyu **cevaplayamaz**:
   n=5'te senaryo-bazlı varyans, aranan etkiden büyük (`champ 62/65`, `champ-shadow 59/65`,
   `ctl-off 59/65` — iki Faz-1 config'i berabere ama **farklı** senaryoları kaybederek).
   Yerine **deterministik replay**: gerçek derlenmiş graph, **scripted model** (varyans inşaen
   sıfır), aynı fixture off ve shadow'dan geçiyor, dışarıdan gözlenebilir her çıktı eşit olmalı.
   9 fixture (tool'suz cevap, başarılı çağrı, B6'nın canlı gözlenen yanlış argümanı, tool [ERROR],
   SSRF [BLOCKED], Faz 0 veto, iki-round retry, redaksiyon, ardışık iki çağrı).
   Karşılaştırılan: tool seçimi, ham argümanlar, sonuçlar, kullanıcı cevabı, **dosya sistemi yan
   etkileri (içerik hash'i)**, hata metni, sayaçlar, ledger. Farklı olmasına izin verilen: yalnız
   `execution_envelopes`. Volatil alanlar açık allowlist ile çıkarılıyor (blanket ignore değil).
   **Ayırt etme gücü mutasyonla doğrulandı**, varsayılmadı: `tool_result_accounting`'e kasten
   shadow-only bir yan etki enjekte edilince 9 vakadan 7'si kırmızıya döndü (yeşil kalan 2'si hiç
   tool çalışmayan vakalar — mutasyonun ateşleyemeyeceği yerler). Mutasyon geri alındı.
   `9dd1405` bunu **gerçek SqliteSaver** ile tekrarladı — shadow state'i checkpoint'e yazıyor,
   `checkpointer=None` ile koşmak tam da test edilmesi gereken mekanizmayı atlıyordu.
   **Sonuç: off ve shadow 9 fixture'da dışarıdan birebir aynı.** Faz 1 kabulü kapandı.

## SONRAKİ OTURUM — kalan iş

1. **`gh` CLI yetkilendirmesi — owner aksiyonu bekliyor.** Claude Code'un PR-durumu özelliği
   "GitHub CLI authentication expired" diyor; gerçek durum **hiç login olunmamış**:
   `gh auth status` → "not logged into any GitHub hosts", `%LOCALAPPDATA%\GitHub CLI\` içinde
   yalnız `device-id` var, **`hosts.yml` yok**, `GH_TOKEN`/`GITHUB_TOKEN` boş.
   `git push` çalışıyor olması çelişki değil: git ayrı depo kullanıyor
   (`credential.helper=manager`, Windows Credential Manager); `gh` onu okumaz.
   Çözüm (owner kendi terminalinde, interaktif tarayıcı akışı — Claude süremez):
   ```powershell
   gh auth login --hostname github.com --git-protocol https --web
   ```
   `mertkaanakgunlu-debug` hesabıyla. Sonra `gh auth status` ile doğrula.
2. **Faz 2 — `prepare_execution` node.** Plan: `C:\Users\mertk\.claude\plans\
   c-users-mertk-desktop-gpt-analysis-md-s-delegated-scone.md` §Faz 2 (satır 212).
   Kapsam: yeni node (`agent → prepare_execution → confirmation` routing değişikliği,
   `nodes.py:457`), capability resolve → normalize → schema validate → canonical path resolve →
   risk classify → TaskContract match → idempotency journal lookup → değişmez `ExecutionRequest`.
   Approval artık **HMAC ile bağlanıyor** (process-local key): `execution_id · capability ·
   normalized_args_digest · target_resource · risk_level · expiry · single_use_nonce`.
   Argüman değişirse (repair dahil) eski onay geçersiz; execute anında digest yeniden doğrulanıyor
   (TOCTOU kapanır); idempotency journal duplicate yan etkiyi reddediyor.
   **Kabul:** onaylanan ile çalışan argüman digest'i her zaman aynı; repair sonrası yeniden onay;
   aynı yan etki iki kez uygulanamıyor (test: approve → retry → journal reddi).
3. **Canlı A/B'nin B6 sorusu hâlâ açık — ama artık doğru yere işaret ediyor.** Deterministik test
   runtime'ı şüpheli listesinden çıkardı; geriye **model nondeterminizmi** kaldı. Bunu ölçmek için
   interleaved (config'leri sırayla değil, dönüşümlü koşan) bir deney gerekir ve n=5 yetmez.
   Owner'a sorulacak: bu deneye şimdi mi girilsin, yoksa Faz 2-4 zaten bu sınıfı hedeflediği için
   sonraya mı bırakılsın? (Öneri: sonraya — 4-6 saatlik koşu, ve Faz 4 tam bu iddiayı yapısal
   olarak imkânsız kılmayı hedefliyor.)
4. **Şampiyon 62/65 referansı kontamine** (bkz. yukarıda #2). Faz 2+ için temiz bir baseline
   gerekirse yeniden koşulmalı — artık handshake + manifest sayesinde tekrar aynı hataya düşmez.
5. **Dış reviewer paketi** — `docs/review/2026-07-premerge-summary.md`, owner süreci, bu oturumda
   dokunulmadı.
6. **Bilinçli ertelenenler (değişmedi):**
   - W4b (veto-turn'de critic LLM atlaması) — graph routing değişikliği ayrı oturum.
   - `[BLOCKED]` sunum katmanından kod soyma.
   - qwen3.5/ministral-3'ün thinking-on kolu (owner kararıyla OFF-only kapsam).
   - `stoic-spence` worktree'sindeki rolling summarization (G17b'nin davranışsal yarısı).
   - `docs/ARCHITECTURE.md` orchestrator bölümü birkaç faz geride — task_f540cef1.

## Ortam / komutlar
```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q                      # 559 test, offline, ~3 dk (pytest-timeout KURULU DEĞİL)
ruff check jarvis/ tests/ scripts/eval_oracle.py scripts/manual_test_driver.py scripts/ab_analyze.py scripts/ab_launch_server.py
```
Env: `LOCAL_MODEL=qwen3:8b`, `LOCAL_REASONING_EFFORT=none` — **CONFIRMED** (provisional değil).
`EXECUTION_CONTRACT_MODE` default `off`.
Koşum artifact'leri: `C:\Temp\jarvis-ab\`. **`ab_analyze.py`'yi PowerShell'den çalıştırın, Git
Bash'ten değil** — Bash, Windows path'lerindeki ters eğik çizgileri yutup sahte bir config kolonu
üretiyor (2026-07-20'de canlı bulundu).

## Değişmeyen taşınan işler
- 8 direct-Gemini modülün shared gateway'e migrasyonu (Sprint 3) — kapsam dışı.
- 4 worktree branch read-through — ayrı go-ahead bekliyor (CLAUDE.md'de liste).
- Electron/mobil confirmation render'ı — hâlâ yalnız CLI text+voice.
- Pre-first-turn kozmetik model label — **artık ayrı bir açık değil sayılabilir**: 6. oturumun bu
  başlık altında raporladığı gözlem `5941f63`'te yanlış-sunucu hatası olarak açıklandı. Gerçek bir
  kozmetik-label sorunu kaldıysa yeniden gözlemlenmesi gerekiyor.
