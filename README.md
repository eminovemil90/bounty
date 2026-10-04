# Bounty Scope Monitor

VPS-də işləyən avtomatik bug bounty scope izləyici. [`arkadiyt/bounty-targets-data`](https://github.com/arkadiyt/bounty-targets-data) repo-sunu hər 8 saatda bir yoxlayır, yeni domenlər tapanda Gmail + Telegram bildirişi göndərir və yalnız ödənişli (BBP) proqramların domenlərinə Nuclei skanı icra edir.

---

## Arxitektura

```
VPS cron (hər 8 saatda)
  └─ scope_monitor.py
       ├─ GitHub API-dan son SHA oxunur
       ├─ Dəyişiklik yoxdursa → çıxır
       └─ Dəyişiklik varsa
            ├─ domains.txt / wildcards.txt fərqi çıxarılır
            ├─ HackerOne / Bugcrowd / Intigriti / YWH JSON-ları parse edilir
            ├─ Gmail + Telegram bildirişi göndərilir
            └─ Yalnız BBP domenlərinə Nuclei skanı başladılır (background)

VPS cron (hər 30 dəqiqədə)
  └─ send_nuclei_results.py
       ├─ Nuclei hələ işləyirsə → keçir (gözləyir)
       └─ Bitibsə → bütün nəticələri bir dəfəlik Email + Telegram-a göndərir
```

---

## Fayllar

| Fayl | İş |
|---|---|
| `scope_monitor.py` | Əsas skript. Scope dəyişikliklərini izləyir, bildiriş göndərir, nuclei-ni başladır |
| `send_nuclei_results.py` | Nuclei nəticələrini toplu göndərir (nuclei bitəndən sonra) |
| `state.json` | Son yoxlanılan commit SHA-sı (avtomatik yaradılır) |
| `.env` | Mühit dəyişənləri (repo-ya daxil edilmir) |

---

## Mühit dəyişənləri (`.env`)

```env
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # Google App Password
TELEGRAM_BOT_TOKEN=123456:ABC-...
TELEGRAM_CHAT_ID=123456789
GITHUB_TOKEN=ghp_...                     # İstəyə bağlı — API rate limit artırır
```

---

## VPS Quraşdırma

### 1. Sistem

```bash
apt update && apt install -y python3 python3-pip git
pip3 install python-dotenv
```

### 2. Go + Nuclei

```bash
wget https://go.dev/dl/go1.22.5.linux-amd64.tar.gz
tar -C /usr/local -xzf go1.22.5.linux-amd64.tar.gz
echo 'export PATH=$PATH:/usr/local/go/bin:/root/go/bin' >> ~/.bashrc
source ~/.bashrc

go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest
nuclei -update-templates
```

### 3. Repo

```bash
mkdir -p /opt/bounty
cd /opt/bounty
git clone https://github.com/SENIN_USER/SENIN_REPO.git .
```

### 4. `.env` faylı

```bash
nano /opt/bounty/.env
# yuxarıdakı dəyişənləri əlavə et
chmod 600 /opt/bounty/.env
```

### 5. Cron

```bash
crontab -e
```

Əlavə et:

```
0 */8 * * * cd /opt/bounty && python3 scope_monitor.py >> /var/log/bounty.log 2>&1
*/30 * * * * cd /opt/bounty && python3 send_nuclei_results.py >> /var/log/bounty.log 2>&1
```

---

## Scope Monitor nə edir (`scope_monitor.py`)

### GitHub izlənməsi

- [`arkadiyt/bounty-targets-data`](https://github.com/arkadiyt/bounty-targets-data) repo-sunun son commit SHA-sını çəkir
- Öncəki SHA ilə müqayisə edir (`state.json`-da saxlanır)
- Dəyişiklik yoxdursa çıxır; varsa dəyişiklikləri analiz edir

### Izlənən fayllar

| Fayl | Nə izlənir |
|---|---|
| `data/domains.txt` | Agregat domen siyahısı — sətir əsaslı fərq |
| `data/wildcards.txt` | Wildcard domenler (`*.example.com`) |
| `data/hackerone_data.json` | `asset_identifier` sahəsi |
| `data/bugcrowd_data.json` | `target` sahəsi |
| `data/intigriti_data.json` | `endpoint` sahəsi |
| `data/yeswehack_data.json` | `target` sahəsi |

### Scope filtrləmə

Aşağıdakılar **nəzərə alınmır**:

- App Store / Google Play linklər (`play.google.com`, `itunes.apple.com`)
- GitHub / GitLab URL-ləri
- RFC 1918 + loopback + link-local IP-lər (`192.168.x.x`, `10.x.x.x`, vs.)
- Reversed TLD formatı (`com.example.android`)
- Mobile bundle ID-ləri (`android.`, `.ios.`)
- Boşluq olan dəyərlər

### Bildiriş formatı

**Email**: Tam hesabat — bütün platformlar, commit tarixçəsi, əlavə/silmə siyahısı

**Telegram**: Qısa xülasə — yeni proqramlar, scope dəyişiklikləri, ilk 10 domen

### BBP filtrləmə (Nuclei üçün)

Nuclei yalnız **ödənişli (Bug Bounty)** proqramların domenlərinə skan edir, VDP-lər atlanır:

| Platform | BBP meyarı |
|---|---|
| HackerOne | `"offers_bounties": true` |
| Bugcrowd | `max_payout` dəyəri mövcud və sıfır deyil |
| Intigriti / YWH | Proqram adında `vdp` söz keçmədikdə BBP sayılır |

Xəta halında fail-open: filtrlənə bilmirsə bütün proqramlar daxil edilir.

---

## Nuclei Pipeline

### Axış

```
all_new_domains (BBP-dən + domains.txt + wildcards.txt)
  │
  ├─ httpx: canlı domenləri yoxla (50 thread, 5s timeout)
  │          ölü domainlər atlanır
  │
  └─ Hər 50 domainlik batch üçün ayrı nuclei prosesi
       └─ severity: critical, high, medium
          timeout: 2 saat/batch
          rate-limit: 25 req/s
          nəticələr: /opt/bounty/nuclei_results.txt (append)
```

### Sabitlər

```python
NUCLEI_BIN    = "/root/go/bin/nuclei"
BATCH_SIZE    = 50          # hər batch-dəki domen sayı
NUCLEI_TIMEOUT = 7200       # saniyə (2 saat)
```

### Proses idarəetməsi

- Hər yeni runda köhnə nuclei prosesləri `pkill` ilə dayandırılır
- Nuclei tapılmadıqda skan atlanır (xəta log-a yazılır)
- Nəticələr `nuclei_results.txt`-ə append edilir

---

## Nuclei Nəticə Göndərmə (`send_nuclei_results.py`)

- Hər 30 dəqiqədə cron tərəfindən çağırılır
- `pgrep -f nuclei` ilə aktiv proseslər yoxlanır
- **Nuclei işləyirsə çıxır** — nəticələr gözlənilir
- Nuclei bitdikdə `nuclei_results.txt` oxunur, `nuclei_sent.txt` ilə müqayisə edilir
- Yeni tapıntılar **bir dəfəlik** Email + Telegram-a göndərilir (hissə-hissə deyil)
- Göndərilənlər `nuclei_sent.txt`-ə yazılır (dedup üçün)

---

## Loglar

```bash
tail -f /var/log/bounty.log
```

---

## Manual Test

```bash
cd /opt/bounty

# İlk run (baseline)
python3 scope_monitor.py

# Dəyişikliyi simulyasiya et — SHA-nı köhnə bir dəyərə dəyiş
echo '{"last_sha": "KÖHNƏ_SHA"}' > state.json
python3 scope_monitor.py

# Nuclei nəticə bildirişini manual işlət
python3 send_nuclei_results.py
```

---<img width="987" height="850" alt="image" src="https://github.com/user-attachments/assets/f415e358-0429-43ce-a758-08d97f4e6974" />


## Texniki Detallar

- GitHub API retry: 3 cəhd, eksponensial geri-çəkilmə (1s / 2s / 4s)
- GitHub token varsa API rate limit 5000 req/saat-a çıxır (yoxdursa 60)
- `state.json` korrupsiya halında avtomatik sıfırlanır
- Telegram mesajları 4000 simvoldan böyüksə avtomatik bölünür
- Email xətası `save_state`-i bloklamır (növbəti run düzgün işləyir)
- `compare` status `too_many_commits` olarsa xəbərdarlıq log-a yazılır

---

## Asılılıqlar

```
python3
python-dotenv
nuclei     (/root/go/bin/nuclei)
httpx      (PATH-da olmalıdır)
```

![Uploading image.png…]()
