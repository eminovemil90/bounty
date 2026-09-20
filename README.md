# Bounty Scope Monitor

Hər 5 saatda bir [`arkadiyt/bounty-targets-data`](https://github.com/arkadiyt/bounty-targets-data) repo-sunu yoxlayır. Yeni dəyişiklik olsa Gmail və Telegram vasitəsilə bildiriş göndərir.

## Nə izləyir?

| Fayl | Məzmun |
|---|---|
| `data/domains.txt` | Əlavə/silinən domenler |
| `data/wildcards.txt` | Əlavə/silinən wildcard-lar |
| `data/hackerone_data.json` | HackerOne proqram dəyişiklikləri |
| `data/bugcrowd_data.json` | Bugcrowd proqram dəyişiklikləri |
| `data/intigriti_data.json` | Intigriti proqram dəyişiklikləri |
| `data/yeswehack_data.json` | YesWeHack proqram dəyişiklikləri |

## Necə işləyir?

1. GitHub Actions hər 5 saatda avtomatik işləyir (`0 */5 * * *`)
2. Script `state.json`-dakı son SHA ilə cari HEAD SHA-nı müqayisə edir
3. Fərq varsa GitHub Compare API ilə dəqiq diff-i çəkir
4. Patch-dən domenler, wildcardlar və proqram adlarını parse edir
5. Yeni proqramları meta-update-dan ayırd edir (yalnız `+` sətirdə olan → YENİ, həm `+` həm `-` sətirdə olan → dəyişiklik)
6. Email + Telegram göndərir, `state.json`-u yeniləyir

Komp söndürsə belə problem yox — GitHub Actions cloud-da işləyir, SHA-based müqayisə sayəsində növbəti runda bütün keçirilmiş dəyişiklikləri toplayır.

## Bildiriş formatı

**Email:**
```
🎯 BB Scope Report — 3 commit (2026-09-20 11:00 → 2026-09-20 16:00 UTC)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 COMMIT TARİXÇƏSİ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  a1b2c3d — 2026-09-20 11:00 UTC
  ...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 DOMEN DƏYİŞİKLİKLƏRİ (domains.txt)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YENİ (+5):
  + api.example.com
  ...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 HACKERONE PROQRAM DƏYİŞİKLİKLƏRİ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🆕 YENİ PROQRAM(LAR):
  ★ ExampleCorp
```

**Telegram:** Kompakt versiya — yeni proqramlar, domenler, wildcard-lar.

## Quraşdırma

GitHub repo-sunda bu secretləri əlavə et (`Settings → Secrets → Actions`):

| Secret | Dəyər |
|---|---|
| `GMAIL_APP_PASSWORD` | Gmail App Password ([buradan al](https://myaccount.google.com/apppasswords)) |
| `TELEGRAM_BOT_TOKEN` | BotFather-dən alınan token |
| `TELEGRAM_CHAT_ID` | Botuna mesaj göndər, chat ID-ni öyrən |

## Fayllar

```
scope_monitor.py   — əsas script
state.json         — son yoxlanan SHA (GitHub Actions tərəfindən yenilənir)
.github/workflows/
  scope-monitor.yml — GitHub Actions workflow
```
