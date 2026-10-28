// Duo tracker relay (Cloudflare Worker)
//
// The Pit Wall and National pages read the bots' data and run status from
// GitHub. Without a token GitHub allows only 60 requests an hour per internet
// connection. This relay makes those same requests with your read-only token
// (5,000 an hour) and shares answers between everyone for a few seconds.
//
// It only passes through the handful of read-only requests the pages make,
// for this one repository, so it can't be used to reach anything else.
//
// Setup: paste this into a Cloudflare Worker, then add a secret named
// GITHUB_TOKEN (a fine-grained token with read-only access to duo-tracker).

const REPO = "aryakvsathye/duo-tracker";
const ALLOWED_ORIGINS = ["https://aryakvsathye.github.io"];
const ALLOWED_PATHS = [
  new RegExp(`^/repos/${REPO}/contents/(state\\.json|restocks\\.csv|national/state\\.json|national/events\\.csv)$`),
  new RegExp(`^/repos/${REPO}/actions/workflows/(check|national)\\.yml/runs$`),
  new RegExp(`^/repos/${REPO}/actions/runs/\\d+/jobs$`),
];
const SHARE_SECONDS = 10;   // how long one answer from GitHub is reused
const PASS_HEADERS = ["content-type", "etag", "x-ratelimit-remaining", "x-ratelimit-reset"];

// Recent answers, kept in memory (the Cache API doesn't work on workers.dev)
const recent = new Map();

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0],
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Access-Control-Allow-Headers": "Accept, If-None-Match",
      "Access-Control-Expose-Headers": "ETag, X-RateLimit-Remaining, X-RateLimit-Reset",
      "Vary": "Origin",
    };
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "GET") return new Response("Only GET is allowed", { status: 405, headers: cors });

    const url = new URL(request.url);
    if (url.pathname === "/" ) return new Response("Duo tracker relay is running", { headers: cors });
    if (!ALLOWED_PATHS.some(re => re.test(url.pathname))) {
      return new Response("Not a request this relay passes on", { status: 404, headers: cors });
    }
    if (!env.GITHUB_TOKEN) return new Response("Relay has no GITHUB_TOKEN secret yet", { status: 500, headers: cors });

    const accept = request.headers.get("Accept") || "application/vnd.github+json";
    const key = `${url.pathname}${url.search}|${accept}`;
    let hit = recent.get(key);

    if (!hit || Date.now() - hit.at > SHARE_SECONDS * 1000) {
      const gh = await fetch(`https://api.github.com${url.pathname}${url.search}`, {
        headers: {
          "Accept": accept,
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "User-Agent": "duo-tracker-relay",
          "X-GitHub-Api-Version": "2022-11-28",
          ...(hit && hit.etag ? { "If-None-Match": hit.etag } : {}),
        },
      });
      if (gh.status === 304 && hit) {
        hit.at = Date.now();                       // unchanged on GitHub: keep our copy
      } else {
        const headers = {};
        PASS_HEADERS.forEach(h => { const v = gh.headers.get(h); if (v) headers[h] = v; });
        hit = { at: Date.now(), status: gh.status, headers, etag: gh.headers.get("etag"), body: await gh.text() };
        if (gh.ok || gh.status === 404) recent.set(key, hit);
        if (recent.size > 200) recent.delete(recent.keys().next().value);
      }
    }

    // The browser already has this exact version: answer "not modified"
    const theirs = request.headers.get("If-None-Match");
    if (theirs && hit.etag && theirs === hit.etag && hit.status === 200) {
      return new Response(null, { status: 304, headers: { ...cors, "ETag": hit.etag } });
    }
    return new Response(hit.body, {
      status: hit.status,
      headers: { ...cors, ...hit.headers, "Cache-Control": "no-cache" },
    });
  },
};
