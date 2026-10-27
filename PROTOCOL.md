# PROTOCOL.md — How the Urja Meter Ops Portal Actually Works

This document describes the internal workings of the **Urja Meter Ops** portal at `https://urja-ops.flockenergy.tech`, as discovered through investigation.

---

## Technology Stack

| Layer | Technology |
|---|---|
| **Framework** | [SvelteKit](https://kit.svelte.dev/) (Svelte 5) with server-side rendering |
| **Authentication** | [Better Auth](https://www.better-auth.com/) — cookie-based sessions |
| **Hosting** | HTTPS with HTTP/2, `alt-svc: h3` (HTTP/3 ready) |
| **Styling** | Tailwind CSS |

The app is a SvelteKit application that renders server-side on first load, then hydrates into a client-side SPA. Data is fetched client-side via internal `/portal/*` JSON endpoints.

---

## Authentication

### Login

- **Endpoint:** `POST /login`
- **Content-Type:** `application/x-www-form-urlencoded`
- **Body:** `email=operator%40urja.local&password=urja-ops-2026`
- **CSRF Protection:** SvelteKit requires the `Origin` header to match the site origin. Without `Origin: https://urja-ops.flockenergy.tech`, the server returns `403 Cross-site POST form submissions are forbidden`.

### Session

- On successful login, the server sets a cookie:
  ```
  __Secure-better-auth.session_token=<token>; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax
  ```
- **TTL:** 1 hour (3600 seconds)
- The cookie is `HttpOnly` and `Secure` — it cannot be read by client-side JavaScript and is only sent over HTTPS.
- Session details can be queried at `GET /api/auth/get-session`, which returns:
  ```json
  {
    "session": { "expiresAt": "...", "token": "...", "userId": "...", "id": "..." },
    "user": { "name": "Ops Desk", "email": "operator@urja.local", ... }
  }
  ```

### Sign Out

- **Endpoint:** `POST /api/auth/sign-out`
- Invalidates the session server-side.

### Unauthenticated Requests

- All `/portal/*` endpoints return `401`:
  ```json
  {"error": "unauthorized", "message": "A valid session is required."}
  ```

---

## Data Endpoints

All data endpoints are under the `/portal/` path and return JSON. They require a valid session cookie.

### 1. Meter Search / List

```
GET /portal/meters/search?q={query}&page={page}
```

- **Search scope:** Matches against `meterId` and `serialNo` only (not make, status, or other fields)
- **Pagination:** 20 items per page
- **Empty query:** Returns all meters
- **Response:**
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

- **Pagination:** 20 items per page (40 total DTs = 2 pages)
- **Response:**
  ```json
  {
    "data": [
      {
        "code": "DT-001",
        "name": "Malviya Nagar DT 1",
        "feederCode": "F-001",
        "capacityKva": 100
      }
    ],
    "total": 40
  }
  ```

### 3. Meter Geolocation

```
GET /portal/meters/{meterId}/geo
```

- **Response:**
  ```json
  {
    "data": {
      "latitude": "26.938961002479868",
      "longitude": "75.83095696146852"
    }
  }
  ```
- **Quirk:** Coordinates are returned as **strings**, not numbers.

### 4. Meter Energy / Consumption

```
GET /portal/meters/{meterId}/energy
```

- **Returns:** ~337 readings per meter (approximately 7 days of 30-minute intervals)
- **Response:**
  ```json
  {
    "data": [
      {
        "timestamp": "23/06/2026 23:30",
        "kwh": "48438.74",
        "kvah": "52313.84",
        "voltR": "226"
      }
    ]
  }
  ```
- **Quirks:**
  - Timestamps are in `DD/MM/YYYY HH:mm` format (non-ISO)
  - All numeric values (`kwh`, `kvah`, `voltR`) are **strings**
  - Values represent **cumulative** readings, not interval deltas

### 5. Signing Secret

```
GET /portal/keys
```

- Returns the HMAC signing secret used for the export endpoint:
  ```json
  {
    "data": {
      "signingSecret": "I3dZPPf5CgTp7JyGNMI8i6z8LFR7TmSR"
    }
  }
  ```

### 6. Bulk Export (HMAC-Signed)

```
GET /portal/export?page=1
```

**Requires HMAC signature in headers:**

| Header | Value |
|---|---|
| `x-timestamp` | Current Unix timestamp (seconds) |
| `x-signature` | HMAC-SHA256 hex digest |

**Signing algorithm** (reconstructed from client JS):
```
message = "GET\n/portal/export\npage=1\n{timestamp}"
signature = HMAC-SHA256(signingSecret, message).hexdigest()
```

The signing is computed in the browser using the Web Crypto API (`crypto.subtle`).

- **Despite the `page` parameter**, this endpoint returns **all 403 meters** in a single response.
- **Response** includes enriched data not available in the search endpoint:
  ```json
  {
    "data": [
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
    ],
    "total": 403
  }
  ```

---

## SvelteKit Server-Load Data

Individual meter detail pages use SvelteKit's server-load pattern:

```
GET /meters/{meterId}/__data.json
```

This returns data in SvelteKit's compact positional encoding, where objects reference values by index in a flat array. The data includes:

- **Nameplate parameters** (in two formats based on `build` type)
- **Network hierarchy** (Zone through DT)

### Two Meter Data Formats

**Legacy format** (`build: "legacy"`, 238 meters):
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

**V2 format** (`build: "v2"`, 165 meters):
```json
{
  "classData": "{\"installed_meter\":{\"MeterId\":\"J100004\",\"SerialNo\":\"SE65293\",\"Make\":\"Genus\",\"PhaseType\":\"single\",\"InstallationStatus\":\"Faulty\",\"InstallationType\":\"CT Operated\"}}"
}
```

Note: The `classData` value is a **JSON string inside JSON** — it must be double-parsed.

---

## Route Map

Discovered from the SvelteKit client manifest:

| Route | Node IDs | Purpose |
|---|---|---|
| `/` | — | Redirects to `/login` (or `/meters` if authenticated) |
| `/login` | [0, 7] | Login form |
| `/(portal)/meters` | [0, 2, 5] | Meter search/list page |
| `/(portal)/meters/[id]` | [0, 2, 6] | Individual meter detail page |
| `/(portal)/transformers` | [0, 2, 4] | Distribution transformers page |

Node 0 = root layout, Node 2 = portal layout (nav bar, sign-out), others = page components.

---

## Data Summary

| Entity | Count | Notes |
|---|---|---|
| **Meters** | 403 | Across 3 zones, 40 DTs |
| **DTs** | 40 | 2 pages of 20 |
| **Zones** | 3 | Jaipur Zone 1, 2, 3 |
| **Manufacturers** | 5 | HPL (90), L&T (64), Genus (88), Allied (76), Secure (85) |
| **Statuses** | 3 | Installed (238), Faulty (90), Decommissioned (75) |
| **Phase Types** | 2 | Single (277), Three (126) |
| **Install Types** | 2 | Whole Current (205), CT Operated (198) |
| **Build Types** | 2 | Legacy (238), V2 (165) |

---

## Network Hierarchy (7 levels)

```
Zone
 └── Circle
      └── Division
           └── Subdivision
                └── Sub Station
                     └── Feeder
                          └── DT (Distribution Transformer)
                               └── Meters
```

Each meter belongs to exactly one DT, which sits in a chain up to a Zone.

---

## Quirks and Surprises

1. **CSRF via Origin header** — SvelteKit rejects POST requests without a matching `Origin` header (returns 403 with "Cross-site POST form submissions are forbidden").

2. **Two data formats in the same system** — legacy meters use flat key-value arrays while v2 meters use stringified JSON (`classData`). The client-side JS handles both in a single component.

3. **Export returns all data at once** — despite accepting a `page` parameter, the export endpoint ignores pagination and returns all 403 meters. This is actually convenient for bulk access.

4. **Strings everywhere** — geo coordinates, energy readings (kwh, kvah, voltR) are all returned as strings, requiring type conversion.

5. **Non-standard timestamp format** — energy readings use `DD/MM/YYYY HH:mm` instead of ISO-8601.

6. **The signing secret is fetchable** — the HMAC secret for the export endpoint is available at `/portal/keys` to any authenticated user. The HMAC signing provides request integrity (preventing tampering) but not access control (which is handled by the session cookie).

7. **No rate limiting** — no observable rate limits on any endpoint during testing.

8. **The "Export all meters" button is on the Transformers page** — an unusual UX choice; you'd expect it on the Meters page.

