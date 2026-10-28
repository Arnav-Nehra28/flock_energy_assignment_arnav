# Urja Meter Ops API

A REST API wrapper around the internal **Urja Meter Ops** portal (`https://urja-ops.flockenergy.tech`).

The legacy portal doesn't have a public or documented API, has weird session requirements, and serves data in inconsistent formats across different meter generations. This project sits between the portal and downstream services: it handles login/session renewal, decodes SvelteKit data payloads, signs HMAC export requests, cleans up the schema differences, and serves clean JSON endpoints with type validation and caching.

## Documentation

- **Reverse Engineering Notes**: [`PROTOCOL.md`](PROTOCOL.md) — How the Urja portal works under the hood (CSRF requirements, Better-Auth cookies, HMAC signing on export, internal SvelteKit endpoints, and quirks).
- **Reflection**: [`REFLECTION.md`](REFLECTION.md) — Engineering reflection covering assumptions, challenges, mistakes, and what I would do with more time.
- **OpenAPI Spec**: [`openapi.json`](openapi.json) — OpenAPI 3.1 specification (also available interactively at `/docs` when running).

---

## Quick Start

### Prerequisites
- Python 3.10+ (tested on Python 3.12)
- Outbound HTTPS access to `https://urja-ops.flockenergy.tech`

### 1. Setup

