#!/usr/bin/env python3
"""
Scrapes Yosemite entrance wait times from yosemite.live.
Uses curl_cffi to impersonate Chrome's TLS fingerprint and bypass Cloudflare.
"""
import json
import re
import sys
from datetime import datetime, timezone
from curl_cffi import requests

OUTPUT_FILE = "wait_times.json"
BASE_URL = "https://yosemite.live"

ENTRANCE_PATTERNS = {
    "hwy140": ["arch rock", "140", "el portal"],
    "hwy120": ["big oak flat", "120", "tioga"],
    "hwy41":  ["south entrance", "41", "wawona"],
}


def empty_result(error=None):
    return {
        "updated": datetime.now(timezone.utc).isoformat(),
        "error": error,
        "entrances": {
            "hwy140": {"wait": None, "status": "unknown"},
            "hwy120": {"wait": None, "status": "unknown"},
            "hwy41":  {"wait": None, "status": "unknown"},
        }
    }


def parse_wait_minutes(text):
    text = str(text).strip().lower()
    if any(w in text for w in ["no wait", "0 min", "no delay"]):
        return 0
    m = re.search(r"(\d+)\s*(?:min|minute|m\b)", text)
    if m:
        return int(m.group(1))
    # Plain number
    m = re.search(r"^\d+$", text)
    if m:
        return int(text)
    return None


def parse_api_json(data):
    """Parse /api/waittimes JSON into our schema."""
    print(f"[api] Raw: {json.dumps(data)[:1500]}", file=sys.stderr)
    result = empty_result()

    # Try list shape: [{entrance, wait, status}, ...]
    items = data if isinstance(data, list) else (
        data.get("entrances") or data.get("data") or data.get("waitTimes") or []
    )

    if not isinstance(items, list) or not items:
        # Try dict shape: {"arch_rock": {"wait": 15}, ...}
        if isinstance(data, dict):
            items = [{"entrance": k, **v} for k, v in data.items() if isinstance(v, dict)]

    for entry in items:
        name = str(entry.get("entrance", entry.get("name", entry.get("location", "")))).lower()
        wait_raw = (
            entry.get("wait") or entry.get("waitTime") or
            entry.get("wait_time") or entry.get("minutes") or
            entry.get("delay")
        )
        status = str(entry.get("status", "open")).lower()

        matched = None
        for key, keywords in ENTRANCE_PATTERNS.items():
            if any(k in name for k in keywords):
                matched = key
                break

        if matched:
            wait_min = None
            if isinstance(wait_raw, (int, float)):
                wait_min = int(wait_raw)
            elif isinstance(wait_raw, str):
                wait_min = parse_wait_minutes(wait_raw)
            result["entrances"][matched] = {"wait": wait_min, "status": status}

    return result


def parse_html_page(html):
    """Extract wait times from the rendered page text."""
    result = empty_result()

    # Strip tags for text analysis
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    print(f"[html] Text snippet: {text[:1000]}", file=sys.stderr)

    lines = re.split(r"[.\n]", text)
    current_entrance = None

    for line in lines:
        line_l = line.lower().strip()
        for key, keywords in ENTRANCE_PATTERNS.items():
            if any(k in line_l for k in keywords):
                current_entrance = key
                break
        wait_min = parse_wait_minutes(line)
        if wait_min is not None and current_entrance:
            prev = result["entrances"][current_entrance].get("wait")
            if prev is None:
                result["entrances"][current_entrance]["wait"] = wait_min
                result["entrances"][current_entrance]["status"] = "open"

    return result


def main():
    session = requests.Session(impersonate="chrome124")

    result = empty_result()

    # 1. Try JSON API endpoint
    try:
        r = session.get(f"{BASE_URL}/api/waittimes", timeout=20)
        print(f"[api] Status: {r.status_code}", file=sys.stderr)
        if r.status_code == 200:
            try:
                data = r.json()
                result = parse_api_json(data)
                print("[main] Got data from API endpoint", file=sys.stderr)
                result["updated"] = datetime.now(timezone.utc).isoformat()
                _save(result)
                return
            except Exception as e:
                print(f"[api] JSON parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[api] Request error: {e}", file=sys.stderr)

    # 2. Fall back to homepage HTML
    try:
        r = session.get(BASE_URL, timeout=25)
        print(f"[html] Status: {r.status_code}", file=sys.stderr)
        if r.status_code == 200:
            html = r.text
            if "just a moment" in html.lower() or "cloudflare" in html[:500].lower():
                print("[html] Cloudflare challenge page received", file=sys.stderr)
                result["error"] = "cloudflare_challenge"
            else:
                result = parse_html_page(html)
                print("[main] Got data from HTML page", file=sys.stderr)
        else:
            result["error"] = f"http_{r.status_code}"
    except Exception as e:
        print(f"[html] Request error: {e}", file=sys.stderr)
        result["error"] = str(e)

    result["updated"] = datetime.now(timezone.utc).isoformat()
    _save(result)


def _save(result):
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
