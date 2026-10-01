from dotenv import load_dotenv
load_dotenv("/opt/bounty/.env")

import glob
import html
import ipaddress
import json
import os
import re
import smtplib
import subprocess
import time
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urlparse

REPO = "arkadiyt/bounty-targets-data"
STATE_FILE = "state.json"
GMAIL_USER = "eminovemil90@gmail.com"
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


def _make_headers():
    h = {"User-Agent": "bounty-monitor/1.0"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"token {GITHUB_TOKEN}"
    return h


def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_make_headers())
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"fetch xətası ({url[:60]}): {e} — {wait}s sonra yenidən...")
            time.sleep(wait)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"state.json oxuna bilmədi: {e} — sıfırdan başlanır")
    return {"last_sha": None}


def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_raw_text(path, sha, retries=3):
    url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{path}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_make_headers())
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8")
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"fetch_raw_text xətası ({path}): {e} — {wait}s sonra yenidən...")
            time.sleep(wait)


def diff_platform_scopes(old_text, new_text, scope_field):
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


def diff_plain_text(old_text, new_text):
    old_lines = {l.strip() for l in old_text.split("\n") if l.strip()}
    new_lines = {l.strip() for l in new_text.split("\n") if l.strip()}
    return sorted(new_lines - old_lines), sorted(old_lines - new_lines)


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


def _is_non_public_ip(val):
    host = val.split(":")[0].strip("[]")
    try:
        addr = ipaddress.ip_address(host)
        return not addr.is_global
    except ValueError:
        pass
    try:
        net = ipaddress.ip_network(val, strict=False)
        return not net.is_global
    except ValueError:
        pass
    return False


def is_web_scope(val):
    if not val:
        return False
    if " " in val:
        return False
    if val.startswith(("http://", "https://", "*.")):
        low = val.lower()
        if any(pat in low for pat in _NON_WEB_URL_PATTERNS):
            return False
        return True
    if "." not in val:
        return False
    if _is_non_public_ip(val):
        return False
    parts = val.split(".")
    if (len(parts) >= 2
            and parts[0].lower() in _REVERSED_TLDS
            and "/" not in val
            and ":" not in val):
        return False
    if (len(parts) >= 4
            and any(p.lower() in _MOBILE_SEGMENTS for p in parts)
            and "/" not in val
            and ":" not in val):
        return False
    return True


def parse_platform_patch(patch, scope_field):
    if not patch:
        return {}, {}, {}

    name_re = re.compile(r'"name"\s*:\s*"([^"]+)"')
    scope_re = re.compile(rf'"{re.escape(scope_field)}"\s*:\s*"([^"]*)"')

    current_name = None
    name_added = set()
    name_removed = set()
    data = {}

    for line in patch.split("\n"):
        if not line:
            continue
        prefix = line[0]
        content = line[1:]

        nm = name_re.search(content)
        if nm:
            current_name = nm.group(1).strip()
            data.setdefault(current_name, {"added": set(), "removed": set()})
            if prefix == "+":
                name_added.add(current_name)
            elif prefix == "-":
                name_removed.add(current_name)

        if prefix in ("+", "-") and current_name:
            sm = scope_re.search(content)
            if sm:
                val = sm.group(1).strip()
                if val and is_web_scope(val):
                    key = "added" if prefix == "+" else "removed"
                    data[current_name][key].add(val)

    def _to_lists(d):
        return {"added": sorted(d["added"]), "removed": sorted(d["removed"])}

    truly_new = {n: _to_lists(data[n]) for n in (name_added - name_removed)
                 if n in data and data[n]["added"]}
    truly_removed = {n: _to_lists(data[n]) for n in (name_removed - name_added) if n in data}
    scope_changes = {
        n: _to_lists(data[n])
        for n in data
        if n not in (name_added - name_removed)
        and n not in (name_removed - name_added)
        and (data[n]["added"] or data[n]["removed"])
    }
    return truly_new, truly_removed, scope_changes


def _format_platform_section(title, new_progs, removed_progs, scope_changes, stats):
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
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for i, chunk in enumerate(chunks):
        if len(chunks) > 1:
            chunk = f"({i+1}/{len(chunks)})\n" + chunk
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML"
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
            if resp.get("ok"):
                print(f"Telegram bildirişi göndərildi ({i+1}/{len(chunks)})")
            else:
                print(f"Telegram xətası: {resp}")
        except Exception as e:
            print(f"Telegram xətası: {e}")


