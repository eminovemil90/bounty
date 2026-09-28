"""
Nuclei nəticələrini oxuyub Telegram + Gmail-ə göndər.
Cron ilə hər 30 dəqiqədə bir işlədilir.
"""
from dotenv import load_dotenv
load_dotenv("/opt/bounty/.env")

import os
import sys
from scope_monitor import send_telegram, send_email

RESULTS_FILE = "/opt/bounty/nuclei_results.txt"
SENT_FILE = "/opt/bounty/nuclei_sent.txt"

if not os.path.exists(RESULTS_FILE):
    print("Nuclei results faylı yoxdur.")
    sys.exit(0)

# Əvvəl göndərilmiş sətirləri oxu
sent = set()
if os.path.exists(SENT_FILE):
    with open(SENT_FILE) as f:
        sent = set(f.read().strip().split("\n"))

# Yeni tapıntıları tap
with open(RESULTS_FILE) as f:
    all_findings = [l.strip() for l in f if l.strip()]

new_findings = [f for f in all_findings if f not in sent]

if not new_findings:
    print("Yeni nuclei tapıntısı yoxdur.")
    sys.exit(0)

print(f"{len(new_findings)} yeni tapıntı tapıldı.")

# Telegram-a göndər
tg_text = f"🔍 <b>Nuclei Nəticələri ({len(new_findings)} tapıntı)</b>\n\n" + "\n".join(new_findings[:30])
if len(new_findings) > 30:
    tg_text += f"\n\n... +{len(new_findings)-30} daha"
send_telegram(tg_text)

# Gmail-ə göndər
email_body = f"Nuclei {len(new_findings)} yeni tapıntı tapdı:\n\n" + "\n".join(new_findings)
send_email("🔍 Nuclei Nəticələri", email_body)

# Göndərilənləri qeyd et
with open(SENT_FILE, "a") as f:
    for finding in new_findings:
        f.write(finding + "\n")

print("Nəticələr göndərildi.")