```bash
git clone git@github.com:Arnav-Nehra28/flock_energy_assignment_arnav.git
cd flock_energy_assignment_arnav

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration (Optional)

The service works out of the box using default portal credentials (`operator@urja.local` / `urja-ops-2026`). If needed, you can override settings with environment variables or a `.env` file:

```bash
export URJA_PORTAL_BASE_URL="https://urja-ops.flockenergy.tech"
export URJA_PORTAL_EMAIL="operator@urja.local"
export URJA_PORTAL_PASSWORD="urja-ops-2026"
export URJA_CACHE_TTL_SECONDS=300
export URJA_EXPORT_CACHE_TTL_SECONDS=600
```

### 3. Run the Server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Once running:
- **Swagger Docs**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json
- **Health Check**: http://localhost:8000/health

### 4. Run Tests

```bash
pytest -v
```

All 17 unit and integration tests hit the local models, cache logic, parsers, HMAC generation, and mock/live API endpoints.

---

## Teammate Handover & Architecture Guide

If you're picking up this codebase to fix a bug or add a feature, here is the mental model:

### Request Lifecycle

```
 Client (cURL / Frontend / Pipeline)
                 │
                 ▼
       FastAPI API Routers (app/routers/)
   ├── meters.py (search, single meter, consumption, export)
   ├── transformers.py (transformer list)
   └── hierarchy.py (7-level grid tree)
                 │
                 ▼
       Service Layer (app/services/)
   ├── cache.py (in-memory TTL cache with key invalidation)
   ├── meter_service.py (schema normalization, tree building, filtering)
   └── transformer_service.py (DT pagination)
                 │
                 ▼
       Portal Client (app/portal_client.py)
   ├── Better-Auth session + Origin header for CSRF
   ├── HMAC-SHA256 signer using secret from /portal/keys
   └── SvelteKit __data.json positional decoder
                 │
                 ▼
  Urja Meter Ops (https://urja-ops.flockenergy.tech)
```

### "Which file do I edit?" Cheat Sheet

- **Adding or modifying an API endpoint**: Check `app/routers/` and define the response schema in `app/models.py`.
- **Portal changed its auth or signing mechanism**: Look at `app/portal_client.py` (`login()`, `_sign_request()`).
- **Found a new meter data format or want to calculate consumption deltas**: Look at `app/services/meter_service.py` (`_normalise_nameplate()`, `get_meter_consumption()`).
- **Adjusting cache durations or swapping cache backend**: Check `app/config.py` (TTL constants) and `app/services/cache.py` (replace with Redis if scaling horizontally).
- **Adding unit or integration tests**: Add cases to `tests/test_units.py` (offline, pure functions) or `tests/test_api.py` (FastAPI test client against live portal).

### 3 Quirks You Need to Know

1. **SvelteKit CSRF (`Origin` header)**: The portal rejects form POSTs unless `Origin: https://urja-ops.flockenergy.tech` is present. If you ever rewrite login requests or make direct calls, don't forget this header or you'll get a silent 403.
2. **HMAC Signing Format**: Bulk export requires signing `METHOD\nPATH\nQUERY\nTIMESTAMP` using the secret from `/portal/keys`. The signature is passed in `x-signature` alongside `x-timestamp`.
3. **Polymorphic Meter Schemas**: Old meters (`build: "legacy"`) store attributes in an array of `{parameterName, parameterValue}`. Newer meters (`build: "v2"`) store them inside an escaped JSON string under `classData`. `meter_service.py` handles both transparently.

---

## Example Requests

### Health & Portal Connectivity
```bash
curl -s http://localhost:8000/health | jq .
```
```json
{
  "status": "healthy",
  "portal_connected": true,
  "portal_session": {
    "user": "operator@urja.local",
    "expires_at": "2026-10-27T17:57:28.164Z"
  }
}
```

### Search & List Meters
```bash
# First page
curl -s "http://localhost:8000/api/meters?page=1" | jq .

# Search by serial number or meter ID
curl -s "http://localhost:8000/api/meters?q=SE33962" | jq .
```

### Meter Detail (handles legacy and v2 automatically)
```bash
curl -s http://localhost:8000/api/meters/J100000 | jq .
```
```json
{
  "meter_id": "J100000",
  "nameplate": {
    "meter_id": "J100000",
    "serial_no": "SE33962",
    "make": "HPL",
    "phase_type": "single",
    "install_status": "Decommissioned",
    "install_type": "Whole Current"
  },
  "hierarchy": {
    "zone": { "name": "Jaipur Zone 1", "code": "Z-01" },
    "circle": { "name": "Circle 1", "code": "C-01" },
    "division": { "name": "Division 1", "code": "D-01" },
    "subdivision": { "name": "Subdivision 1", "code": "SD-01" },
    "substation": { "name": "Substation 1", "code": "SS-01" },
    "feeder": { "name": "Feeder 1", "code": "F-001" },
    "dt": { "name": "Malviya Nagar DT 1", "code": "DT-001" }
  },
  "location": {
    "latitude": 26.938961002479868,
    "longitude": 75.83095696146852
  }
}
```

### Meter Consumption (converted to ISO-8601 & floats)
```bash
curl -s http://localhost:8000/api/meters/J100000/consumption | jq '.[0:2]'
```
```json
[
  {
    "timestamp": "2026-06-23T23:30:00",
    "kwh": 48438.74,
    "kvah": 52313.84,
    "volt_r": 226
  },
  {
    "timestamp": "2026-06-24T00:00:00",
    "kwh": 48439.16,
    "kvah": 52314.29,
    "volt_r": 229
  }
]
```

### Bulk Export & Multi-Field Filtering
The legacy portal only searches by ID or serial. Our export endpoint pulls the HMAC snapshot and allows filtering across any combination of status, manufacturer make, phase type, DT code, or zone:

```bash
# Find faulty Genus meters
curl -s "http://localhost:8000/api/meters/export?status=Faulty&make=Genus" | jq '{total: .total, sample: .data[0]}'

# Find three-phase meters under transformer DT-006
curl -s "http://localhost:8000/api/meters/export?phase=three&dt_code=DT-006" | jq .
```

### Transformers List
```bash
curl -s "http://localhost:8000/api/transformers?page=1" | jq .
```

### Network Hierarchy Tree
Returns the nested 7-level structure starting from Zones down to Transformers, including live meter counts at each level:
```bash
curl -s http://localhost:8000/api/hierarchy | jq '.[0] | {name, code, meter_count, child_circles: .children | length}'
```

---

## Project Structure

```
flock_energy_assignment/
├── app/
│   ├── config.py              # Settings from environment or defaults
│   ├── main.py                # FastAPI app setup, routes, error handling
│   ├── models.py              # Pydantic schemas and models
│   ├── portal_client.py       # Async client for Urja portal (auth, HMAC, HTTP)
│   ├── routers/
│   │   ├── meters.py          # Search, single meter, consumption, export
│   │   ├── transformers.py   # Transformer list endpoints
│   │   └── hierarchy.py       # 7-level grid hierarchy endpoint
│   └── services/
│       ├── cache.py           # Simple in-memory TTL cache
│       ├── meter_service.py   # Schema decoders, export caching, tree builder
│       └── transformer_service.py
├── tests/
│   ├── test_units.py          # Unit tests (models, cache, HMAC, parsers)
│   └── test_api.py            # API integration tests
├── PROTOCOL.md                # Portal reverse-engineering notes
├── REFLECTION.md              # Reflection questions
├── openapi.json               # Exported OpenAPI 3.1 specification
├── requirements.txt
└── README.md
```

---

## Design Choices & Trade-offs

- **In-Memory Cache vs Redis**: For 403 meters, in-memory caching keeps everything fast, simple, and self-contained with no external dependencies needed to run or review the assignment. In a multi-worker production setup, this would be replaced with Redis.
- **FastAPI + HTTPX**: Asynchronous all the way through, which makes concurrent requests to the portal non-blocking and generates interactive OpenAPI docs automatically.
- **Bulk Export vs Page-by-Page Crawling**: The HMAC export endpoint returns the entire fleet in one go. Using this snapshot to power filtering and hierarchy reconstruction avoids hundreds of individual HTTP requests.
- **Read-Only Scope**: In line with the assignment instructions, no mutating endpoints were created or attempted against the portal.
