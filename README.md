# İş Başvuru Paneli / Job Application Tracker

Çok kullanıcılı bir iş başvurusu takip uygulaması: kimin hangi şirkete, hangi
pozisyona, ne zaman ve hangi kanaldan başvurduğunu takip eder; adaylık
durumunu (gönderildi/mülakat/teklif/olumsuz) günceller, takip gereken
başvuruları hatırlatır ve değerlendirilebilecek yeni ilanları listeler. Her
kullanıcı yalnızca kendi verisini görür ve düzenler.

İki ayrı parça olarak gelişti:

1. **`tracker.py`** -- bağımsız, tek kullanıcılı bir komut satırı aracı
   (sadece Python standart kütüphanesi, SQLite). Kendi test paketi
   (`tests/test_tracker.py`) ile birlikte, aşağıdaki web uygulamasından
   tamamen ayrı çalışır.
2. **`app.py`** -- Flask tabanlı, çok kullanıcılı web paneli. Bu README'nin
   geri kalanı bu web uygulamasını anlatıyor.

## Özellikler

- **Çok kullanıcılı hesaplar:** ad soyad, e-posta, kullanıcı adı, şifre,
  hedef pozisyon, şehir tercihi ile kayıt; kullanıcı adı/e-posta ile giriş,
  "beni hatırla", 5 başarısız denemede geçici hesap kilidi; profil
  sayfasından bilgi güncelleme, şifre değiştirme, verilerini CSV olarak
  indirme, hesabı kalıcı silme. Şifreler Argon2 ile hash'lenir.
- **KVKK onayı**, isteğe bağlı **e-posta doğrulama** ve **şifremi unuttum**
  (SMTP ayarlanmazsa bu ikisi sessizce devre dışı kalır, uygulama yine
  çalışır).
- **Excel tarzı tablolar:** başvuru ve ilan tablolarında sütun başlığına
  tıklayarak sıralama, sütun bazlı filtre + genel arama, hücreye çift
  tıklayarak yerinde düzenleme -- sayfa yenilenmeden, AJAX ile.
- **İstatistikler sayfası (`/stats`):** durum/kanal/ilan-etiketi dağılımı ve
  aylık başvuru sayısı grafikleri, mülakat+teklif oranı, takip bekleyen
  başvuru sayısı.
- **Gmail'den içe aktarma (opsiyonel):** Google OAuth ile kendi Gmail
  hesabını bağla, uygulama olası başvuru e-postalarını tarar, hiçbirini
  otomatik kaydetmeden önce şirket/pozisyon/tarih/kanalı düzenleyip
  onayladığın bir inceleme ekranı sunar.
- **CSV içe/dışa aktarma:** profil sayfasından başvurularını ve ilanlarını
  CSV olarak indir; başka bir kaynaktan hazırladığın CSV'yi içe aktar.
- **Demo modu (opsiyonel):** giriş sayfasında "Demoyu dene" butonu, kayıt
  olmadan örnek verilerle dolu izole bir hesaba anında giriş yapar.
- **TR/EN dil desteği**, koyu/açık tema.
- **SQLite (geliştirme) / PostgreSQL (üretim)**, şema değişiklikleri Alembic
  migration'ları ile yönetilir (`migrations/`), uygulama her açılışta
  otomatik `flask db upgrade` çalıştırır.

## Hızlı başlangıç (yerel geliştirme)

```bash
pip install -r requirements.txt
cp .env.example .env
# .env içinde en az şunları doldur: SECRET_KEY, PANEL_USERNAME, PANEL_PASSWORD
python app.py
```

Varsayılan olarak `http://localhost:5000` adresinde, yerel bir SQLite
dosyasıyla (`applications.db`) çalışır. İlk açılışta `PANEL_USERNAME`/
`PANEL_PASSWORD` ile bir "sahip" hesabı otomatik oluşturulur -- bu hesapla
giriş yapabilir ya da `/register` üzerinden yeni bir hesap açabilirsin.

Testleri çalıştırmak için:

```bash
python -m pytest tests/ -q
```

## Ortam değişkenleri

Tamamı `.env.example` dosyasında açıklamalarıyla birlikte listeli. Özet:

| Değişken | Zorunlu mu? | Ne işe yarar |
|---|---|---|
| `SECRET_KEY` | Evet | Flask session imzası, şifreleme anahtarı türetme |
| `PANEL_USERNAME` / `PANEL_PASSWORD` | Evet | `/admin/*` HTTP Basic Auth + sahip hesabının ilk kurulumu |
| `DATABASE_URL` | Hayır | PostgreSQL bağlantı adresi (üretim); boşsa SQLite kullanılır |
| `DB_PATH` | Hayır | `DATABASE_URL` boşsa kullanılacak SQLite dosya yolu |
| `OWNER_EMAIL` vb. | Hayır | Mevcut sahip hesabının profil alanlarını doldurmak için (tek seferlik) |
| `MAIL_*` | Hayır | SMTP -- e-posta doğrulama ve şifre sıfırlama için |
| `GOOGLE_CLIENT_ID`/`SECRET` | Hayır | Gmail içe aktarma özelliği için |
| `DEMO_MODE` | Hayır | `true` ise herkese açık demo girişi aktif olur |

## Gmail içe aktarma kurulumu

