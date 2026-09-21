import json
import os
import re
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


def fetch_raw_text(path, sha):
    """raw.githubusercontent.com-dan fayl məzmununu yüklə (patch hədd aşdıqda)."""
    url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "bounty-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def diff_platform_scopes(old_text, new_text, scope_field):
    """
    İki JSON mətni arasında scope dəyişikliklərini tap.
    GitHub patch truncate olduqda fallback kimi istifadə olunur.
    """
    name_re = re.compile(r'"name"\s*:\s*"([^"]+)"')
    scope_re = re.compile(rf'"{re.escape(scope_field)}"\s*:\s*"([^"]*)"')

    def extract(text):
        result = {}
        cur = None
        for line in text.split("\n"):
            nm = name_re.search(line)
            if nm:
                cur = nm.group(1).strip()
                result.setdefault(cur, set())
            sm = scope_re.search(line)
            if sm and cur:
                v = sm.group(1).strip()
                if v:
                    result[cur].add(v)
        return result

    old_map = extract(old_text)
    new_map = extract(new_text)
    old_names = set(old_map)
    new_names = set(new_map)

    new_progs, removed_progs, scope_changes = {}, {}, {}

    for n in new_names - old_names:
        scopes = sorted(s for s in new_map[n] if is_web_scope(s))
        if scopes:
            new_progs[n] = {"added": scopes, "removed": []}

    for n in old_names - new_names:
        scopes = sorted(s for s in old_map[n] if is_web_scope(s))
        if scopes:
            removed_progs[n] = {"added": [], "removed": scopes}

    for n in old_names & new_names:
        added = sorted(s for s in new_map[n] - old_map[n] if is_web_scope(s))
        removed = sorted(s for s in old_map[n] - new_map[n] if is_web_scope(s))
        if added or removed:
            scope_changes[n] = {"added": added, "removed": removed}

    return new_progs, removed_progs, scope_changes


def parse_patch_lines(patch):
    """domains.txt / wildcards.txt üçün — sadə sətir əlavə/silmə."""
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


_REVERSED_TLDS = {
    "com", "org", "io", "net", "gov", "edu",
    "de", "fr", "uk", "jp", "cn", "ru", "br", "in", "us",
}

_NON_WEB_URL_PATTERNS = (
    "play.google.com/store",
    "itunes.apple.com/",
    "apps.apple.com/",
    "github.com/",
    "gitlab.com/",
)

_MOBILE_SEGMENTS = {"android", "ios"}


def is_web_scope(val):
    """Yalnız web-ə aid scope-ları saxla (Android/iOS/IoT/repo-ları süzgəcdən keçir)."""
    if not val:
        return False
    # Boşluqlu dəyərlər domen deyil (text description)
    if " " in val:
        return False
    if val.startswith(("http://", "https://", "*.")):
        low = val.lower()
        if any(pat in low for pat in _NON_WEB_URL_PATTERNS):
            return False
        return True
    if "." not in val:
        return False
    parts = val.split(".")
    # Reversed TLD paket: com.example, com.example.app, org.sdk kimi (2+ hissə)
    if (len(parts) >= 2
            and parts[0].lower() in _REVERSED_TLDS
            and "/" not in val
            and ":" not in val):
        return False
    # Ölkə-reversed paket: nz.co.company.android.*, au.com.product.ios.* kimi
    if (len(parts) >= 4
            and any(p.lower() in _MOBILE_SEGMENTS for p in parts)
            and "/" not in val
            and ":" not in val):
        return False
    return True