def build_telegram_message(commits, domains_added, domains_removed,
                            wildcards_added, wildcards_removed,
                            platforms, first_date, last_date):
    lines = ["🎯 <b>BB Scope Report</b>"]
    lines.append(f"📅 {first_date} → {last_date} UTC  |  {len(commits)} commit\n")

    for label, new_progs, removed_progs, scope_changes in platforms:
        for name, info in new_progs.items():
            scopes = info.get("added", [])
            lines.append(f"🆕 <b>YENİ ({label}):</b> {html.escape(name)}")
            for s in scopes[:5]:
                lines.append(f"  + {html.escape(s)}")
            if len(scopes) > 5:
                lines.append(f"  ... +{len(scopes)-5} daha scope")
        for name, info in scope_changes.items():
            added = info.get("added", [])
            if added:
                lines.append(f"📋 <b>{label} — {html.escape(name)}:</b>")
                for s in added[:5]:
                    lines.append(f"  + {html.escape(s)}")
                if len(added) > 5:
                    lines.append(f"  ... +{len(added)-5} daha")
        for name in removed_progs:
            lines.append(f"❌ <b>({label}) silindi:</b> {html.escape(name)}")

    if domains_added:
        lines.append(f"\n✅ <b>Yeni domenler (+{len(domains_added)}):</b>")
        for d in domains_added[:10]:
            lines.append(f"  + {html.escape(d)}")
        if len(domains_added) > 10:
            lines.append(f"  ... +{len(domains_added)-10} daha")

    if wildcards_added:
        lines.append(f"\n🌐 <b>Yeni wildcard-lar (+{len(wildcards_added)}):</b>")
        for w in wildcards_added[:10]:
            lines.append(f"  + {html.escape(w)}")
        if len(wildcards_added) > 10:
            lines.append(f"  ... +{len(wildcards_added)-10} daha")

    if domains_removed:
        lines.append(f"\n🗑 Silindi: {len(domains_removed)} domen")
    if wildcards_removed:
        lines.append(f"🗑 Silindi: {len(wildcards_removed)} wildcard")

    return "\n".join(lines)


def get_bbp_names(filename, sha):
    """Platform JSON-undan BBP proqram adlarını qaytarır. Xəta halında None (fail-open)."""
    try:
        text = fetch_raw_text(filename, sha)
        data = json.loads(text)
        bbp = set()
        for prog in data:
            name = prog.get("name", "")
            if not name:
                continue
            if "offers_bounties" in prog:
                if prog["offers_bounties"] is True:
                    bbp.add(name)
            elif "max_payout" in prog:
                mp = prog.get("max_payout")
                if mp not in (None, 0, "0", "", "null"):
                    bbp.add(name)
            else:
                if "vdp" not in name.lower():
                    bbp.add(name)
        print(f"BBP filtr ({filename}): {len(bbp)} BBP proqram tapıldı")
        return bbp
    except Exception as e:
        print(f"BBP filtr xətası ({filename}): {e} — hamısı daxil edilir")
        return None


NUCLEI_BIN = "/root/go/bin/nuclei"

# ── Bug Bounty template strategiyası ────────────────────────────────────────
#
# PHASE 1 — "Quick Wins" (sürətli, yüksək tapılma ehtimalı)
#   Bunlar HTTP-əsaslı, az request, tez cavab verən templatedir.
#   Həmişə ödəyir: takeover, exposed secrets, default credentials, panels.
#
_BB_FAST_TAGS = (
    "takeover,"        # subdomain takeover — DNS dangling, unclaimed services
    "exposure,"        # .env, .git, backup fayllar, API keys, credentials
    "default-login,"   # admin panel default şifrələri (admin:admin, vs.)
    "panel,"           # exposed admin/management interfeyslər
    "misconfig,"       # S3 bucket açıq, CORS wildcard, security headers yox
    "token,"           # exposed JWT, OAuth token, API key response-da
    "api"              # API endpoint misconfiguration, unauthenticated API
)
_BB_FAST_EXCLUDE = "dos,fuzz,headless,helpers,tech,info"

