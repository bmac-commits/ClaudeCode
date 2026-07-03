#!/usr/bin/env python3
"""
Scrapes Yosemite entrance wait times from yosemite.live.
Runs locally (residential IP bypasses Cloudflare).
Writes wait_times.json, then commits + pushes to GitHub.
"""
import html as html_lib
import json
import re
import subprocess
import sys
import time
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


def scrape_once():
    session = requests.Session(impersonate="chrome124")
    r = session.get(URL, timeout=20)
    r.raise_for_status()
    # The site HTML-escapes "<1" as "&lt;1" for quiet entrances (the most
    # common state) — unescape before parsing or every sub-minute reading
    # silently fails to match and gets reported as unavailable.
    html = html_lib.unescape(r.text)

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


def all_unknown(result):
    return all(e["wait"] is None for e in result["entrances"].values())


def scrape():
    # Redundancy layer 1: a single fetch can land on a half-rendered page or
    # transient site hiccup. Retry once before treating it as a real failure.
    result = scrape_once()
    if all_unknown(result):
        time.sleep(5)
        retry = scrape_once()
        if not all_unknown(retry):
            return retry
    return result


def git_push(result):
    payload = json.dumps(result, indent=2) + "\n"
    OUTPUT_FILE.write_text(payload)
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
    # Commit our own change first, then merge (not rebase) remote history in.
    # Rebase requires the *entire* working tree to be clean, so it collides
    # with unrelated in-progress edits elsewhere in this repo (e.g. an active
    # Claude Code session). Merge only cares about the paths it touches, so
    # committing wait_times.json first and merging keeps this resilient to
    # unrelated uncommitted files sitting around.
    subprocess.run(
        ["git", "-C", str(REPO_DIR), "pull", "--no-rebase", "--no-edit", "-X", "ours", "--quiet"],
        check=True
    )
    subprocess.run(["git", "-C", str(REPO_DIR), "push"], check=True)
    print("[git] Pushed wait_times.json")


def main():
    try:
        result = scrape()
        print(json.dumps(result, indent=2))

        # Redundancy layer 2: if this run found nothing usable but the last
        # published data did, keep serving the last-known-good values instead
        # of overwriting them with a wall of nulls. The dashboard's own
        # staleness warning (⚠ updated Xh ago) already handles telling
        # visitors the data is aging, which is far more useful than showing
        # "unavailable" for what's usually a transient/one-off scrape miss.
        if all_unknown(result) and OUTPUT_FILE.exists():
            try:
                previous = json.loads(OUTPUT_FILE.read_text())
                if not all_unknown(previous):
                    print("[digest] New scrape had no data; keeping last-known-good wait_times.json")
                    return
            except (json.JSONDecodeError, KeyError):
                pass

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
        if OUTPUT_FILE.exists():
            try:
                previous = json.loads(OUTPUT_FILE.read_text())
                if not all_unknown(previous):
                    print("[digest] Scrape errored; keeping last-known-good wait_times.json")
                    sys.exit(1)
            except (json.JSONDecodeError, KeyError):
                pass
        git_push(result)
        sys.exit(1)


if __name__ == "__main__":
    main()
