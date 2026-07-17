# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-16 (2. oturum) — Manuel test round 2 (Patch 1.1 üzerinde)

**Context:** İlk canlı manuel test oturumunun (7 bug bulan) 16 maddelik test listesi, stabilizasyon
sprinti + Patch 1.1 uygulanmış working tree üzerinde AYNEN yeniden koşuldu — bu kez tamamen izole:
`python -m jarvis --api --profile test --port 8132`, sabit `JARVIS_TEST_HOME` (scratchpad altında),
`CLOUD_POLICY=off`, `EXTERNAL_WRITES_ENABLED=false`, Tavily anahtarı env'den geçirildi. Gerçek
`data/` ve repo'ya dokunulmadı; toplam maliyet **$0.00** (41 Ollama çağrısı, usage.json canlı doğrulandı).

## Sonuç özeti (detay: bu oturumun konuşma raporu)

**Başarı oranı: 16 testten 2 tam + 2 kısmi geçer (~%19).** Kök neden geçen seferle aynı ve
değişmedi: qwen2.5:7b-instruct, gerçek graph'ın ~30 araç + uzun system prompt yükü altında
tool-call KANALINI güvenilir kullanamıyor (JSON'u düz metne yazıyor, önceki cevabı yankılıyor,
Çince'ye kayıyor). Bu, sprint 2'deki tool-domain router'ın çözmesi beklenen problem — öncelik değişmedi.

**Ama stabilizasyon katmanının kendisi canlıda kanıtlandı:**
- Model etiketi/trace artık gerçeği söylüyor: her turn `actual_provider:"ollama"`, `fallback_used:false`.
- Maliyet gerçeği: 41 çağrı → `cost_usd: 0.0`, `flash/pro_turns: 0`, `by_provider` doğru.
- `degraded` listesi dürüst (`entity_extractor`, `fact_extractor` — CLOUD_POLICY=off gate'i).
- **External-writes gate canlıda ilk kez gerçek bir halüsinasyonu yakaladı**: A2 turn'ünde model
  ~20 tool-call'luk bir batch halüsinasyonu üretti (2× `itu_mail send/reply` dahil) — gate L3'leri
  `blocked_external_writes_disabled` ile kesti, batch'in tamamı stub'landı, sıfır yan etki, hepsi
  audit'te. (Not: nodes.py external-writes bloğu batch'teki TÜM çağrıları stub'lıyor — bu olayda
  bizi L2 halüsinasyonlarından da korudu.)
- SSRF guard'ı canlı doğrulandı (C9): gerçek `url_read http://localhost:8132/status` çağrısı
  `[ERROR] Refusing to fetch this URL` ile engellendi.
- `--profile test` izolasyonu + crash-resume çalıştı (süreç öldürülüp yeniden başlatılınca aynı
  session kaldığı yerden yüklendi).

## Bu oturumun bulduğu YENİ buglar (backlog'a)

1. **`imap_tools` bağımlılığı eksik + tool hatası HTTP 500 olarak sızıyor** (E15): "Son 3 mailimi
   listele" → model GERÇEK `itu_mail list_unread` çağrısı üretti (read-action, Patch 1.1 gate'inden
   doğru geçti) → tool import'u `No module named 'imap_tools'` ile patladı → `/chat` opak 500 döndü.
   İki ayrı iş: (a) `imap_tools` requirements.txt'te YOK — ya eklenmeli ya itu_mail graceful-degrade
   olmalı; (b) tool exception'ı ToolMessage hatası olarak modele dönmeli, turn'ü 500'le öldürmemeli
   (LangGraph ToolNode `handle_tool_errors` ayarı yok gibi).
2. **Başarılı tool sonucunu tanımayıp loop'a girme + recursion-limit'in opak 500'ü** (F16):
   `procedure_save` başarılı DRAFT sonucuna rağmen ~10 kez üst üste çağrıldı (id=2..11, hepsi
   persist oldu), recursion limit 30 turn'ü `GRAPH_RECURSION_LIMIT` 500'üyle kesti. Backstop çalıştı
   ama: duplicate draftlar kaldı, kullanıcıya opak 500 gitti. (Draft-onay tasarımının kendisi doğru
   çalıştı: hiçbiri onaysız aktif olmadı.)
3. **Minör/audit**: per-action downgrade'li çağrılarda decision kaydı action-level risk (<2 →
   atlanıyor) ile, execution kaydı spec-level risk (3) ile yazılıyor — audit'te "kararsız yürütme"
   gibi görünüyor (E15'te `itu_mail` execution_start risk_level:3 var, decision satırı yok).
4. **Model davranış deseni** (router sprintine veri): iki turn'de qwen bir ÖNCEKİ cevabını aynen
   yankıladı (C8, D10) — history injection'ın küçük modelde kendisi bir hata modu.

## Explicitly deferred / önceki oturumdan taşınan

- **Patch 1.1 hâlâ working tree'de, COMMIT EDİLMEDİ** (standing rule: açık istek olmadan commit
  yok). Dosya listesi önceki handoff'takiyle aynı; bu oturum repo'ya hiç dokunmadı (git status
  birebir aynı, test artefaktları scratchpad'de).
- Sprint 2 (tool-domain router + `_is_trivially_simple` rewrite + critic ayrımı) — bu oturumun
  sonuçları aciliyetini bir kez daha doğruladı. Sprint 3 (8 direct-Gemini modülün gateway
  migrasyonu) değişmedi.
- 4 worktree branch read-through (carried over).
- AI Studio tier onayı → `AI_STUDIO_BILLING_MODE` set etme (carried over).

## Önerilen sonraki adımlar

1. Patch 1.1'i commit/push et (önceki handoff'un önerdiği mesajla) — GPT reviewer re-verify edebilsin.
2. Yeni bug 1a/1b (imap_tools + tool-error→ToolMessage) — küçük, sprint 2'den bağımsız, hemen alınabilir.
3. Sprint 2'ye başla — bu oturumun ölçümleri (hangi turn'lerde kanal çöküyor, yankı deseni,
   loop deseni) router tasarımına doğrudan girdi; test çıktıları:
   `C:\Temp\claude\C--Users-mertk-Desktop-Jarvis\b3003573-96bf-4a9d-b68e-08e4c7f01708\scratchpad\`
   (`manual_test_driver.py` yeniden kullanılabilir, `results.jsonl` ham kayıt, `jarvis-test-home\`
   audit/usage kanıtları). Scratchpad session'a özel — kalıcı olması istenirse repo dışına kopyala.

## Environment checklist to resume work

```powershell
.\.venv\Scripts\Activate.ps1
pytest                                # 242 test (bu oturumda değişmedi, koşulmadı — kod da değişmedi)
ollama serve                          # qwen2.5:7b-instruct yüklü olmalı
python -m jarvis --api --profile test --port 8130   # izole smoke-test yolu
```
