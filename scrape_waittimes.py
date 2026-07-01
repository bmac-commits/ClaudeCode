#!/usr/bin/env python3
"""
Scrapes Yosemite entrance wait times from yosemite.live.
Runs locally (residential IP bypasses Cloudflare).
Writes wait_times.json, then commits + pushes to GitHub.
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from curl_cffi import requests

REPO_DIR = Path(__file__).parent
OUTPUT_FILE = REPO_DIR / "wait_times.json"
URL = "https://yosemite.live"

ENTRANCE_MAP = {
    "arch rock":      "hwy140",
    "big oak flat":   "hwy120",
    "south entrance": "hwy41",
}


def scrape():
    session = requests.Session(impersonate="chrome124")
    r = session.get(URL, timeout=20)
    r.raise_for_status()
    html = r.text

    result = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "error": None,
        "entrances": {
            "hwy140": {"wait": None, "status": "unknown", "trend": None},
            "hwy120": {"wait": None, "status": "unknown", "trend": None},
            "hwy41":  {"wait": None, "status": "unknown", "trend": None},
        }
    }

    cards = re.findall(
        r'class="entrance-card"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )

    for card in cards:
        name_m = re.search(r'class="entrance-name"[^>]*>([^<]+)<', card)
        if not name_m:
            continue
        name = name_m.group(1).strip().lower()

        key = None
        for pattern, hwy_key in ENTRANCE_MAP.items():
            if pattern in name:
                key = hwy_key
                break
        if not key:
            continue

        wait_m = re.search(r'class="wait-value"[^>]*>\s*([<\d]+)\s*<', card)
        wait_unit_m = re.search(r'class="wait-unit"[^>]*>([^<]+)<', card)
        trend_m = re.search(r'class="trend-label"[^>]*>([^<]+)<', card)

        wait_min = None
        if wait_m:
            raw = wait_m.group(1).strip()
            if raw == "<1":
                wait_min = 0
            else:
                try:
                    wait_min = int(raw)
                except ValueError:
                    pass

        unit = wait_unit_m.group(1).strip().lower() if wait_unit_m else "min"
        trend = trend_m.group(1).strip() if trend_m else None

        if "hour" in unit and wait_min is not None:
            wait_min *= 60

        status = "open" if wait_min is not None else "unknown"
        result["entrances"][key] = {
            "wait": wait_min,
            "status": status,
            "trend": trend,
        }

    return result


def git_push(result):
    OUTPUT_FILE.write_text(json.dumps(result, indent=2) + "\n")
    subprocess.run(["git", "-C", str(REPO_DIR), "pull", "--rebase", "--quiet"], check=True)
    subprocess.run(["git", "-C", str(REPO_DIR), "add", "wait_times.json"], check=True)
    diff = subprocess.run(
        ["git", "-C", str(REPO_DIR), "diff", "--cached", "--quiet"]
    )
    if diff.returncode == 0:
        print("[git] No changes, skipping commit")
        return
    subprocess.run(
        ["git", "-C", str(REPO_DIR), "commit", "-m", "chore: update wait times [skip ci]"],
        check=True
    )
    subprocess.run(["git", "-C", str(REPO_DIR), "push"], check=True)
    print("[git] Pushed wait_times.json")


def main():
    try:
        result = scrape()
        print(json.dumps(result, indent=2))
        git_push(result)
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        result = {
            "updated": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
            "entrances": {
                "hwy140": {"wait": None, "status": "unknown", "trend": None},
                "hwy120": {"wait": None, "status": "unknown", "trend": None},
                "hwy41":  {"wait": None, "status": "unknown", "trend": None},
            }
        }
        git_push(result)
        sys.exit(1)


if __name__ == "__main__":
    main()
