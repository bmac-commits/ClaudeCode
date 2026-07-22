# ParkGauge

Live trip-planning dashboards for every US national park — weather, air quality,
NPS alerts, live cameras, river/tide gauges, entrance status, and things to do,
all on one page per park. Live at [parkgauge.com](https://parkgauge.com).

## Architecture

```
index.html                 Hub page — grid of park cards, filterable by state
  └── PARKS array           One entry per dashboard (name, states, accent
                             color, gradient, icon, href)

{park}_cams.html            65 dashboards, one per park (e.g. yosemite_cams.html)
  └── map_viewer.html        Shared "Park Map PDF" viewer all 65 link through
```

Every dashboard is a **single static HTML file with no backend**. All live
data (weather, air quality, NPS alerts, USGS/NOAA gauges) is fetched directly
from public APIs in the browser on page load — there's no server-side
rendering or database. `yosemite_cams.html` is the canonical template: every
other `*_cams.html` file copies its CSS class system and JS structure, and
only the park-specific content (copy, camera list, fetch URLs, accordion
sections) differs. Read that one file (it has an in-file architecture
comment at the top) to understand the shape of all 65.

`map_viewer.html` exists so all 65 dashboards' map-PDF links can share one
wrapper page (with a persistent "All Parks" back link) instead of each
needing its own near-duplicate viewer file.

## Deployment

The site is a Cloudflare Workers **static assets** deployment (`wrangler.jsonc`,
`assets.directory: "."` — the whole repo root is the asset source).

**`git push` does NOT deploy the site.** Deploying requires a separate step:

```
npx wrangler deploy
```

`.assetsignore` is this project's `.gitignore`-equivalent for deployment — it
controls which files wrangler's raw filesystem scan is allowed to publish.
Anything that shouldn't be publicly served (internal scripts, logs, scratch
HTML, this repo's own tooling) must be listed there, or it goes live. This has
bitten the project once already (`waittimes_scraper.log.previous` was
publicly served until added to `.assetsignore`) — when adding any new
non-dashboard file to the repo root, check whether it needs to be added here
too.

## Background automation

Two Python scripts keep dashboard data fresh by running **locally on a
schedule via macOS LaunchAgents** (not GitHub Actions — the one workflow in
`.github/workflows/` is `workflow_dispatch`-only with its schedule
intentionally disabled, since these scrapers need a residential IP to avoid
being blocked):

- **`scrape_waittimes.py`** — scrapes Yosemite entrance wait times, writes
  `wait_times.json`.
- **`monitor.py`** — health-checks every live camera and gauge across all
  dashboards (reads `monitor_config.json`), writes `monitor_results.json`,
  which `status.html` renders as a red/green table.
- **`monitor_extract_config.py`** — regenerates `monitor_config.json` by
  parsing `index.html`'s `PARKS` array and each dashboard's `CAMS`/gauge-fetch
  code. It's not part of the scheduled run — re-run it by hand whenever a
  dashboard's cameras or gauges change, or a park is added/removed. It
  expects `PARKS` entries to use single-quoted `name: '...'` / `href: '...'`
  — reformatting that array can make a park silently drop out of monitoring.
- **`daily_digest.py`** — summarizes the scraper's log once a day to a local
  folder, replacing per-failure GitHub email notifications.

Both `scrape_waittimes.py` and `monitor.py` auto-commit and push their own
JSON output with `[skip ci]` commit messages — this is why
`chore: update wait times` / `chore: update dashboard monitor results`
commits appear in history without a human triggering them.

## Other Cloudflare Workers in this repo

- **`nps-alerts-proxy/`** — proxies `developer.nps.gov`'s alerts API with
  caching + rate limiting, since dashboards call it directly from the browser
  and the public API doesn't support CORS/has tight per-key limits.

## Adding a new park dashboard

1. Copy `yosemite_cams.html`'s structure (CSS + JS shape) into a new
   `{park}_cams.html`, swapping in the new park's content, cameras, and live
   data source URLs. Only use verified, real data sources — omit sections
   honestly (river gauges, live cams, etc.) rather than fabricate a source
   that doesn't exist for that park.
2. Add an entry to `PARKS` in `index.html` (matching the existing quoting
   style — see the comment above that array).
3. Re-run `python3 monitor_extract_config.py` so the new park's cameras/gauges
   get picked up by `monitor.py`.
4. Add a `.claude/launch.json` entry if you want a local preview server for it.
5. Verify in a browser, then `git push` **and** `npx wrangler deploy`.

## Not part of the live site

These files/directories live in the repo but are old prototypes or
scratch work unrelated to ParkGauge itself — most are already excluded from
deployment via `.assetsignore`:

`ocean_pong.html`, `real_estate_agent.py`, `wave_server.py`,
`hero_photo_demo.html`, `design_mockups.html`, `wave_map.html`,
`norcal_waves.py`, `norcal_cams.html`, `gee_waves.py`, `.gee_cache.json`,
`gee-env/`, `static/`, `templates/`, `site-analytics-proxy/` (empty scaffold,
no source).
