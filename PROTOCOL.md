# Urja Portal Reverse Engineering Notes

These are my technical notes from reverse-engineering the **Urja Meter Ops** portal at `https://urja-ops.flockenergy.tech`.

---

## 1. Stack & Architecture

Looking at response headers, DOM markup, and network requests:
- **Framework**: SvelteKit (Svelte 5) with server-side rendering (SSR) and client-side hydration.
- **Auth Layer**: Better Auth (`__Secure-better-auth.session_token` cookie).
- **Transport**: HTTPS with HTTP/2 (supports HTTP/3 via `alt-svc: h3`).
- **Styling**: Tailwind CSS.

When a page loads, SvelteKit delivers initial HTML along with a JS bundle (`_app/immutable/...`). Subsequent navigation and data updates happen client-side using internal JSON endpoints under `/portal/*` and SvelteKit route data at `/__data.json`.

---

## 2. Authentication Flow

### Login (`POST /login`)
- **URL**: `https://urja-ops.flockenergy.tech/login`
- **Content-Type**: `application/x-www-form-urlencoded`
- **Payload**: `email=operator%40urja.local&password=urja-ops-2026`
- **Gotcha**: SvelteKit strictly validates the `Origin` header on POST form submissions. If you omit `Origin: https://urja-ops.flockenergy.tech`, you get:
  ```
  403 Forbidden: Cross-site POST form submissions are forbidden
  ```

### Session Cookie
On valid credentials, the server returns a session cookie:
```
__Secure-better-auth.session_token=<token>; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax
```
- **TTL**: 1 hour (3600 seconds).
- It's marked `HttpOnly` and `Secure`, so client JS never touches the raw cookie value directly; the browser passes it automatically with requests.

### Checking Session State
You can verify the session anytime via:
```
GET /api/auth/get-session
```
Returns:
```json
{
  "session": {
    "id": "...",
    "token": "...",
    "userId": "...",
    "expiresAt": "2026-10-27T17:57:28.164Z"
  },
  "user": {
    "id": "...",
    "name": "Ops Desk",
    "email": "operator@urja.local"
  }
}
```

Any `/portal/*` endpoint hit without this cookie returns `401 Unauthorized`:
```json
{"error": "unauthorized", "message": "A valid session is required."}
```

---

## 3. Discovered Endpoints

All data endpoints require the session cookie.

### 1. Meter Search & List
```
GET /portal/meters/search?q={query}&page={page}
```
- **Pagination**: 20 meters per page.
- **Search behavior**: Matches against `meterId` and `serialNo`. It ignores make, phase, or status.
- An empty query (`q=`) lists meters in order.
- Response payload:
  ```json
  {
    "data": [
      {
        "meterId": "J100000",
        "serialNo": "SE33962",
        "make": "HPL",
        "phaseType": "single",
        "installStatus": "Decommissioned",
        "dtCode": "DT-001"
      }
    ],
    "total": 403
  }
  ```

### 2. Distribution Transformers
```
GET /portal/dts?page={page}
```
- **Pagination**: 20 items per page (40 DTs total across 2 pages).
- Sample item:
  ```json
  {
    "code": "DT-001",
    "name": "Malviya Nagar DT 1",
    "feederCode": "F-001",
    "capacityKva": 100
  }
  ```

### 3. Meter Geolocation
```
GET /portal/meters/{meterId}/geo
```
- Returns coordinates for a specific meter:
  ```json
  {
    "data": {
      "latitude": "26.938961002479868",
      "longitude": "75.83095696146852"
    }
  }
  ```
- *Note*: Both latitude and longitude are returned as string values, not numbers.

### 4. Meter Energy / Interval Consumption
```
GET /portal/meters/{meterId}/energy
```
- Returns ~337 interval readings (about 7 days of 30-minute blocks).
- Sample item:
  ```json
  {
    "timestamp": "23/06/2026 23:30",
    "kwh": "48438.74",
    "kvah": "52313.84",
    "voltR": "226"
  }
  ```
- *Notes*:
  - Timestamps use `DD/MM/YYYY HH:mm` (non-standard format without timezone).
  - `kwh`, `kvah`, and `voltR` are strings.
  - The values represent cumulative meter register readings, not consumption deltas for each interval.

### 5. Signing Secret
```
GET /portal/keys
```
- Returns the signing secret required to sign bulk export requests:
  ```json
  {
    "data": {
      "signingSecret": "<signing-secret-token>"
    }
  }
  ```
  The secret is fetched dynamically by client code before exporting and passed to the WebCrypto signing routine.

### 6. Bulk Export (HMAC Signed)
```
GET /portal/export?page=1
```
In the portal UI, there is an "Export all meters" button located on the Transformers page (`/transformers`). Clicking it fires a request with two mandatory headers:
- `x-timestamp`: Current Unix timestamp in seconds (e.g. `1761584846`).
- `x-signature`: HMAC-SHA256 hex digest of the request string.