Bu özellik tamamen opsiyoneldir; `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
ayarlanmazsa profil sayfasında "Bu özellik şu anda kapalı" yazar ve
uygulamanın geri kalanı normal çalışır. Kurmak istersen:

1. [Google Cloud Console](https://console.cloud.google.com/) üzerinde yeni
   bir proje oluştur (veya mevcut birini kullan).
2. **APIs & Services → Library** içinden **Gmail API**'yi etkinleştir.
3. **APIs & Services → OAuth consent screen**'de bir "External" uygulama
   oluştur; "Testing" aşamasında bırakıp kendi Gmail adresini (ve
   denemesini istediğin başka adresleri) **Test users** listesine ekle --
   bu, uygulamanın Google tarafından tam doğrulanmasını beklemeden,
   belirlediğin kullanıcılarla çalışmasını sağlar.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**,
   tip olarak **Web application** seç. **Authorized redirect URIs**'a
   uygulamanın adresini ekle:
   - Yerelde: `http://localhost:5000/gmail/callback`
   - Railway/üretimde: `https://<alan-adın>/gmail/callback`
5. Oluşan **Client ID** ve **Client secret**'ı `.env` dosyana
   `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` olarak yaz.
6. Uygulamayı yeniden başlat. Profil sayfasında "Gmail hesabını bağla"
   butonu görünecek.

Uygulama yalnızca salt-okunur `gmail.readonly` izni ister, e-posta
göndermez/silmez/değiştirmez. Taranan e-postalardan hiçbiri otomatik
kaydedilmez -- "Eski başvuruları tara" bir inceleme ekranı açar, orada her
satırı düzenleyip işaretleyip onayladıktan sonra başvuru olarak kaydedilir.
Gmail'in refresh token'ı veritabanında düz metin değil, `SECRET_KEY`'den
türetilen bir anahtarla şifrelenmiş olarak tutulur.

## Demo modu

`DEMO_MODE=true` ayarlandığında giriş sayfasında "Demoyu dene" butonu
belirir. Tıklanınca:

- Rastgele kullanıcı adlı (`demo-xxxxxxxx`), kayıt formuna gerek kalmadan
  anında giriş yapılan, örnek başvuru/ilan verileriyle önceden doldurulmuş
  izole bir hesap oluşturulur.
- Demo hesapları birbirinden tamamen izoledir (normal hesaplarla aynı
  kurallar geçerli).
- Gmail bağlama ve CSV içe aktarma, demo hesaplarında gizlenir (gerçek
  hesap verisi/kimlik bilgisi karışmasını önlemek için).
- 24 saatten eski demo hesapları, bir sonraki "Demoyu dene" tıklamasında
  otomatik temizlenir.

## Deploy (Railway)

Repo bir `Procfile` içerir (`gunicorn app:app`). Railway'de:

1. Yeni bir servis oluştur, bu repoyu bağla (veya `railway up` ile deploy
   et).
2. Kalıcı veri için bir Volume ekle (örn. `/data`) ve `DB_PATH` ortam
   değişkenini o yola ayarla -- ya da yönetilen bir PostgreSQL eklentisi
   bağlayıp `DATABASE_URL`'i kullan.
3. Yukarıdaki zorunlu ortam değişkenlerini (en az `SECRET_KEY`,
   `PANEL_USERNAME`, `PANEL_PASSWORD`) Railway'in Variables sekmesinden
   gir.
4. Uygulama her açılışta kendi Alembic migration'larını otomatik çalıştırır
   (`flask db upgrade`); elle bir şey çalıştırman gerekmez, mevcut bir
   veritabanı da güvenle yeni şemaya geçer.

## Mimari notları

- **Veri katmanı:** `models.py` (SQLAlchemy: `User`, `Application`,
  `CandidateJob`), şema değişiklikleri `migrations/versions/` altında
  Alembic migration'ları olarak tutulur -- `db.create_all()` değil, bu
  migration'lar çalıştırılır, böylece eski bir üretim veritabanı da yeni
  sütunları güvenle alır.
- **Kimlik doğrulama:** Flask-Login + Argon2 (eski Werkzeug hash'li
  hesaplar ilk girişte sessizce Argon2'ye yükseltilir).
- **`tracker.py` / `jobs.py`:** web uygulamasının SQLAlchemy katmanından
  tamamen bağımsız, kendi ham `sqlite3` fonksiyonlarıyla çalışan CLI aracı;
  sadece ortak sabitler (`VALID_CHANNELS` vb.) web tarafından yeniden
  kullanılır.
- **`/admin/*` uç noktaları:** HTTP Basic Auth (`PANEL_USERNAME`/
  `PANEL_PASSWORD`) ile korunan, dış bir betiğin (örn. toplu ilan/başvuru
  ekleyen bir otomasyon) sahip hesabına veri yazması için kullanılır.
- **Testler:** `tests/` altında -- CLI testleri (`test_tracker.py`),
  kimlik doğrulama ve hesap testleri (`test_auth.py`), hesaplar arası veri
  izolasyonu testleri (`test_isolation.py`), Excel-tarzı tablo uç noktaları
  (`test_grid.py`), istatistik sayfası (`test_stats.py`), CSV/demo/Gmail
  uç noktaları (`test_stage4.py`, `test_gmail_integration.py`).

## `tracker.py` komut satırı aracı

Web uygulamasından bağımsız, tek başına kullanılabilir:

```bash
python tracker.py add --company "Acme Corp" --position "Backend Engineer" \
  --date 2026-10-20 --channel email --note "Referans ile başvurdum"
python tracker.py list
python tracker.py status 3 interview
python tracker.py followup
```

Veriler, proje klasöründe otomatik oluşan `applications.db` dosyasında
tutulur (`.gitignore`'da, repoya eklenmez).
