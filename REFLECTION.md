# Reflection

Answers to the take-home reflection prompts.

---

### 1. What assumptions did you make?

1. **Read-only workload**: The task instructions specified treating the portal as read-only. I assumed the data is operational snapshot data where a 5 to 10-minute cache TTL on lists and exports is completely acceptable for downstream consumers.
2. **Bulk export coverage**: Calling `GET /portal/export?page=1` currently returns all 403 meters in one payload despite having a page query parameter. I assumed this bulk endpoint is the intended source of truth for full-fleet synchronization, rather than paginating through the search endpoint 20 items at a time.
3. **Session lifetime**: The `__Secure-better-auth.session_token` cookie specifies a `Max-Age` of 3600 seconds (1 hour). I assumed tracking token age with a 60-second buffer plus catching 401 status codes to re-authenticate is sufficient for resilient long-running client sessions.
4. **Timezone**: Energy readings contain timestamps in `DD/MM/YYYY HH:mm` format with no timezone offset specified. Given the meters are located in Jaipur (Rajasthan), I parsed them into ISO-8601 assuming local Indian Standard Time (IST).

---

### 2. Which part was the most difficult, and how did you get unstuck?

The most challenging part was figuring out how the bulk export on the Transformers page worked.

Clicking "Export all meters" in the browser initiated a request to `/portal/export?page=1` that included two custom headers: `x-timestamp` and `x-signature`. Calling that endpoint directly with just the session cookie returned an unauthorized/forbidden response.

To solve this, I pulled the SvelteKit frontend bundle from `_app/immutable/nodes/4.2Bgc2kUI.js` and searched for the export request handler. I found a signing helper `C(e, t, n, r)` calling `crypto.subtle.sign("HMAC", ...)` using a key fetched from `GET /portal/keys`. The signature string was constructed as:
```
METHOD\nPATH\nQUERY\nTIMESTAMP
```
Once I saw the format, I reproduced the HMAC-SHA256 generation using Python's standard `hmac` and `hashlib` libraries. That unlocked the bulk export endpoint immediately.

---

### 3. If you had another day, what would you improve?

1. **Persistent storage & geospatial queries**: While an in-memory cache is fast and simple for 403 meters, at 100,000+ meters in-memory filtering falls apart. I would back the service with PostgreSQL + PostGIS (or SQLite with spatialite/R*Tree) to enable real radius and polygon queries (e.g., "find all faulty meters within 2 km of substation X").
2. **Consumption delta calculations**: The portal's `/portal/meters/{id}/energy` endpoint gives cumulative register readings (total lifetime kWh). For downstream analytics, calculating the delta $\Delta\text{kWh}$ between consecutive 30-minute intervals would be much more practical.
3. **Background synchronization worker**: Instead of waiting for a user request to trigger the HMAC export, a lightweight background task could refresh the cache on a timer (e.g., every 15 minutes) so client requests are always served instantaneously from local memory or Redis.
4. **Resilience / Circuit Breaker**: If the legacy portal has downtime or slow response times, adding retries with exponential backoff and a circuit breaker would prevent cascading failures in client services.

---

### 4. What mistake did you make while solving this (there's always one)?

When first testing the login endpoint via `curl -X POST https://urja-ops.flockenergy.tech/login`, I was greeted with:
```
403 Forbidden: Cross-site POST form submissions are forbidden
```
For a few minutes I thought there might be a hidden CSRF token in an input field or a cookie handshake step I had missed. After checking SvelteKit's standard form action behavior, I realized SvelteKit validates the HTTP `Origin` header against the host. Adding `Origin: https://urja-ops.flockenergy.tech` to the request fixed it right away and returned the session cookie.

Another catch was that meter detail schemas are not consistent across the fleet. Meters with `build: "legacy"` use a flat array of `{parameterName, parameterValue}` objects, whereas newer meters with `build: "v2"` store their attributes in a JSON-encoded string inside a `classData` field. My initial parser broke on the v2 meters until I inspected `J100004` and added handling for both formats.

---

### 5. If you were reviewing your own submission, what would you criticise?

1. **In-memory cache in single process**: Using an in-memory dictionary for caching means multiple Uvicorn worker processes will each maintain their own cache and authenticate separately. In a multi-worker production deployment, this should be backed by Redis.
2. **Coupling single meter detail to SvelteKit `__data.json`**: For individual meter lookups, the service calls `/meters/{id}/__data.json`, which is an internal SvelteKit SSR data route. If the frontend developers modify their route structure or SvelteKit updates its internal encoding, this route could change. A safer approach would be to populate single-meter details directly from the bulk export cache.
3. **Absence of interval consumption deltas**: As noted earlier, the API returns the portal's raw cumulative kWh and kVAh numbers. While typed and validated, it would be much more useful to data consumers if the API calculated the per-interval consumption.
