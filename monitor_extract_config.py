#!/usr/bin/env python3
"""
One-time/occasional tool: scans every dashboard listed in index.html's PARKS
array and extracts its live camera and river/tide gauge definitions into
monitor_config.json, which monitor.py then uses for the actual periodic
health checks.

Re-run this whenever a dashboard's CAMS array or gauge fetch code changes
(new park added, camera swapped, etc.) — it's not part of the scheduled job.
"""
import json
import re
from pathlib import Path

REPO = Path(__file__).parent
INDEX_HTML = REPO / "index.html"
OUT_FILE = REPO / "monitor_config.json"


def extract_parks():
    text = INDEX_HTML.read_text()
    array_text = text.split("const PARKS = [", 1)[1]
    array_text = array_text.split("\n  ];", 1)[0]
    # Each park object is separated by a line that's just "    {"
    chunks = re.split(r"\n\s*\{\s*\n", array_text)
    parks = []
    for chunk in chunks:
        name_m = re.search(r"name:\s*'([^']+)'", chunk)
        href_m = re.search(r"href:\s*'([a-z_0-9]+\.html)'", chunk)
        if name_m and href_m:
            parks.append({"name": name_m.group(1), "file": href_m.group(1)})
    return parks


def extract_cams(html_text):
    cams = []
    m = re.search(r"const CAMS\s*=\s*\[(.*?)\n\s*\];", html_text, re.S)
    if not m:
        return cams
    body = m.group(1)
    for obj_m in re.finditer(r"\{([^{}]*)\}", body, re.S):
        obj = obj_m.group(1)

        def field(key):
            fm = re.search(rf"{key}:\s*'([^']*)'", obj)
            return fm.group(1) if fm else None

        name = field("name")
        cam_type = field("type")
        url = field("url")
        embed_id = field("embedId")
        if not name:
            continue
        if cam_type == "img" and url:
            cams.append({"name": name, "type": "image", "url": url})
        elif cam_type == "youtube" and embed_id:
            cams.append({"name": name, "type": "youtube", "embedId": embed_id})
    return cams


def resolve_var_list(html_text, var_name):
    m = re.search(rf"const {var_name}\s*=\s*\[([^\]]*)\]", html_text)
    if not m:
        return None
    return re.findall(r"'([0-9]+)'", m.group(1))


def resolve_var_scalar(html_text, var_name):
    m = re.search(rf"const {var_name}\s*=\s*'([0-9]+)'", html_text)
    if m:
        return m.group(1)
    # Loop pattern: `for (const siteId of sites) { ... ${siteId} ... }`
    # — siteId isn't a scalar const, it's bound to each item of another array.
    loop_m = re.search(rf"for\s*\(\s*const\s+{var_name}\s+of\s+([A-Za-z_]+)\s*\)", html_text)
    if loop_m:
        return None  # signal caller to treat this as a list via the loop-source var
    return None


def resolve_loop_source(html_text, var_name):
    """If var_name is bound by `for (const var_name of SOURCE)`, return SOURCE's list."""
    loop_m = re.search(rf"for\s*\(\s*const\s+{var_name}\s+of\s+([A-Za-z_]+)\s*\)", html_text)
    if not loop_m:
        return None
    return resolve_var_list(html_text, loop_m.group(1))


def extract_gauges(html_text):
    gauges = []
    seen = set()

    # USGS waterservices — either literal sites=NNN,NNN or sites=${VAR}.
    # Also capture this same call's parameterCd, since some sites (e.g. a
    # lake-elevation-only gauge) don't report the generic discharge/gauge
    # height/temperature codes and need their own specific one to check.
    for m in re.finditer(
        r"waterservices\.usgs\.gov/nwis/iv/\?[^'\"`]*?sites=([^&'\"`]+)(?:[^'\"`]*?parameterCd=([0-9,]+))?[^'\"`]*",
        html_text,
    ):
        raw, param_cd = m.group(1), m.group(2)
        ids = []
        if raw.startswith("${"):
            var_m = re.match(r"\$\{([A-Za-z_]+)", raw)
            if var_m:
                var_name = var_m.group(1)
                if var_name.endswith("s") or "SITES" in var_name.upper():
                    ids = resolve_var_list(html_text, var_name) or []
                else:
                    scalar = resolve_var_scalar(html_text, var_name)
                    if scalar:
                        ids = [scalar]
                    else:
                        ids = resolve_loop_source(html_text, var_name) or []
        else:
            ids = re.findall(r"[0-9]{6,}", raw)
        for site_id in ids:
            key = ("usgs", site_id)
            if key not in seen:
                seen.add(key)
                entry = {"type": "usgs", "id": site_id, "name": f"USGS site {site_id}"}
                if param_cd:
                    entry["parameterCd"] = param_cd
                gauges.append(entry)

    # NOAA Tides & Currents
    for m in re.finditer(r"tidesandcurrents\.noaa\.gov/api/prod/datagetter\?[^'\"`]*?station=([0-9]+)", html_text):
        station = m.group(1)
        key = ("noaa-tide", station)
        if key not in seen:
            seen.add(key)
            gauges.append({"type": "noaa-tide", "id": station, "name": f"NOAA tide station {station}"})

    # NOAA/NWS Water Prediction Service gauges
    for m in re.finditer(r"api\.water\.noaa\.gov/nwps/v1/gauges/([a-zA-Z0-9]+)", html_text):
        gauge_id = m.group(1)
        key = ("noaa-gauge", gauge_id)
        if key not in seen:
            seen.add(key)
            gauges.append({"type": "noaa-gauge", "id": gauge_id, "name": f"NOAA/NWS gauge {gauge_id.upper()}"})

    return gauges


def main():
    parks = extract_parks()
    config = []
    for park in parks:
        path = REPO / park["file"]
        if not path.exists():
            print(f"[warn] missing file: {park['file']}")
            continue
        html_text = path.read_text()
        cams = extract_cams(html_text)
        gauges = extract_gauges(html_text)
        if park["file"] == "yosemite_cams.html":
            # Not extractable from a URL pattern like other gauges — this
            # checks scrape_waittimes.py's published output (via the same
            # GitHub raw URL the live page fetches) rather than a live API.
            gauges.append({
                "type": "waittimes",
                "id": "yosemite-entrance-waits",
                "name": "Yosemite entrance wait times",
            })
        config.append({
            "name": park["name"],
            "file": park["file"],
            "cams": cams,
            "gauges": gauges,
        })

    OUT_FILE.write_text(json.dumps(config, indent=2) + "\n")

    total_cams = sum(len(p["cams"]) for p in config)
    total_gauges = sum(len(p["gauges"]) for p in config)
    print(f"[extract] {len(config)} parks, {total_cams} camera checks, {total_gauges} gauge checks")
    print(f"[extract] wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
