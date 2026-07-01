#!/usr/bin/env python3
"""
Scrapes Yosemite entrance wait times from yosemite.live and writes wait_times.json.
Uses Playwright to handle Cloudflare JS challenge.
"""
import asyncio
import json
import re
import sys
from datetime import datetime, timezone

OUTPUT_FILE = "wait_times.json"
URL = "https://yosemite.live"

# Keywords to match each entrance in page text
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
    """Extract a wait time in minutes from a string like '15 min', '~30 minutes', 'No Wait'."""
    text = text.strip().lower()
    if any(w in text for w in ["no wait", "0 min", "open"]):
        return 0
    m = re.search(r"(\d+)\s*(?:min|minute)", text)
    if m:
        return int(m.group(1))
    return None


async def try_api_endpoint(page):
    """Try to hit /api/waittimes directly and parse JSON."""
    try:
        resp = await page.goto(f"{URL}/api/waittimes", timeout=20000, wait_until="networkidle")
        if resp and resp.ok:
            body = await resp.text()
            data = json.loads(body)
            return data
    except Exception as e:
        print(f"[api] Failed: {e}", file=sys.stderr)
    return None


def parse_api_response(data):
    """Parse the yosemite.live /api/waittimes JSON into our format."""
    result = empty_result()
    # The actual structure is unknown — log it for debugging and try common shapes
    print(f"[api] Raw response: {json.dumps(data, indent=2)[:2000]}", file=sys.stderr)

    # Try shape: [{entrance: "Arch Rock", wait: 15, status: "open"}, ...]
    entrances = data if isinstance(data, list) else data.get("entrances", data.get("data", []))
    if not isinstance(entrances, list):
        return None  # Unknown shape

    for entry in entrances:
        name = str(entry.get("entrance", entry.get("name", entry.get("title", "")))).lower()
        wait_raw = entry.get("wait", entry.get("waitTime", entry.get("wait_time", entry.get("minutes"))))
        status = str(entry.get("status", "open")).lower()

        # Match to our keys
        matched_key = None
        for key, keywords in ENTRANCE_PATTERNS.items():
            if any(k in name for k in keywords):
                matched_key = key
                break

        if matched_key:
            wait_min = None
            if isinstance(wait_raw, (int, float)):
                wait_min = int(wait_raw)
            elif isinstance(wait_raw, str):
                wait_min = parse_wait_minutes(wait_raw)

            result["entrances"][matched_key] = {
                "wait": wait_min,
                "status": status,
            }

    return result


async def scrape_html_page(page):
    """Navigate to yosemite.live homepage and extract wait times from DOM."""
    result = empty_result()
    try:
        await page.goto(URL, timeout=40000, wait_until="networkidle")

        # Wait for Cloudflare challenge to potentially resolve
        await page.wait_for_timeout(5000)

        title = await page.title()
        print(f"[html] Page title: {title}", file=sys.stderr)

        # Dump page text for debugging on first run
        content = await page.content()
        print(f"[html] Page length: {len(content)} chars", file=sys.stderr)
        if "just a moment" in title.lower() or "cloudflare" in content.lower()[:500]:
            print("[html] Still on Cloudflare challenge page", file=sys.stderr)
            result["error"] = "cloudflare_challenge"
            return result

        # Try to find wait time info in the page
        # Look for text like "15 min", "No Wait", etc. near entrance names
        text = await page.evaluate("() => document.body.innerText")
        print(f"[html] Body text snippet: {text[:1000]}", file=sys.stderr)

        lines = text.split("\n")
        current_entrance = None

        for i, line in enumerate(lines):
            line_lower = line.lower().strip()

            # Detect which entrance we're looking at
            for key, keywords in ENTRANCE_PATTERNS.items():
                if any(k in line_lower for k in keywords):
                    current_entrance = key
                    break

            # Look for wait time on this line or next few lines
            wait_min = parse_wait_minutes(line)
            if wait_min is not None and current_entrance:
                result["entrances"][current_entrance]["wait"] = wait_min
                result["entrances"][current_entrance]["status"] = "open" if wait_min >= 0 else "closed"

    except Exception as e:
        print(f"[html] Error: {e}", file=sys.stderr)
        result["error"] = str(e)

    return result


async def main():
    from playwright.async_api import async_playwright

    result = empty_result()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        # Remove webdriver flag
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        # First try the API endpoint
        api_data = await try_api_endpoint(page)
        if api_data:
            parsed = parse_api_response(api_data)
            if parsed:
                result = parsed
                print("[main] Got data from API endpoint", file=sys.stderr)
            else:
                print("[main] API returned unknown shape, falling back to HTML", file=sys.stderr)
                result = await scrape_html_page(page)
        else:
            result = await scrape_html_page(page)

        await browser.close()

    result["updated"] = datetime.now(timezone.utc).isoformat()
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
