# 2026 Otel Fırsatları — Forum Scraper + Qwen Analizi + Görselleştirme

Donanimhaber **"2026 F/P Otel Fırsatları"** konusundaki yorumları toplayan,
lokal **LM Studio / Qwen** modeliyle analiz eden ve sonuçları arama yapılabilen
bir HTML sayfasında gösteren üç parçalı bir araç.

Konu: https://forum.donanimhaber.com/2026-f-p-otel-firsatlari--161803716 (571 sayfa)

## Kurulum

```bash
pip install -r requirements.txt
```

## 1) Forumu tara → `desifre.json`

Her sayfadaki mesajların **kullanıcı adı**, **tarih** ve **mesaj içeriğini**
toplar.

```bash
python scrape_forum.py --pages 1     # tek sayfa ile test (desifre.json oluşur)
python scrape_forum.py               # 571 sayfanın tamamı
python scrape_forum.py --start 1 --end 50   # sayfa aralığı
```

**Anti-bot / Cloudflare önlemleri:** gerçekçi tarayıcı header'ları, dönüşümlü
User-Agent, sayfalar arası rastgele bekleme, yeniden deneme (exponential
backoff) ve anti-bot doğrulaması algılanırsa otomatik olarak `cloudscraper`'a
geçiş.

**Devam edebilir (resumable):** Her mesaja benzersiz bir sıra numarası (`id`)
ve foruma özgü kalıcı bir kimlik (`message_id`) atanır. Çıktı her sayfadan
sonra kaydedilir; işlem yarıda kesilip script tekrar çalıştırılırsa var olan
`desifre.json` okunur, daha önce kaydedilmiş mesajlar `message_id` ile
atlanır ve sıra numaraları kaldığı yerden devam eder (eski kayıtlar yeniden
incelenmez, mükerrer olmaz).

> Çıktı `desifre.json` (ASCII güvenli ad; istenen "deşifre.json" ile aynı içerik).
> Her kayıt: `id`, `message_id`, `page`, `username`, `date`, `message`.

## 2) LM Studio + Qwen ile analiz → `yorum.json`

Önce **LM Studio**'da Local Server'ı başlatın (varsayılan `http://localhost:1234`)
ve bir Qwen modeli yükleyin. Sonra:

```bash
python analyze_messages.py --limit 5          # birkaç mesajla test
python analyze_messages.py --model qwen2.5-7b-instruct
```

Her mesaj için çıkarılan bilgiler `yorum.json`'a yazılır:
`id`, `message_id`, `otel` (bahsedilen otel), `fiyat` (varsa),
`ozet` (yorum özeti) + kullanıcı/tarih.

**Bu script de devam edebilir:** var olan `yorum.json` okunur ve daha önce
analiz edilmiş mesajlar (`id` ile) atlanır. Modele ulaşılamazsa o mesaj
**kaydedilmez**, böylece bir sonraki çalıştırmada otomatik olarak yeniden
denenir.

> LM Studio bu makinede lokal çalıştığı için analizi kendi bilgisayarınızda
> çalıştırmanız gerekir. Depodaki `yorum.json` şu an HTML'i önizlemeniz için
> birkaç **örnek** kayıt içerir; `analyze_messages.py`'yi çalıştırınca gerçek
> verilerle değişir.

## 3) Görselleştir → `yorumlar.html`

`yorum.json`'u okuyup modern, aranabilir kartlar halinde gösterir
(otel / yorum / fiyat). Arama, sıralama ve filtreleme içerir.

```bash
# Aynı klasörde basit bir sunucu çalıştırıp tarayıcıda açın:
python -m http.server 8000
# -> http://localhost:8000/yorumlar.html
```

Dosyayı doğrudan (`file://`) açarsanız tarayıcı `yorum.json`'u otomatik
çekemez; bu durumda sayfadaki **dosya seç** kutusundan `yorum.json`'u elle
yükleyebilirsiniz.
