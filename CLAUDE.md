# Attention Half-Life (İlginin Yarılanma Süresi)

İnternet ne kadar hızlı unutuyor? Bu proje olayların **ilgi yarılanma süresini** ölçer:
bir olaya gösterilen kamusal ilginin zirvedeki seviyesinin yarısına düşmesi kaç gün sürüyor?

İlgi, Wikipedia sayfa görüntülenmeleriyle (Wikimedia Pageviews API) ölçülür.

## Temel yöntem (bana sormadan değiştirme)

Her olay için (tek bir Wikipedia makalesi, tek bir dil projesi):

1. **Baseline** = olay tarihinden *önceki* 30 günün günlük görüntülenme medyanı.
   Makale yoksa ya da 7 günden az geçmişi varsa baseline = 1.
2. **Fazla ilgi** `excess(t) = views(t) - baseline`, negatif değerler 0'a kırpılır.
3. Zirve ve eşik geçişlerini bulmadan önce 3 günlük ortalanmış hareketli ortalama ile yumuşat.
4. **Zirve** = olay tarihinin ±7 günü içinde yumuşatılmış fazla ilginin en yüksek olduğu gün.
5. **Yarılanma süresi** = zirveden, yumuşatılmış fazla ilginin zirvenin %50'sinin altına ilk düştüğü güne kadar geçen gün sayısı.
   365 gün içinde düşmezse sansürlü olarak kaydet (`half_life = NaN`, `censored = True`).
6. Ayrıca şunları sakla: zirve görüntülenmesi, baseline, olay tarihinden zirveye kadar geçen gün (zirve öncesi yükseliş)
   ve ikincil zirve sayısı (ilk kez %50'nin altına düştükten sonra yumuşatılmış fazla ilginin ana zirvenin %30'unu aştığı tepeler).
7. Zirve sonrası eğriye hem üstel (exponential) hem de power-law bozulma modeli uydur;
   parametreleri ve hangisinin daha iyi oturduğunu (daha düşük AIC) kaydet.

## Veri kaynağı kuralları

- API adresi: `https://wikimedia.org/api/rest_v1/metrics/pageviews/`
- Botları dışlamak için her zaman `agent=user` kullan.
- Her istekte açıklayıcı bir `User-Agent` başlığı gönder: `attention-half-life/0.1 (GitHub: <kullanıcı-adı>)`.
- İstekleri makul hızda tut: saniyede en fazla ~5 istek, 429/5xx hatalarında bekleyip tekrar dene (backoff).
- **Her API yanıtını yerelde önbelleğe al**, `data/cache/` altında (SQLite). Aynı isteği asla iki kez atma.
  Script'ler yarıda kesilirse kaldığı yerden devam edebilmeli.
- Olay tarihi veya makale başlığı uydurma. Her makalenin varlığını API üzerinden doğrula.

## Repo yapısı

```
README.md
requirements.txt
data/
  events.csv        # olay listesi: event_id, title, project, event_date, category, source
  results.csv       # her olay için bir satır, tüm metriklerle
  cache/            # yerel API önbelleği (gitignore'da)
src/
  fetch.py          # önbellekli Pageviews API istemcisi
  halflife.py       # baseline, zirve, yarılanma süresi, bozulma modelleri
  analyze.py        # kategori / yıl / dil karşılaştırmaları, grafikler
  discover.py       # günlük top-1000 listelerinden otomatik olay keşfi
  classify.py       # Wikidata ile otomatik kategorizasyon (P31, P570)
tests/
  test_halflife.py  # yarılanma süresi bilinen sentetik eğrilerle birim testleri
figures/            # README'de kullanılan, üretilmiş PNG'ler
pytest.ini          # testlerin `src` paketini bulması için
docs/               # GitHub Pages etkileşimli demo (statik HTML/JS, sunucu yok)
.github/workflows/update.yml
```

## Kurallar

- Python 3.11+, pandas, requests, matplotlib, scipy, pytest. Bağımlılıkları minimumda tut.
- Public fonksiyonlarda type hint ve docstring olsun. Küçük, test edilebilir fonksiyonlar yaz.
- Her script komut satırından `argparse` ile çalışabilsin ve `--help` desteklesin.
- Ham önbellek verisini commit'leme; `events.csv`, `results.csv` ve grafikleri commit'le.
- Kod ve yorumlar İngilizce olsun. README Türkçe (en altta kısa bir İngilizce özetle). Benimle konuşurken Türkçe yaz.
- `halflife.py`'ye dokunan her görevi bitirmeden önce `pytest` çalıştır.
- Her aşamanın sonunda açıklayıcı bir mesajla commit at.

## Olay keşfi (2. aşama)

- 2016-01-01'den itibaren günlük top-1000 listelerini çek (`/top/{project}/all-access/{yyyy}/{mm}/{dd}`).
- Aday olay: günlük görüntülenmesi önceki 30 günün medyanının ≥10 katı olan
  **ve** önceki 30 gün içinde top listede hiç görünmemiş makale.
- Hariç tut: ana sayfalar, `Special:` / `Özel:` ad alanları, arama sayfaları.
- Bot filtresi: masaüstünde sıçrayıp mobile-web'de yerinde sayan spike'ları at.
- Her yıl tekrarlayan olayları (aynı makalenin ≥3 farklı yılda aynı tarih civarında sıçraması) `recurring` olarak etiketle.

## Kategorizasyon (2. aşama)

Wikidata `instance of (P31)` kullan:
- human + ölüm tarihi (P570) spike'ın ±3 günü içinde → `death`
- diğer human → `person_in_news`
- earthquake / flood / wildfire / storm → `disaster`
- election / referendum → `election`
- film / TV series / album / video game → `entertainment`
- sports event / season / match → `sports`
- diğer her şey → `other`

Elle seçilen olaylarda ek olarak şu iki kategori de kullanılabilir: `accident` (kaza, ör. Ever Given) ve `technology` (ürün lansmanı, kesinti vb.).
Wikidata isteklerini toplu gönder (çağrı başına en fazla 50 ID).