# PHASE 2 — "Deep Scan" (dərin, daha ağır, yüksək ödəniş)
#   Bu templateler daha çox request edir amma tapırsa payout yüksəkdir.
#   RCE, SQLi, SSRF, auth bypass — triagerdə prioritet verilir.
#
_BB_DEEP_TAGS = (
    "cve,"             # bütün CVE templateləri (xüsusilə son 2 ildəkilər)
    "rce,"             # Remote Code Execution — ən yüksək ödəniş
    "ssrf,"            # SSRF — cloud metadata, internal network
    "sqli,"            # SQL Injection — data leak, auth bypass
    "ssti,"            # Server-Side Template Injection → RCE-yə chain
    "lfi,"             # Local File Inclusion — /etc/passwd, config fayllar
    "xss,"             # Cross-Site Scripting — stored > reflected
    "auth-bypass,"     # Authentication bypass — JWT, session, MFA
    "redirect,"        # Open Redirect — OAuth token theft üçün chain
    "injection,"       # Command injection, LDAP, XXE, header injection
    "file-upload,"     # Unrestricted file upload → RCE
    "idor"             # Insecure Direct Object Reference
)
_BB_DEEP_EXCLUDE = "dos,fuzz,headless,helpers,tech,info"
# ────────────────────────────────────────────────────────────────────────────


