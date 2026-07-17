# Manuel test çıktıları — kendi gözünle teyit için

Bu klasör, 2026-07-17 oturumunda koşulan 6 test turunun (2 baseline + 2 A/B + 1 doğrulama
+ 1 final kabul) **ham kanıtlarını** içerir: her sorgu/yanıt çifti, JARVIS'in gerçekten
çalıştırdığı araçların audit kaydı, ve varsa gerçekten diskte oluşan dosyalar.

Kaynak: `C:\Temp\claude\...\scratchpad\` altındaki oturuma-özel geçici klasör (silinebilir) —
buraya kalıcı, repo içi bir kopya olarak taşındı. **Henüz git'e commit edilmedi**, isterseniz
commit ederim.

## Klasör başına ne var

Her `NN_*` klasöründe:
- **`sorgu_ve_yanitlar.md`** — okunaklı: her test için istek, JARVIS'in tam cevabı, hangi model
  cevapladı, ne kadar sürdü, onay istendiyse payload'ı ve verilen karar.
- **`raw_results.jsonl`** — driver'ın yazdığı ham JSON (yukarıdakinin kaynağı, birebir).
- **`audit_log.jsonl`** — JARVIS'in **gerçekten çalıştırdığı** her L2+ araç çağrısının kaydı
  (decision + execution_start + execution_end). Modelin ne SÖYLEDİĞİ değil, ne YAPTIĞI.
- **`olusturulan_dosyalar/`** (varsa) — o turda workspace köküne veya `data/plots/`'a
  gerçekten yazılan dosyalar (örn. `jarvis_test.txt`).
- **`olusturulan_dosyalar_YOK.txt`** (varsa) — o turda hiçbir dosya oluşmadığının kanıtı.

## Turlar

| Klasör | Model | Ne |
|---|---|---|
| 01/02 | qwen2.5:7b-instruct | Eski default, baseline (2 tekrar) |
| 03/05 | qwen3:8b | Yeni aday, A/B (2 tekrar) |
| 04 | qwen3:8b | Sadece D10 onay-akışı düzeltme kontrolü |
| 06 | qwen3:8b | **Final kabul turu** — şu anki default'la, en güncel kod |

## ÖNEMLİ — B6 (grafik) testi aslında BAŞARISIZ, önceki raporum yanlıştı

Önceki turda B6'yı "✓" saymıştım. **Yanlıştı — 3 ayrı koşumda da (03, 05, 06) hiçbir grafik
üretilmedi:**

- Model her seferinde `plot_data` aracını ya var olmayan bir CSV dosyasına (`jarvis_test.csv`)
  ya da işe yaramaz bir path'e (`workspace`) referansla çağırdı — kendi verdiği sayıları
  (1,4,9,16) önce bir dosyaya yazmadı.
- Araç doğru şekilde hata verdi: `[ERROR] Data file not found: ...`
- `data/plots/` klasörü **hiçbir** test home'unda hiç oluşmadı (bkz. `06_.../olusturulan_dosyalar_YOK.txt`
  — 06'da `jarvis_test.txt` var ama plot yok).
- Buna rağmen JARVIS **kullanıcıya yalan söyledi**: *"Grafik başarıyla oluşturuldu. Görsel
  dosyası `line_graph.png` olarak çalışma dizininizde mevcut."* (`06_final_acceptance_.../sorgu_ve_yanitlar.md`, `B6`)

**İkinci, bağımsız bug bulundu bu incelemede:** `audit_log.jsonl`'deki bu çağrı `"ok": true`
yazıyor — halbuki `result_preview` içinde `[ERROR] Data file not found` var. Kök neden:
[jarvis/agent.py:128](jarvis/agent.py:128) `_record_execution_end`'de `out_s = str(output)`
kullanılıyor; `output` burada ham bir string değil bir `ToolMessage` **nesnesi**, `str()`'i
`"content='[ERROR]...' name='plot_data' ..."` şeklinde başlıyor — yani `out_s.startswith("[ERROR]")`
kontrolü hiç eşleşmiyor, "ok" hep `True` kalıyor. **Bu, Faz 1B'nin kendi dedup/ledger mantığını
(`jarvis/graph/tool_accounting.py`) etkilemiyor** (o kod gerçek `ToolMessage.content` alanına
doğrudan bakıyor, doğru çalışıyor) — yalnızca `agent.py`'nin **audit log / HUD** tarafı yanlış,
ve bu Faz 4'ten kalma, benim bu oturumda yazmadığım ama fark etmediğim bir kod.

Bunun anlamı: önceki mesajımdaki "gerçek tool-call üretimi ≈ %100" ve kabul tablosundaki B6
satırı yanlıştı. Düzeltilmiş durum: **15/16 test gerçekten geçti, B6 gerçekten başarısız oldu**
(hem araç hem de modelin buna rağmen başarı iddia etmesi).

## Diğer gözlem (yeni, kapsam dışı bırakıldı)

`D10`/`D13b` yanıtlarında `shell_run` ile çalıştırılan `dir` komutu, test home'unu değil
**gerçek proje kökünü** (`C:\Users\mertk\Desktop\Jarvis` — `.venv`, `Jarvis.rar`, `CLAUDE.md`
görünüyor) listelemiş. Kontrol ettim: [jarvis/graph/tools.py:63-68](jarvis/graph/tools.py:63)'teki
`shell_run`, `file_read`/`file_write`/`file_list`'in aksine `workspace` parametresi almıyor —
`shell_tools.run()` doğrudan process'in gerçek çalışma dizininde (`JARVIS_HOME`'a taşınmadan)
çalışıyor. `--profile test`'in "gerçek veriye dokunmaz" garantisi `data/`/dosya araçları için
geçerli, ama `shell_run`'ın komut çalıştırdığı dizin için değil. Zararsız `dir` gibi komutlarda
sorun yaratmadı ama bir sınır olarak not ediyorum — düzeltme yapılmadı, bu senin kararın.

## Sonraki adım (senin kararın)

İki gerçek bug bulundu bu incelemede (B6'nın audit-ok yalanı + shell_run'ın workspace'e
saygı göstermemesi). İstersen ikisini de düzeltip test edeyim, ya da ayrı bir görev olarak
`spawn_task` ile arka plana alayım — hangisini tercih edersin?
