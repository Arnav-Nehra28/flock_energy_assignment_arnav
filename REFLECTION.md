# Engineering Reflection

Answers to the evaluation reflection questions for the Flock Energy Take-Home Assignment.

---

### 1. What assumptions did you make?

1. **Read-Only Invariance**: We assumed the underlying Urja portal data is fundamentally read-only from an external perspective. Operations staff consult it; changes are infrequent enough that short-term caching (5–10 minutes) provides high performance without serving meaningfully stale data.
2. **Fixed Bulk Scope**: The `/portal/export` endpoint accepts `page=1` and currently dumps all 403 meters in a single JSON payload. We assumed that in production with tens of thousands of meters, the portal backend might eventually paginate this or stream NDJSON, but currently it serves as a reliable bulk sync mechanism.
3. **Session Lifetime**: We observed the `__Secure-better-auth.session_token` cookie carries `Max-Age=3600` (1 hour). We assumed that proactive token refresh with a 60-second safety margin (and a fallback re-auth on HTTP 401) is sufficient to maintain uninterrupted uptime across long-running worker processes.
4. **Timezone Context**: The portal provides energy timestamps in the format `DD/MM/YYYY HH:mm` without timezone offsets. Because the meters are located in Jaipur (Rajasthan, India), we converted these to standard ISO-8601 strings assuming Indian Standard Time (IST / UTC+05:30).

---

### 2. Which part was the most difficult, and how did you get unstuck?

**The most difficult part was discovering and reverse-engineering the bulk export signature mechanism.**

When examining the Distribution Transformers page in the portal, there was an "Export all meters" button. Inspecting the network requests and decompiling the client JavaScript bundles revealed that:
1. It wasn't calling a standard REST query with a bearer token.
2. It called an obscure endpoint `/portal/keys` which returned a `signingSecret`.
3. It constructed a multi-line message: `METHOD\nPATH\nQUERY\nTIMESTAMP`.
4. It signed the string using HMAC-SHA256 in WebCrypto (`crypto.subtle`) and sent the result via `x-timestamp` and `x-signature` headers.

**How we got unstuck**:
Rather than guessing parameter combinations or trying to brute-force the API, we extracted the SvelteKit compiled asset (`nodes/4.2Bgc2kUI.js`). By formatting the minified code, we isolated the function `C(e, t, n, r)` which explicitly implemented the WebCrypto HMAC-SHA256 signing algorithm. We reproduced this signing scheme in Python using `hmac` and `hashlib.sha256`, which immediately unlocked the entire enriched dataset of 403 meters.

---

### 3. If you had another day, what would you improve?

1. **Persistent Geospatial & Search Index (PostGIS / SQLite with R*Tree)**:
   Currently, geo queries and attribute filters run in-memory over the cached export dataset. While this executes in sub-millisecond time for 403 meters, at 100,000+ meters it would degrade CPU and memory. We would introduce SQLite with R*Tree indexing or PostgreSQL/PostGIS to support bounding box queries ("meters within 5km of coordinates X, Y") and fast indexed faceted search.
2. **Streaming & Webhooks / Change Data Capture**:
   Instead of on-demand portal polling, a background sync worker could periodically hash the export payload, detect newly installed/faulty meters or status transitions, and emit webhook events to downstream microservices.
3. **Comprehensive Visual Dashboard**:
   A lightweight, reactive dashboard (e.g. built with React/Vite or SvelteKit + Leaflet) displaying a live map of meters colored by status (faulty vs installed), transformer load aggregations, and interactive consumption time-series charts.
4. **Rate Limiting & Resilient Backoff Circuit Breaker**:
   Add a circuit breaker (e.g., using `tenacity` or `pybreaker`) that prevents slamming the legacy portal if it becomes degraded or experiences downtime.

---

### 4. What mistake did you make while solving this (there's always one)?

**Mistake:**
During initial exploration of the `/login` route, we attempted a direct `curl -X POST /login` with username and password, expecting standard session cookies. Instead, the server responded with:
`403 Forbidden: Cross-site POST form submissions are forbidden`.

We initially wondered whether there was an undocumented CSRF token hidden in the HTML DOM or a cookie handshake requirement. 

**Fix:**
After reviewing SvelteKit's built-in CSRF protection specifications, we realized SvelteKit validates the HTTP `Origin` header on all form actions. As soon as we included `Origin: https://urja-ops.flockenergy.tech`, authentication succeeded immediately and returned the Better Auth session token.

Another minor pitfall was discovering that the portal detail page serves two different data schemas: older meters use a flat array of `{parameterName, parameterValue}` pairs (`build: legacy`), whereas newer meters serialize nameplate data into a stringified JSON object named `classData` (`build: v2`). Early versions of our detail parser crashed on v2 meters until we inspected both schemas and added polymorphic deserialization.

---

### 5. If you were reviewing your own submission, what would you criticise?

1. **In-Memory Cache vs. Distributed Storage**:
   The current cache (`TTLCache`) lives in Python process memory. If the FastAPI application is scaled horizontally behind a round-robin load balancer across multiple containers or workers, each worker maintains its own portal session and cache, leading to duplicated login calls and cache fragmentation. In production, Redis or Memcached should back the caching and distributed session locks.
2. **Coupling to SvelteKit Internal Routing**:
   The single-meter detail endpoint fetches `/meters/{meter_id}/__data.json` directly from SvelteKit's internal server-rendered JSON endpoints. While this allows pulling hierarchy and nameplate data for individual meters without running a full bulk export, SvelteKit positional data formats can change during framework updates. A cleaner architectural alternative for single-meter lookup is serving it directly from the cached bulk export dataset.
3. **Missing Delta Calculation for Energy Readings**:
   The energy readings returned by `/portal/meters/{id}/energy` are raw cumulative meter register values (e.g., total kWh: 48,438.74). The API normalizes the timestamps and types, but does not calculate interval deltas (kWh consumed during that specific 30-minute window). Providing computed interval consumption alongside raw registers would be much more immediately usable for analytics teams.
