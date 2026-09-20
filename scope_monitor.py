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
    added, removed = [], []
    if not patch:
        return added, removed
    for line in patch.split("\n"):
        if '"name"' not in line:
            continue
        try:
            name = line.split('"name"')[1].split('"')[2]
            if not name:
                continue
            if line.startswith("+"):
                added.append(name)
            elif line.startswith("-"):
                removed.append(name)
        except Exception:
            pass
    return added, removed


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


def main():
    state = load_state()
    last_sha = state.get("last_sha")

    commits_resp = fetch(f"https://api.github.com/repos/{REPO}/commits?per_page=1")
    current_sha = commits_resp[0]["sha"]

    print(f"last_sha   : {last_sha}")
    print(f"current_sha: {current_sha}")

    if last_sha is None:
        print("İlk run — baseline SHA saxlanır, email göndərilmir.")
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
    h1_added, h1_removed, h1_stats = [], [], (0, 0)
    bc_added, bc_removed, bc_stats = [], [], (0, 0)

    if "data/domains.txt" in files:
        f = files["data/domains.txt"]
        domains_added, domains_removed = parse_patch_lines(f.get("patch", ""))

    if "data/wildcards.txt" in files:
        f = files["data/wildcards.txt"]
        wildcards_added, wildcards_removed = parse_patch_lines(f.get("patch", ""))

    if "data/hackerone_data.json" in files:
        f = files["data/hackerone_data.json"]
        h1_added, h1_removed = parse_program_names(f.get("patch", ""))
        h1_stats = (f.get("additions", 0), f.get("deletions", 0))

    if "data/bugcrowd_data.json" in files:
        f = files["data/bugcrowd_data.json"]
        bc_added, bc_removed = parse_program_names(f.get("patch", ""))
        bc_stats = (f.get("additions", 0), f.get("deletions", 0))

    def date(c):
        return c["commit"]["committer"]["date"][:16].replace("T", " ")

    first_date = date(commits[0]) if commits else "?"
    last_date = date(commits[-1]) if commits else "?"
    subject = f"🎯 BB Scope Report — {len(commits)} commit ({first_date} → {last_date} UTC)"

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
        if h1_added:
            L.append(f"YENİ: {', '.join(h1_added)}")
        if h1_removed:
            L.append(f"SİLİNDİ: {', '.join(h1_removed)}")
        L.append(f"Fayl dəyişikliyi: +{h1_stats[0]} / -{h1_stats[1]} sətir")

    if bc_stats[0] or bc_stats[1]:
        L.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        L.append(" BUGCROWD PROQRAM DƏYİŞİKLİKLƏRİ")
        L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        if bc_added:
            L.append(f"YENİ: {', '.join(bc_added)}")
        if bc_removed:
            L.append(f"SİLİNDİ: {', '.join(bc_removed)}")
        L.append(f"Fayl dəyişikliyi: +{bc_stats[0]} / -{bc_stats[1]} sətir")

    L.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    L.append("Mənbə: https://github.com/arkadiyt/bounty-targets-data")
    L.append(f"Commits: {last_sha[:7]}...{current_sha[:7]}")

    send_email(subject, "\n".join(L))
    save_state({"last_sha": current_sha})


if __name__ == "__main__":
    main()
