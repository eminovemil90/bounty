import json
import os
import smtplib
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

REPO = "arkadiyt/bounty-targets-data"
STATE_FILE = "state.json"
GMAIL_USER = "eminovemil90@gmail.com"
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bounty-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_sha": None}


def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def parse_patch_lines(patch):
    added, removed = [], []
    if not patch:
        return added, removed
    for line in patch.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            val = line[1:].strip()
            if val:
                added.append(val)
        elif line.startswith("-") and not line.startswith("---"):
            val = line[1:].strip()
            if val:
                removed.append(val)
    return added, removed


def parse_program_names(patch):
    """
    Patch-dən proqram adlarını çıxart.
    Yalnız + olan amma - olmayan adlar → YENİ proqram.
    Hər iki işarədə olan adlar → dəyişiklik (meta update), yeni deyil.
    """
    added_names, removed_names = [], []
    if not patch:
        return added_names, removed_names, []

    raw_added, raw_removed = set(), set()
    for line in patch.split("\n"):
        if '"name"' not in line:
            continue
        try:
            name = line.split('"name"')[1].split('"')[2]
            if not name:
                continue
            if line.startswith("+"):
                raw_added.add(name)
            elif line.startswith("-"):
                raw_removed.add(name)
        except Exception:
            pass

    # Həm + həm - olan adlar = yalnız dəyişiklik (yeni proqram deyil)
    truly_new = sorted(raw_added - raw_removed)
    truly_removed = sorted(raw_removed - raw_added)
    modified = sorted(raw_added & raw_removed)

    return truly_new, truly_removed, modified


def send_email(subject, body):
    if not GMAIL_PASS:
        print("GMAIL_APP_PASSWORD not set — skipping email")
        return
    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.send_message(msg)
    print(f"Email göndərildi: {subject}")


def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not set — skipping")
        return
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())
    if resp.get("ok"):
        print("Telegram bildirişi göndərildi")
    else:
        print(f"Telegram xətası: {resp}")


def build_telegram_message(commits, domains_added, domains_removed,
                            wildcards_added, h1_new, h1_removed,
                            bc_new, bc_removed, first_date, last_date):
    lines = [f"🎯 <b>BB Scope Report</b>"]
    lines.append(f"📅 {first_date} → {last_date} UTC  |  {len(commits)} commit\n")

    if h1_new:
        for name in h1_new:
            lines.append(f"🆕 <b>YENİ PROQRAM (H1):</b> {name}")
    if bc_new:
        for name in bc_new:
            lines.append(f"🆕 <b>YENİ PROQRAM (BC):</b> {name}")

    if domains_added:
        lines.append(f"\n✅ <b>Yeni domenler (+{len(domains_added)}):</b>")
        for d in domains_added[:10]:
            lines.append(f"  + {d}")
        if len(domains_added) > 10:
            lines.append(f"  ... +{len(domains_added) - 10} daha")

    if wildcards_added:
        lines.append(f"\n🌐 <b>Yeni wildcard-lar (+{len(wildcards_added)}):</b>")
        for w in wildcards_added:
            lines.append(f"  + {w}")

    if domains_removed:
        lines.append(f"\n🗑 Silindi: {len(domains_removed)} domen")

    if h1_removed:
        lines.append(f"❌ H1 silindi: {', '.join(h1_removed)}")
    if bc_removed:
        lines.append(f"❌ BC silindi: {', '.join(bc_removed)}")

    return "\n".join(lines)


