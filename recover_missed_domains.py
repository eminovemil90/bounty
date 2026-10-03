"""
16 saat ərzindəki qaçırılmış domenləri GitHub diff-dən bərpa edib nuclei ilə skan edir.
Bir dəfəlik istifadə üçün nəzərdə tutulub.
"""
from dotenv import load_dotenv
load_dotenv("/opt/bounty/.env")

from scope_monitor import (
    fetch, fetch_raw_text,
    parse_patch_lines, diff_plain_text,
    parse_platform_patch, diff_platform_scopes,
    get_bbp_names, run_nuclei,
)

REPO = "arkadiyt/bounty-targets-data"

# Log-dan götürülmüş: httpx xətası səbəbiylə nuclei işləməyən SHA cütləri
MISSED_PAIRS = [
    ("cdef4b7a402ec6e6c69836b39bf84aaa2e214352", "ace9344e29a40fbd3897a3131175c21b57029183"),  # 112 domain
    ("ace9344e29a40fbd3897a3131175c21b57029183", "115217c2cc5d87c6b23a1b43e312d17a05b997e2"),  # 110 domain
    ("115217c2cc5d87c6b23a1b43e312d17a05b997e2", "bd2ca1876fe9cd2b6f41b98fe9308226d1d1a17b"),  # 70 domain
    ("bd2ca1876fe9cd2b6f41b98fe9308226d1d1a17b", "bf7db681960ddf41435ae78b5f940eacc34d5217"),  # 124 domain
    ("bf7db681960ddf41435ae78b5f940eacc34d5217", "0b1ef51c82e62475ca774930ec52c9f43e7e9ffe"),  # 2 domain
]

PLATFORMS = [
    ("data/hackerone_data.json",  "asset_identifier"),
    ("data/bugcrowd_data.json",   "target"),
    ("data/intigriti_data.json",  "endpoint"),
    ("data/yeswehack_data.json",  "target"),
]

all_domains = set()

for old_sha, new_sha in MISSED_PAIRS:
    print(f"\n── {old_sha[:7]}...{new_sha[:7]} ──")
    try:
        compare = fetch(
            f"https://api.github.com/repos/{REPO}/compare/{old_sha}...{new_sha}"
        )
    except Exception as e:
        print(f"  compare xətası: {e} — atlanır")
        continue

    files = {f["filename"]: f for f in compare.get("files", [])}

    # domains.txt
    if "data/domains.txt" in files:
        patch = files["data/domains.txt"].get("patch", "")
        try:
            if patch:
                added, _ = parse_patch_lines(patch)
            else:
                added, _ = diff_plain_text(
                    fetch_raw_text("data/domains.txt", old_sha),
                    fetch_raw_text("data/domains.txt", new_sha),
                )
            all_domains.update(added)
            print(f"  domains.txt: +{len(added)}")
        except Exception as e:
            print(f"  domains.txt xətası: {e}")

    # wildcards.txt
    if "data/wildcards.txt" in files:
        patch = files["data/wildcards.txt"].get("patch", "")
        try:
            if patch:
                added, _ = parse_patch_lines(patch)
            else:
                added, _ = diff_plain_text(
                    fetch_raw_text("data/wildcards.txt", old_sha),
                    fetch_raw_text("data/wildcards.txt", new_sha),
                )
            all_domains.update(added)
            print(f"  wildcards.txt: +{len(added)}")
        except Exception as e:
            print(f"  wildcards.txt xətası: {e}")

    # Platform JSON-ları (yalnız dəyişmişlər)
    for filename, scope_field in PLATFORMS:
        if filename not in files:
            continue
        f = files[filename]
        patch = f.get("patch", "")
        try:
            if patch:
                new_progs, _, scope_changes = parse_platform_patch(patch, scope_field)
            else:
                old_text = fetch_raw_text(filename, old_sha)
                new_text = fetch_raw_text(filename, new_sha)
                new_progs, _, scope_changes = diff_platform_scopes(
                    old_text, new_text, scope_field
                )
        except Exception as e:
            print(f"  {filename} diff xətası: {e}")
            continue

        bbp = get_bbp_names(filename, new_sha)

        count = 0
        for name, info in {**new_progs, **scope_changes}.items():
            if bbp is None or name in bbp:
                added = info.get("added", [])
                all_domains.update(added)
                count += len(added)
        if count:
            print(f"  {filename}: +{count} BBP domain")

print(f"\n{'━'*40}")
print(f"Cəmi unikal domen: {len(all_domains)}")
print(f"{'━'*40}")

if not all_domains:
    print("Heç bir domen tapılmadı.")
else:
    for d in sorted(all_domains):
        print(f"  {d}")
    print(f"\nNuclei başladılır...")
    run_nuclei(all_domains)
    print("Nuclei background-da işləyir.")
    print("Bitmək üçün: watch 'pgrep -c nuclei 2>/dev/null || echo BITDI'")
    print("Nəticələr: cat /opt/bounty/nuclei_results.txt")