def parse_platform_patch(patch, scope_field):
    """
    Platform JSON patch-ini parse et.
    scope_field: HackerOne→"asset_identifier", Intigriti→"endpoint",
                 Bugcrowd/YesWeHack→"target"

    Qaytarır:
      new_programs    : {name: [scope, ...]}   — yalnız + sətirlərdə olan
      removed_programs: {name: [scope, ...]}   — yalnız - sətirlərdə olan
      scope_changes   : {name: {'added': [...], 'removed': [...]}}
                        — mövcud proqramlarda scope dəyişikliyi
    """
    if not patch:
        return {}, {}, {}

    name_re = re.compile(r'"name"\s*:\s*"([^"]+)"')
    scope_re = re.compile(rf'"{re.escape(scope_field)}"\s*:\s*"([^"]*)"')

    current_name = None
    name_added = set()
    name_removed = set()
    data = {}  # name -> {'added': [], 'removed': []}

    for line in patch.split("\n"):
        if not line:
            continue
        prefix = line[0]   # '+', '-', ' ', '@', '\\'
        content = line[1:]

        # Proqram adını izlə (istənilən prefix-dən)
        nm = name_re.search(content)
        if nm:
            current_name = nm.group(1).strip()
            data.setdefault(current_name, {"added": [], "removed": []})
            if prefix == "+":
                name_added.add(current_name)
            elif prefix == "-":
                name_removed.add(current_name)

        # Scope dəyişikliyini izlə (yalnız + və - sətirlərdə, web-only)
        if prefix in ("+", "-") and current_name:
            sm = scope_re.search(content)
            if sm:
                val = sm.group(1).strip()
                if val and is_web_scope(val):
                    key = "added" if prefix == "+" else "removed"
                    data[current_name][key].append(val)

    # Yalnız web scope-u olan proqramları göstər (Android/iOS-only proqramları gizlət)
    truly_new = {n: data[n] for n in (name_added - name_removed)
                 if n in data and data[n]["added"]}
    truly_removed = {n: data[n] for n in (name_removed - name_added) if n in data}
    scope_changes = {
        n: data[n]
        for n in data
        if n not in (name_added - name_removed)
        and n not in (name_removed - name_added)
        and (data[n]["added"] or data[n]["removed"])
    }
    return truly_new, truly_removed, scope_changes


