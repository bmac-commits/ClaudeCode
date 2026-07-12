const GRAPHQL_URL = 'https://api.cloudflare.com/client/v4/graphql';
const CACHE_TTL_SECONDS = 3600; // 1 hour — visit counts don't need to be real-time
const WINDOW_DAYS = 30;

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

function json(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS_HEADERS, ...extraHeaders },
  });
}

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    if (url.pathname !== '/stats') {
      return json({ error: 'Not found. Use /stats' }, 404);
    }

    const cache = caches.default;
    const cacheKey = new Request(url.toString(), request);
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    const until = new Date();
    const since = new Date(until.getTime() - WINDOW_DAYS * 24 * 60 * 60 * 1000);
    const sinceStr = isoDate(since);
    const untilStr = isoDate(until);

    const query = `
      query Stats($accountTag: string!, $siteTag: string!, $since: Date!, $until: Date!) {
        viewer {
          accounts(filter: { accountTag: $accountTag }) {
            rumPageloadEventsAdaptiveGroups(
              limit: 1000
              filter: { siteTag: $siteTag, date_geq: $since, date_leq: $until }
            ) {
              count
              sum { visits }
            }
          }
        }
      }
    `;

    const gqlRes = await fetch(GRAPHQL_URL, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.CF_API_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        query,
        variables: {
          accountTag: env.CF_ACCOUNT_TAG,
          siteTag: env.CF_SITE_TAG,
          since: sinceStr,
          until: untilStr,
        },
      }),
    });

    if (!gqlRes.ok) {
      return json({ error: 'Upstream analytics request failed' }, 502);
    }

    const gqlBody = await gqlRes.json();
    if (gqlBody.errors) {
      return json({ error: 'Analytics query error', details: gqlBody.errors }, 502);
    }

    const groups = gqlBody.data?.viewer?.accounts?.[0]?.rumPageloadEventsAdaptiveGroups ?? [];
    const pageviews = groups.reduce((sum, g) => sum + (g.count ?? 0), 0);
    const visits = groups.reduce((sum, g) => sum + (g.sum?.visits ?? 0), 0);

    const response = json({
      pageviews,
      visits,
      windowDays: WINDOW_DAYS,
      since: sinceStr,
      until: untilStr,
    }, 200, {
      'Cache-Control': `public, max-age=${CACHE_TTL_SECONDS}`,
    });

    ctx.waitUntil(cache.put(cacheKey, response.clone()));
    return response;
  },
};
