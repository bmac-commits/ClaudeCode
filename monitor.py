#!/usr/bin/env python3
"""
Periodic health check for every live camera and river/tide gauge across all
44 park dashboards. Reads monitor_config.json (built by
monitor_extract_config.py), hits each live data source directly, and writes
monitor_results.json — which status.html reads to render a glanceable
red/green table so dashboards don't need to be checked one by one.

Runs 3x/day via the com.parks.monitor LaunchAgent. Commits + pushes
monitor_results.json so the hosted status page (GitHub Pages) always shows
the latest run, same pattern as scrape_waittimes.py.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

REPO_DIR = Path(__file__).parent
CONFIG_FILE = REPO_DIR / "monitor_config.json"
RESULTS_FILE = REPO_DIR / "monitor_results.json"
TIMEOUT = 15


def check_image_cam(cam):
    try:
        session = requests.Session(impersonate="chrome124")
        r = session.get(cam["url"], timeout=TIMEOUT)
        content_type = r.headers.get("content-type", "")
        if r.status_code == 200 and content_type.startswith("image/"):
            return {"status": "ok", "detail": f"HTTP 200, {content_type}"}
        return {"status": "error", "detail": f"HTTP {r.status_code}, content-type={content_type or 'none'}"}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}


def check_youtube_cam(cam):
    embed_id = cam["embedId"]
    try:
        session = requests.Session(impersonate="chrome124")
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={embed_id}&format=json"
        r = session.get(oembed_url, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "error", "detail": f"oEmbed HTTP {r.status_code} — video may be deleted/private"}
        thumb = session.get(f"https://i.ytimg.com/vi/{embed_id}/hqdefault.jpg", timeout=TIMEOUT)
        if thumb.status_code != 200:
            return {"status": "error", "detail": f"Thumbnail HTTP {thumb.status_code}"}
        return {"status": "ok", "detail": "oEmbed + thumbnail both reachable"}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}


def check_usgs_gauge(gauge):
    try:
        session = requests.Session(impersonate="chrome124")
        param_cd = gauge.get("parameterCd", "00060,00065,00010")
        url = f"https://waterservices.usgs.gov/nwis/iv/?sites={gauge['id']}&parameterCd={param_cd}&format=json&period=PT2H"
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "error", "detail": f"HTTP {r.status_code}"}
        data = r.json()
        series = data.get("value", {}).get("timeSeries", [])
        has_reading = any(ts.get("values", [{}])[0].get("value") for ts in series)
        if has_reading:
            return {"status": "ok", "detail": f"{len(series)} parameter(s) reporting"}
        return {"status": "error", "detail": "No current readings in response (site may be inactive/offline)"}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}


def check_noaa_tide_gauge(gauge):
    try:
        session = requests.Session(impersonate="chrome124")
        url = f"https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?station={gauge['id']}&product=water_level&datum=MLLW&units=english&time_zone=lst_ldt&format=json&date=latest"
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "error", "detail": f"HTTP {r.status_code}"}
        data = r.json()
        if data.get("data"):
            return {"status": "ok", "detail": "Current water level reported"}
        return {"status": "error", "detail": f"No data field in response: {data.get('error', data)}"}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}


def check_noaa_water_gauge(gauge):
    try:
        session = requests.Session(impersonate="chrome124")
        url = f"https://api.water.noaa.gov/nwps/v1/gauges/{gauge['id']}"
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "error", "detail": f"HTTP {r.status_code}"}
        data = r.json()
        primary = data.get("status", {}).get("observed", {}).get("primary")
        if primary is not None and primary != -999:
            return {"status": "ok", "detail": f"Current reading: {primary} {data.get('status', {}).get('observed', {}).get('primaryUnit', '')}"}
        return {"status": "error", "detail": "No current observation (primary reading missing/sentinel)"}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}


GAUGE_CHECKERS = {
    "usgs": check_usgs_gauge,
    "noaa-tide": check_noaa_tide_gauge,
    "noaa-gauge": check_noaa_water_gauge,
}


def run_checks():
    config = json.loads(CONFIG_FILE.read_text())
    results = []
    ok_count = 0
    error_count = 0

    for park in config:
        park_result = {"name": park["name"], "file": park["file"], "cams": [], "gauges": []}

        for cam in park["cams"]:
            if cam["type"] == "image":
                outcome = check_image_cam(cam)
            elif cam["type"] == "youtube":
                outcome = check_youtube_cam(cam)
            else:
                outcome = {"status": "error", "detail": f"Unknown cam type: {cam['type']}"}
            park_result["cams"].append({"name": cam["name"], **outcome})
            ok_count += outcome["status"] == "ok"
            error_count += outcome["status"] == "error"

        for gauge in park["gauges"]:
            checker = GAUGE_CHECKERS.get(gauge["type"])
            outcome = checker(gauge) if checker else {"status": "error", "detail": f"Unknown gauge type: {gauge['type']}"}
            park_result["gauges"].append({"name": gauge["name"], **outcome})
            ok_count += outcome["status"] == "ok"
            error_count += outcome["status"] == "error"

        results.append(park_result)
        print(f"[monitor] {park['name']}: {len(park['cams'])} cam(s), {len(park['gauges'])} gauge(s) checked")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok_count": ok_count,
        "error_count": error_count,
        "parks": results,
    }


def git_push():
    subprocess.run(["git", "-C", str(REPO_DIR), "add", "monitor_results.json"], check=True)
    diff = subprocess.run(
        ["git", "-C", str(REPO_DIR), "diff", "--cached", "--quiet"]
    )
    if diff.returncode == 0:
        print("[git] No changes, skipping commit")
        return
    subprocess.run(
        ["git", "-C", str(REPO_DIR), "commit", "-m", "chore: update dashboard monitor results [skip ci]"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(REPO_DIR), "pull", "--no-rebase", "--no-edit", "-X", "ours", "--quiet"],
        check=True,
    )
    subprocess.run(["git", "-C", str(REPO_DIR), "push"], check=True)
    print("[git] Pushed monitor_results.json")


def main():
    try:
        results = run_checks()
    except Exception as e:
        print(f"[error] Monitor run failed: {e}", file=sys.stderr)
        sys.exit(1)

    RESULTS_FILE.write_text(json.dumps(results, indent=2) + "\n")
    print(f"[monitor] {results['ok_count']} ok, {results['error_count']} error — wrote {RESULTS_FILE}")

    try:
        git_push()
    except subprocess.CalledProcessError as e:
        print(f"[error] git push failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
