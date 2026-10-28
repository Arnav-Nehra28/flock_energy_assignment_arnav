# Reflection

My notes and answers to the assignment reflection prompts.

---

### 1. What assumptions did you make?

1. **Read-only access**: The prompt specified treating the portal as read-only. I assumed meter operational data changes relatively slowly, so caching lists and bulk snapshots for 5 to 10 minutes in memory is a sensible balance between freshness and load on the legacy portal.
2. **Bulk export as the source of truth**: Calling `GET /portal/export?page=1` dumps all 403 meters in a single JSON payload despite accepting a `page` query parameter. I assumed this bulk snapshot was intended to be the primary dataset for fleet sync, filtering, and hierarchy building, rather than crawling the search endpoint 20 items at a time.
3. **Session handling**: The `__Secure-better-auth.session_token` cookie has a 1-hour `Max-Age`. I assumed tracking token age locally with a small buffer, plus catching any unexpected 401s to trigger a transparent re-login, is sufficient for continuous background uptime.
4. **Timezone**: Energy readings use `DD/MM/YYYY HH:mm` with no timezone specified. Since the meters are in the Jaipur area, I parsed timestamps assuming Indian Standard Time (IST).

---

### 2. Which part was the most difficult, and how did you get unstuck?

The trickiest part was figuring out how the bulk export on the Transformers page worked.

Clicking "Export all meters" in the browser initiated a request to `/portal/export?page=1`. When I tried copying the URL and hitting it via cURL with my session cookie, the server returned an unauthorized error. Looking closely at Chrome's Network tab, the browser was sending two extra request headers: `x-timestamp` and `x-signature`.

To figure out how the signature was generated, I opened the SvelteKit frontend chunk for that page (`nodes/4.2Bgc2kUI.js`), prettified it, and searched for `export`. I found a signing helper calling `crypto.subtle.sign("HMAC", ...)` using a secret retrieved from `/portal/keys`. The message format being signed was:
```
METHOD\nPATH\nQUERY\nTIMESTAMP
```
Once I had the exact string format and the endpoint providing the key, replicating it in Python with `hmac` and `hashlib.sha256` was straightforward and unlocked the full dataset.

---

### 3. If you had another day, what would you improve?

1. **Database & spatial queries**: In-memory caching works great for 403 meters, but it wouldn't scale to a utility with hundreds of thousands of meters. I'd back this with PostgreSQL and PostGIS to support real geospatial queries (like finding all faulty meters within 3 km of a substation or polygon bounding boxes).
2. **Consumption interval deltas**: Right now the API exposes the portal's raw cumulative meter readings (total lifetime kWh). For downstream billing or load analysis, computing the delta (consumption between consecutive 30-minute intervals) directly in the response would save consumers from doing the math themselves.
3. **Background sync worker**: Instead of refreshing the cache lazily when a client request arrives, I'd set up a background scheduler (like APScheduler or Celery) to refresh the export snapshot periodically so client requests never experience a cache-miss latency penalty.
4. **Circuit breaker & retries**: If the legacy portal has a blip or starts throttling, adding exponential backoff retries and a circuit breaker would prevent client requests from timing out or hanging.

---

### 4. What mistake did you make while solving this (there's always one)?

When I first tested the login endpoint with cURL:
```bash
curl -X POST https://urja-ops.flockenergy.tech/login -d "email=operator@urja.local&password=urja-ops-2026"
```
It immediately threw back:
```
403 Forbidden: Cross-site POST form submissions are forbidden
```
I spent about 15 minutes inspecting the login page HTML looking for a hidden CSRF input token or an initial cookie handshake that I might have missed. After checking SvelteKit's default form action security, I realized SvelteKit verifies the `Origin` header against the host. Adding `-H "Origin: https://urja-ops.flockenergy.tech"` solved it right away.

The second surprise was meter detail schemas. The first few meters I inspected (`J100000`, `J100001`) used a flat array of `{parameterName, parameterValue}` objects. But when testing across the full list, meter `J100004` caused the parser to fail because newer meters use `build: "v2"`, where attributes are packed inside a stringified JSON string under `classData`. I had to update the decoder to detect and unpack both formats.

---

### 5. If you were reviewing your own submission, what would you criticise?

1. **In-memory cache across workers**: Using an in-memory dictionary cache means if you run Uvicorn with `--workers 4`, each worker maintains its own independent cache and auth session. In production, caching should use an external Redis instance.
2. **Coupling to SvelteKit `__data.json`**: For individual meter lookups (`/meters/{id}`), the service fetches `/meters/{id}/__data.json`, which is an internal SvelteKit SSR data route. If the frontend maintainers change their routing structure, this route could break. A more resilient approach would be resolving individual meter details out of the cached bulk export snapshot.
3. **Raw cumulative readings instead of deltas**: While the consumption endpoint cleans up timestamps and parses numbers to floats, it still returns cumulative lifetime kWh. Providing interval deltas would be much more practical for downstream consumers.
