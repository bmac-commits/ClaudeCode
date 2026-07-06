const NPS_ALERTS_URL = 'https://developer.nps.gov/api/v1/alerts';
const CACHE_TTL_SECONDS = 300; // 5 minutes — alerts don't change fast enough to need less

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

export default {
  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    if (url.pathname !== '/alerts') {
      return json({ error: 'Not found. Use /alerts?parkCode=XXXX' }, 404);
    }

    const parkCode = url.searchParams.get('parkCode');
    if (!parkCode) {
      return json({ error: 'Missing required parkCode query parameter' }, 400);
    }

    // Cache is keyed on the incoming request URL, so each parkCode gets its
    // own cache entry. This is what turns millions of client requests into a
    // handful of upstream NPS calls per cache window, instead of one NPS
    // call per visitor.
    const cache = caches.default;
    const cacheKey = new Request(url.toString(), request);
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    const upstream = new URL(NPS_ALERTS_URL);
    upstream.searchParams.set('parkCode', parkCode);
    upstream.searchParams.set('limit', '20');
    upstream.searchParams.set('api_key', env.NPS_API_KEY);

    const npsRes = await fetch(upstream.toString());
    const bodyText = await npsRes.text();

    const response = new Response(bodyText, {
      status: npsRes.status,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': `public, max-age=${CACHE_TTL_SECONDS}`,
        ...CORS_HEADERS,
      },
    });

    // Only cache successful upstream responses — never cache a rate-limit
    // or error response, so a transient NPS failure doesn't get "stuck".
    if (npsRes.ok) {
      ctx.waitUntil(cache.put(cacheKey, response.clone()));
    }

    return response;
  },
};