def _format_platform_section(title, new_progs, removed_progs, scope_changes, stats):
    """Email üçün bir platforma bölməsi."""
    if not stats[0] and not stats[1]:
        return []
    L = []
    L.append(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    L.append(f" {title}")
    L.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if new_progs:
        L.append("🆕 YENİ PROQRAM(LAR):")
        for name, info in new_progs.items():
            scopes = info.get("added", [])
            L.append(f"  ★ {name}" + (f" ({len(scopes)} scope)" if scopes else ""))
            for s in scopes:
                L.append(f"      + {s}")

    if scope_changes:
        L.append("📋 MÖVCUD PROQRAMLARDA SCOPE DƏYİŞİKLİYİ:")
        for name, info in scope_changes.items():
            added = info.get("added", [])
            removed = info.get("removed", [])
            if not added and not removed:
                continue
            L.append(f"  [{name}]")
            for s in added:
                L.append(f"      + {s}")
            for s in removed:
                L.append(f"      - {s}")

    if removed_progs:
        L.append("❌ SİLİNƏN PROQRAM(LAR):")
        for name, info in removed_progs.items():
            L.append(f"  ✗ {name}")

    if not new_progs and not scope_changes and not removed_progs:
        L.append(f"  (metadata yeniləməsi — yeni scope yoxdur)")

    L.append(f"Fayl dəyişikliyi: +{stats[0]} / -{stats[1]} sətir")
    return L


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
    # Telegram 4096 simvol limiti
    if len(text) > 4000:
        text = text[:3990] + "\n...(kəsildi)"
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
                            wildcards_added, wildcards_removed,
                            platforms, first_date, last_date):
    """
    platforms: list of (label, new_progs, removed_progs, scope_changes)
    """
    lines = ["🎯 <b>BB Scope Report</b>"]
    lines.append(f"📅 {first_date} → {last_date} UTC  |  {len(commits)} commit\n")

    for label, new_progs, removed_progs, scope_changes in platforms:
        for name, info in new_progs.items():
            scopes = info.get("added", [])
            lines.append(f"🆕 <b>YENİ ({label}):</b> {name}")
            for s in scopes[:5]:
                lines.append(f"  + {s}")
            if len(scopes) > 5:
                lines.append(f"  ... +{len(scopes)-5} daha scope")
        for name, info in scope_changes.items():
            added = info.get("added", [])
            if added:
                lines.append(f"📋 <b>{label} — {name}:</b>")
                for s in added[:5]:
                    lines.append(f"  + {s}")
                if len(added) > 5:
                    lines.append(f"  ... +{len(added)-5} daha")
        for name in removed_progs:
            lines.append(f"❌ <b>({label}) silindi:</b> {name}")

    if domains_added:
        lines.append(f"\n✅ <b>Yeni domenler (+{len(domains_added)}):</b>")
        for d in domains_added[:10]:
            lines.append(f"  + {d}")
        if len(domains_added) > 10:
            lines.append(f"  ... +{len(domains_added)-10} daha")

    if wildcards_added:
        lines.append(f"\n🌐 <b>Yeni wildcard-lar (+{len(wildcards_added)}):</b>")
        for w in wildcards_added[:10]:
            lines.append(f"  + {w}")
        if len(wildcards_added) > 10:
            lines.append(f"  ... +{len(wildcards_added)-10} daha")

    if domains_removed:
        lines.append(f"\n🗑 Silindi: {len(domains_removed)} domen")
    if wildcards_removed:
        lines.append(f"🗑 Silindi: {len(wildcards_removed)} wildcard")

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

    # ── domains.txt / wildcards.txt ────────────────────────────────────────
    domains_added, domains_removed = [], []
    wildcards_added, wildcards_removed = [], []

    if "data/domains.txt" in files:
        domains_added, domains_removed = parse_patch_lines(
            files["data/domains.txt"].get("patch", "")
        )
    if "data/wildcards.txt" in files:
        wildcards_added, wildcards_removed = parse_patch_lines(
            files["data/wildcards.txt"].get("patch", "")
        )

    # ── Platform JSON faylları ─────────────────────────────────────────────
    h1_new, h1_removed, h1_scope, h1_stats = {}, {}, {}, (0, 0)
    bc_new, bc_removed, bc_scope, bc_stats = {}, {}, {}, (0, 0)
    ig_new, ig_removed, ig_scope, ig_stats = {}, {}, {}, (0, 0)
    ywh_new, ywh_removed, ywh_scope, ywh_stats = {}, {}, {}, (0, 0)

    def process_platform(filename, scope_field):
        """
        Platform faylını parse et.
        Patch varsa parse_platform_patch(), yoxdursa raw fetch + diff_platform_scopes().
        """
        if filename not in files:
            return {}, {}, {}, (0, 0)
        f = files[filename]
        stats = (f.get("additions", 0), f.get("deletions", 0))
        patch = f.get("patch", "")
        if patch:
            n, r, s = parse_platform_patch(patch, scope_field)
        else:
            # GitHub API patch-i truncate etdi — raw faylları çək və diff et
            print(f"{filename}: patch yoxdur, raw diff istifadə olunur...")
            try:
                old_text = fetch_raw_text(filename, last_sha)
                new_text = fetch_raw_text(filename, current_sha)
                n, r, s = diff_platform_scopes(old_text, new_text, scope_field)
            except Exception as e:
                print(f"{filename} raw diff xətası: {e}")
                n, r, s = {}, {}, {}
        return n, r, s, stats

    h1_new,  h1_removed,  h1_scope,  h1_stats  = process_platform("data/hackerone_data.json",  "asset_identifier")
    bc_new,  bc_removed,  bc_scope,  bc_stats  = process_platform("data/bugcrowd_data.json",    "target")
    ig_new,  ig_removed,  ig_scope,  ig_stats  = process_platform("data/intigriti_data.json",   "endpoint")
    ywh_new, ywh_removed, ywh_scope, ywh_stats = process_platform("data/yeswehack_data.json",   "target")

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

    L += _format_platform_section("HACKERONE", h1_new, h1_removed, h1_scope, h1_stats)
    L += _format_platform_section("BUGCROWD", bc_new, bc_removed, bc_scope, bc_stats)
    L += _format_platform_section("INTIGRITI", ig_new, ig_removed, ig_scope, ig_stats)
    L += _format_platform_section("YESWEHACK", ywh_new, ywh_removed, ywh_scope, ywh_stats)

    L.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    L.append("Mənbə: https://github.com/arkadiyt/bounty-targets-data")
    L.append(f"Commits: {last_sha[:7]}...{current_sha[:7]}")

    send_email(subject, "\n".join(L))

    # ── Telegram ───────────────────────────────────────────────────────────
    platforms = [
        ("H1", h1_new, h1_removed, h1_scope),
        ("BC", bc_new, bc_removed, bc_scope),
        ("Intigriti", ig_new, ig_removed, ig_scope),
        ("YWH", ywh_new, ywh_removed, ywh_scope),
    ]
    tg_text = build_telegram_message(
        commits, domains_added, domains_removed,
        wildcards_added, wildcards_removed,
        platforms, first_date, last_date
    )
    send_telegram(tg_text)

    save_state({"last_sha": current_sha})


if __name__ == "__main__":
    main()