def _launch_nuclei_batches(phase_name, live, batch_prefix,
                            tags, exclude_tags,
                            batch_size, timeout_sec, rate,
                            results_file):
    batches = [live[i:i+batch_size] for i in range(0, len(live), batch_size)]
    print(f"Nuclei {phase_name}: {len(live)} domen → "
          f"{len(batches)} batch ({batch_size}/batch), "
          f"rate={rate}, timeout={timeout_sec}s")
    for i, batch in enumerate(batches):
        batch_file = f"/opt/bounty/{batch_prefix}_{i:03d}.txt"
        with open(batch_file, "w") as f:
            f.write("\n".join(batch) + "\n")
        subprocess.Popen(
            ["timeout", str(timeout_sec),
             NUCLEI_BIN,
             "-l", batch_file,
             "-tags", tags,
             "-severity", "critical,high,medium",
             "-exclude-tags", exclude_tags,
             "-o", results_file,
             "-silent", "-timeout", "10",
             "-retries", "2", "-rate-limit", str(rate)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    return len(batches)


def run_nuclei(all_new_domains):
    if not all_new_domains:
        return

    if not os.path.exists(NUCLEI_BIN):
        print(f"XƏTA: nuclei tapılmadı: {NUCLEI_BIN} — skan atlanır")
        return

    # Artıq işləyən nuclei proseslərini dayandır
    check = subprocess.run(["pgrep", "-f", "nuclei"], capture_output=True)
    if check.returncode == 0:
        print("Köhnə nuclei prosesləri tapıldı, dayandırılır...")
        subprocess.run(["pkill", "-f", "nuclei"], capture_output=True)
        time.sleep(3)

    normalized = set()
    for d in all_new_domains:
        d = d.lstrip("*.")
        if d.startswith(("http://", "https://")):
            d = urlparse(d).netloc or d
        if d:
            normalized.add(d)

    if not normalized:
        return

    all_domains_file = "/opt/bounty/all_domains.txt"
    live_domains_file = "/opt/bounty/live_domains.txt"

    if os.path.exists(live_domains_file):
        os.remove(live_domains_file)

    with open(all_domains_file, "w") as f:
        f.write("\n".join(sorted(normalized)) + "\n")

    print(f"httpx: {len(normalized)} domain yoxlanır...")
    try:
        subprocess.run(
            ["httpx", "-l", all_domains_file, "-silent", "-o", live_domains_file,
             "-timeout", "5", "-threads", "50", "-rate-limit", "100"],
            timeout=300, capture_output=True
        )
    except Exception as e:
        print(f"httpx xətası: {e} — nuclei atlanır (dead domainlərə skan edilmir).")
        return

    if not os.path.exists(live_domains_file):
        print("httpx: heç bir canlı domen tapılmadı, nuclei atlanır.")
        return

    with open(live_domains_file) as f:
        live = [l.strip() for l in f if l.strip()]

    if not live:
        print("httpx: heç bir canlı domen tapılmadı, nuclei atlanır.")
        return

    print(f"httpx: {len(live)}/{len(normalized)} domen canlıdır → nuclei başladılır...")

    results_file = "/opt/bounty/nuclei_results.txt"

    # Köhnə batch fayllarını təmizlə
    for old in glob.glob("/opt/bounty/fast_*.txt") + glob.glob("/opt/bounty/deep_*.txt"):
        os.remove(old)

    # Phase 1: Quick Wins — böyük batch, yüksək rate, 45 dəq timeout
    n1 = _launch_nuclei_batches(
        "PHASE-1(quick)", live, "fast",
        tags=_BB_FAST_TAGS,
        exclude_tags=_BB_FAST_EXCLUDE,
        batch_size=100, timeout_sec=2700, rate=50,
        results_file=results_file,
    )

    # Phase 2: Deep Scan — kiçik batch, aşağı rate, 2 saatlıq timeout
    n2 = _launch_nuclei_batches(
        "PHASE-2(deep)", live, "deep",
        tags=_BB_DEEP_TAGS,
        exclude_tags=_BB_DEEP_EXCLUDE,
        batch_size=30, timeout_sec=7200, rate=25,
        results_file=results_file,
    )

    print(f"Nuclei başladıldı: {n1} fast batch + {n2} deep batch → {results_file}")


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

    compare_status = compare.get("status", "")
    if compare_status not in ("ahead", "identical", ""):
        print(f"XƏBƏRDARLIQ: GitHub compare status='{compare_status}' — "
              f"çox sayda commit olduqda fayl siyahısı natamam ola bilər")

    commits = compare.get("commits", [])
    files = {f["filename"]: f for f in compare.get("files", [])}

    domains_added, domains_removed = [], []
    wildcards_added, wildcards_removed = [], []

    if "data/domains.txt" in files:
        patch = files["data/domains.txt"].get("patch", "")
        if patch:
            domains_added, domains_removed = parse_patch_lines(patch)
        else:
            print("data/domains.txt: patch yoxdur, raw diff istifadə olunur...")
            try:
                domains_added, domains_removed = diff_plain_text(
                    fetch_raw_text("data/domains.txt", last_sha),
                    fetch_raw_text("data/domains.txt", current_sha),
                )
            except Exception as e:
                print(f"data/domains.txt raw diff xətası: {e}")

    if "data/wildcards.txt" in files:
        patch = files["data/wildcards.txt"].get("patch", "")
        if patch:
            wildcards_added, wildcards_removed = parse_patch_lines(patch)
        else:
            print("data/wildcards.txt: patch yoxdur, raw diff istifadə olunur...")
            try:
                wildcards_added, wildcards_removed = diff_plain_text(
                    fetch_raw_text("data/wildcards.txt", last_sha),
                    fetch_raw_text("data/wildcards.txt", current_sha),
                )
            except Exception as e:
                print(f"data/wildcards.txt raw diff xətası: {e}")

    h1_new, h1_removed, h1_scope, h1_stats = {}, {}, {}, (0, 0)
    bc_new, bc_removed, bc_scope, bc_stats = {}, {}, {}, (0, 0)
    ig_new, ig_removed, ig_scope, ig_stats = {}, {}, {}, (0, 0)
    ywh_new, ywh_removed, ywh_scope, ywh_stats = {}, {}, {}, (0, 0)

    def process_platform(filename, scope_field):
        if filename not in files:
            return {}, {}, {}, (0, 0)
        f = files[filename]
        stats = (f.get("additions", 0), f.get("deletions", 0))
        patch = f.get("patch", "")
        if patch:
            n, r, s = parse_platform_patch(patch, scope_field)
        else:
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

    try:
        send_email(subject, "\n".join(L))
    except Exception as e:
        print(f"Email göndərmə xətası: {e}")

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

    # SHA-nı nuclei-dən ƏVVƏL yenilə (nuclei uğursuz olsa belə növbəti run düzgün işləsin)
    save_state({"last_sha": current_sha})

    # ── Nuclei Scan (yalnız BBP proqramları) ──────────────────────────────
    # Dəyişiklik olmayan platformlar üçün JSON yükləmə (API quota qənaəti)
    h1_bbp  = get_bbp_names("data/hackerone_data.json",  current_sha) if (h1_new  or h1_scope)  else None
    bc_bbp  = get_bbp_names("data/bugcrowd_data.json",   current_sha) if (bc_new  or bc_scope)  else None
    ig_bbp  = get_bbp_names("data/intigriti_data.json",  current_sha) if (ig_new  or ig_scope)  else None
    ywh_bbp = get_bbp_names("data/yeswehack_data.json",  current_sha) if (ywh_new or ywh_scope) else None

    all_new = set()
    all_new.update(domains_added)
    all_new.update(wildcards_added)  # *.target.com → run_nuclei içdə lstrip("*.") olur
    for info, bbp in [(h1_new,  h1_bbp),  (bc_new,  bc_bbp),
                      (ig_new,  ig_bbp),  (ywh_new, ywh_bbp)]:
        for name, pi in info.items():
            if bbp is None or name in bbp:
                all_new.update(pi.get("added", []))
    for info, bbp in [(h1_scope,  h1_bbp),  (bc_scope,  bc_bbp),
                      (ig_scope,  ig_bbp),  (ywh_scope, ywh_bbp)]:
        for name, pi in info.items():
            if bbp is None or name in bbp:
                all_new.update(pi.get("added", []))
    run_nuclei(all_new)


if __name__ == "__main__":
    main()