#### How the signature is constructed:
Looking into the client chunk `_app/immutable/nodes/4.2Bgc2kUI.js`, the signing function builds a plain text string separated by newlines:
```
METHOD\nPATH\nQUERY\nTIMESTAMP
```
For `GET /portal/export?page=1` at timestamp `1761584846`, the message is:
```
GET\n/portal/export\npage=1\n1761584846
```
The client then computes `HMAC-SHA256(secret, message)`. In Python:
```python
import hmac, hashlib

message = f"GET\n/portal/export\npage=1\n{timestamp}"
signature = hmac.new(
    secret.encode("utf-8"),
    message.encode("utf-8"),
    hashlib.sha256
).hexdigest()
```

#### Export Payload Details:
Even though the request includes `page=1`, the endpoint returns **all 403 meters** at once. Each record contains full hierarchy and geo data:
```json
{
  "meterId": "J100000",
  "serialNo": "SE33962",
  "make": "HPL",
  "phaseType": "single",
  "installStatus": "Decommissioned",
  "installType": "Whole Current",
  "build": "legacy",
  "dtCode": "DT-001",
  "hierarchy": {
    "zone": { "name": "Jaipur Zone 1", "code": "Z-01" },
    "circle": { "name": "Circle 1", "code": "C-01" },
    "division": { "name": "Division 1", "code": "D-01" },
    "subdivision": { "name": "Subdivision 1", "code": "SD-01" },
    "substation": { "name": "Substation 1", "code": "SS-01" },
    "feeder": { "name": "Feeder 1", "code": "F-001" },
    "dt": { "name": "Malviya Nagar DT 1", "code": "DT-001" }
  },
  "geo": { "lat": 26.938961, "lng": 75.830957 }
}
```

---

## 4. Single Meter Data & SvelteKit SSR

When viewing a meter detail page (`/meters/[id]`), the frontend does not hit `/portal/meters/{id}`. Instead, it pulls SvelteKit's route-level loader:
```
GET /meters/{meterId}/__data.json
```
This file uses SvelteKit's positional encoding format (an array of nodes where objects point to array indices).

Once decoded, meter details have two completely different formats depending on the `build` property:

### Format A: Legacy (`build: "legacy"` — 238 meters)
Attributes are stored as an array of key-value pairs:
```json
{
  "data": [
    { "parameterName": "Meter ID", "parameterValue": "J100000" },
    { "parameterName": "Serial No", "parameterValue": "SE33962" },
    { "parameterName": "Make", "parameterValue": "HPL" },
    { "parameterName": "Phase Type", "parameterValue": "single" },
    { "parameterName": "Installation Status", "parameterValue": "Decommissioned" },
    { "parameterName": "Installation Type", "parameterValue": "Whole Current" }
  ]
}
```

### Format B: V2 (`build: "v2"` — 165 meters)
Attributes are stored inside a stringified JSON blob under `classData`:
```json
{
  "classData": "{\"installed_meter\":{\"MeterId\":\"J100004\",\"SerialNo\":\"SE65293\",\"Make\":\"Genus\",\"PhaseType\":\"single\",\"InstallationStatus\":\"Faulty\",\"InstallationType\":\"CT Operated\"}}"
}
```
*Note*: `classData` must be parsed a second time with `json.loads` to get the actual meter attributes.

---

## 5. Network Hierarchy Breakdown

The portal groups all meters under a 7-tier distribution hierarchy:
```
Zone
 └── Circle
      └── Division
           └── Subdivision
                └── Substation
                     └── Feeder
                          └── Distribution Transformer (DT)
                               └── Meters
```

From analyzing the full export:
- 403 total meters across 3 Zones (Jaipur Zone 1, 2, and 3).
- 40 Distribution Transformers.
- 5 Manufacturers: HPL (90), Genus (88), Secure (85), Allied (76), L&T (64).
- 3 Installation Statuses: Installed (238), Faulty (90), Decommissioned (75).
- 2 Phases: Single (277), Three (126).
- 2 Build Types: Legacy (238), V2 (165).

---

## 6. Key Quirks & Implementation Notes

1. **Origin Header on Login**: Standard `curl -X POST` fails with 403 unless `Origin: https://urja-ops.flockenergy.tech` is passed.
2. **Double JSON in V2**: V2 meters have JSON embedded as an escaped string inside JSON. The parser must check for `classData` and decode it conditionally.
3. **Strings instead of Floats**: Geo coordinates (`latitude`, `longitude`) and power numbers (`kwh`, `kvah`) come back as string values.
4. **Timestamp Format**: The energy series uses `DD/MM/YYYY HH:mm` rather than ISO-8601.
5. **HMAC Key Exposure**: The signing key is publicly retrievable via `/portal/keys` once authenticated. It acts as an integrity check against URL tampering rather than an access secret.
6. **Export Location**: In the portal UI, the export button is placed on `/transformers` rather than the meters page.
7. **Coupling Limitations**: Single meter detail currently uses `/__data.json`. Because SvelteKit positional arrays are internal framework details, our parser includes bounds validation and circular reference guards, with graceful error mapping if the schema drifts. Fleet statistics cited above reflect observations taken during portal inspection.