def main():
    state = load_state()
    last_sha = state.get("last_sha")

    commits_resp = fetch(f"https://api.github.com/repos/{REPO}/commits?per_page=1")
    current_sha = commits_resp[0]["sha"]

    print(f"last_sha   : {last_sha}")
    print(f"current_sha: {current_sha}")

    if last_sha is None:
        print("İlk run — baseline SHA saxlanır, bildiriş göndərilmir.")
        save_state({"last_sha": current_sha})
        return

    if last_sha == current_sha:
        print("Dəyişiklik yoxdur.")
        return

    compare = fetch(
        f"https://api.github.com/repos/{REPO}/compare/{last_sha}...{current_sha}"
    )

    commits = compare.get("commits", [])
    files = {f["filename"]: f for f in compare.get("files", [])}

    domains_added, domains_removed = [], []
    wildcards_added, wildcards_removed = [], []
    h1_new, h1_removed, h1_modified, h1_stats = [], [], [], (0, 0)
    bc_new, bc_removed, bc_modified, bc_stats = [], [], [], (0, 0)

    if "data/domains.txt" in files:
        domains_added, domains_removed = parse_patch_lines(
            files["data/domains.txt"].get("patch", "")
        )

    if "data/wildcards.txt" in files:
        wildcards_added, wildcards_removed = parse_patch_lines(
            files["data/wildcards.txt"].get("patch", "")
        )

    if "data/hackerone_data.json" in files:
        f = files["data/hackerone_data.json"]
        h1_new, h1_removed, h1_modified = parse_program_names(f.get("patch", ""))
        h1_stats = (f.get("additions", 0), f.get("deletions", 0))

    if "data/bugcrowd_data.json" in files:
        f = files["data/bugcrowd_data.json"]
        bc_new, bc_removed, bc_modified = parse_program_names(f.get("patch", ""))
        bc_stats = (f.get("additions", 0), f.get("deletions", 0))

    def date(c):
        return c["commit"]["committer"]["date"][:16].replace("T", " ")

    first_date = date(commits[0]) if commits else "?"
    last_date = date(commits[-1]) if commits else "?"
    subject = f"🎯 BB Scope Report — {len(commits)} commit ({first_date} → {last_date} UTC)"

    # ── Email ──────────────────────────────────────────────────────────────
    L = []
    L.append(f"Son yoxlamadan bəri {len(commits)} commit edildi.\n")

    L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    L.append(" COMMIT TARİXÇƏSİ")
    L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for c in commits:
        L.append(f"  {c['sha'][:7]} — {date(c)} UTC")

    if domains_added or domains_removed:
        L.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        L.append(" DOMEN DƏYİŞİKLİKLƏRİ (domains.txt)")
        L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        if domains_added:
            L.append(f"YENİ (+{len(domains_added)}):")
            for d in domains_added:
                L.append(f"  + {d}")
        if domains_removed:
            L.append(f"\nSİLİNDİ (-{len(domains_removed)}):")
            for d in domains_removed:
                L.append(f"  - {d}")

    if wildcards_added or wildcards_removed:
        L.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        L.append(" WİLDCARD DƏYİŞİKLİKLƏRİ (wildcards.txt)")
        L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        if wildcards_added:
            L.append(f"YENİ (+{len(wildcards_added)}):")
            for w in wildcards_added:
                L.append(f"  + {w}")
        if wildcards_removed:
            L.append(f"\nSİLİNDİ (-{len(wildcards_removed)}):")
            for w in wildcards_removed:
                L.append(f"  - {w}")

    if h1_stats[0] or h1_stats[1]:
        L.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        L.append(" HACKERONE PROQRAM DƏYİŞİKLİKLƏRİ")
        L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        if h1_new:
            L.append(f"🆕 YENİ PROQRAM(LAR):")
            for name in h1_new:
                L.append(f"  ★ {name}")
        if h1_removed:
            L.append(f"SİLİNDİ: {', '.join(h1_removed)}")
        if h1_modified:
            L.append(f"DƏYİŞİKLİK: {', '.join(h1_modified)}")
        L.append(f"Fayl dəyişikliyi: +{h1_stats[0]} / -{h1_stats[1]} sətir")

    if bc_stats[0] or bc_stats[1]:
        L.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        L.append(" BUGCROWD PROQRAM DƏYİŞİKLİKLƏRİ")
        L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        if bc_new:
            L.append(f"🆕 YENİ PROQRAM(LAR):")
            for name in bc_new:
                L.append(f"  ★ {name}")
        if bc_removed:
            L.append(f"SİLİNDİ: {', '.join(bc_removed)}")
        if bc_modified:
            L.append(f"DƏYİŞİKLİK: {', '.join(bc_modified)}")
        L.append(f"Fayl dəyişikliyi: +{bc_stats[0]} / -{bc_stats[1]} sətir")

    L.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    L.append("Mənbə: https://github.com/arkadiyt/bounty-targets-data")
    L.append(f"Commits: {last_sha[:7]}...{current_sha[:7]}")

    send_email(subject, "\n".join(L))

    # ── Telegram ───────────────────────────────────────────────────────────
    tg_text = build_telegram_message(
        commits, domains_added, domains_removed,
        wildcards_added, h1_new, h1_removed,
        bc_new, bc_removed, first_date, last_date
    )
    send_telegram(tg_text)

    save_state({"last_sha": current_sha})


if __name__ == "__main__":
    main()
