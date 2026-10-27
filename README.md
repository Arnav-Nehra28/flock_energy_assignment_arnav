# Urja Meter Ops API Service

A clean, modern REST API proxy and data normalization service built over the legacy **Urja Meter Ops** internal portal (`https://urja-ops.flockenergy.tech`).

Designed for product and data engineering teams who require programmatic, reliable access to smart-meter telemetry, distribution transformer hierarchies, and 30-minute interval consumption data without manual portal interactions.

---

## Deliverables & Documentation Index

- **Protocol & Reverse Engineering Documentation**: [`PROTOCOL.md`](PROTOCOL.md) — Comprehensive explanation of how the portal works under the hood (SvelteKit SSR, Better Auth cookies, CSRF requirements, HMAC-SHA256 bulk export, internal routes, and polymorphic data formats).
- **OpenAPI 3.1 Specification**: [`openapi.json`](openapi.json) — Full machine-readable API definition. Can be imported directly into Postman, Insomnia, or code generators.
- **Engineering Reflection**: [`REFLECTION.md`](REFLECTION.md) — Detailed answers to the 5 evaluation questions (assumptions, toughest challenges, self-criticism, mistakes, and future improvements).

---

## Architectural Highlights

```
┌─────────────────────────────────┐
│     Client Applications         │
│  (Data pipelines, Dashboards)   │
└────────────────┬────────────────┘
                 │ HTTP / JSON
                 ▼
┌────────────────────────────────────────────────────────┐
│             Urja Meter Ops API (FastAPI)               │
│                                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │    Meters    │  │ Transformers │  │  Hierarchy   │  │
│  │   Endpoints  │  │   Endpoints  │  │  Tree (7-lvl)│  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                 │          │
│         └────────────┬────┴─────────────────┘          │
│                      ▼                                 │
│          ┌───────────────────────┐                     │
│          │ In-Memory TTL Cache   │                     │
│          └───────────┬───────────┘                     │
│                      ▼                                 │
│          ┌───────────────────────┐                     │
│          │ Portal Client Service │                     │
│          │  - Better Auth Session│                     │
│          │  - HMAC-SHA256 Signer │                     │
│          │  - Auto-Reauth Worker │                     │
│          └───────────┬───────────┘                     │
└──────────────────────┼─────────────────────────────────┘
                       │ HTTPS (HTTP/2)
                       ▼
┌────────────────────────────────────────────────────────┐
│     Urja Meter Ops Portal (Legacy SvelteKit App)       │
│           https://urja-ops.flockenergy.tech            │
└────────────────────────────────────────────────────────┘
```

### Key Capabilities
1. **Automated Session Lifecycle**: Handles CSRF headers (`Origin`), negotiates session cookies with Better Auth (`__Secure-better-auth.session_token`), tracks 1-hour expiration with proactive renewal, and transparently re-authenticates on HTTP 401.
2. **Reverse-Engineered HMAC Bulk Export**: Fetches dynamic signing secrets from `/portal/keys`, computes cryptographic HMAC-SHA256 signatures over timestamped requests, and exposes the full 403-meter dataset.
3. **Data Normalization & Polymorphic Schema Handling**: Normalizes both `legacy` (flat parameter arrays) and `v2` (nested stringified `classData` JSON) schemas into unified Pydantic models.
4. **Network Hierarchy Reconstruction**: Rebuilds the complete 7-tier distribution network hierarchy:
   $$\text{Zone} \rightarrow \text{Circle} \rightarrow \text{Division} \rightarrow \text{Subdivision} \rightarrow \text{Substation} \rightarrow \text{Feeder} \rightarrow \text{Distribution Transformer (DT)}$$
   Aggregates live meter counts across all branches.
5. **Local Query & Index Layer**: Enables faceted filtering on attributes the portal cannot filter (e.g. status, manufacturer make, phase, DT, and zone) with sub-millisecond in-memory caching.
6. **Type Safety & Interactive Docs**: Fully typed with Pydantic v2 and serves auto-generated interactive documentation via **Swagger UI** (`/docs`) and **ReDoc** (`/redoc`).

---

## Project Structure

```
flock_energy_assignment/
├── app/
│   ├── __init__.py
│   ├── config.py              # Environment configuration & defaults
│   ├── main.py                # FastAPI app initialization, middleware & routes
│   ├── models.py              # Pydantic v2 domain schemas & enums
│   ├── portal_client.py       # Async HTTP client, session manager & HMAC signer
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── hierarchy.py       # 7-level network hierarchy endpoint
│   │   ├── meters.py          # Meter search, details, geo, consumption & export
│   │   └── transformers.py   # Distribution transformer endpoints
│   └── services/
│       ├── __init__.py
│       ├── cache.py           # In-memory TTL cache with key invalidation
│       ├── meter_service.py   # Business logic, decoders & hierarchy tree builder
│       └── transformer_service.py # Transformer retrieval & pagination
├── tests/
│   ├── test_units.py          # Unit tests (models, cache, HMAC, parsers)
│   └── test_api.py            # End-to-end integration tests
├── openapi.json               # OpenAPI 3.1.0 specification
├── PROTOCOL.md                # Portal reverse-engineering documentation
├── REFLECTION.md              # Engineering reflection & analysis
├── pytest.ini                 # Pytest configuration
├── requirements.txt           # Python dependencies
└── README.md                  # Front-door documentation
```

---

## Installation & Setup

### Prerequisites
- Python 3.10+ (tested on Python 3.12)
- Network access to `https://urja-ops.flockenergy.tech`

### 1. Clone & Set Up Virtual Environment

```bash
cd flock_energy_assignment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration (Optional)

The application works out-of-the-box with default portal credentials. You can override settings via environment variables or a `.env` file:

```bash
# Optional overrides (defaults shown)
export URJA_PORTAL_BASE_URL="https://urja-ops.flockenergy.tech"
export URJA_PORTAL_EMAIL="operator@urja.local"
export URJA_PORTAL_PASSWORD="urja-ops-2026"
export URJA_CACHE_TTL_SECONDS=300
export URJA_EXPORT_CACHE_TTL_SECONDS=600
```

---

## Running the API Service

Start the local API server with Uvicorn:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Once started:
- **Interactive Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc Documentation**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Raw OpenAPI Spec**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)
- **Service Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

---

## Running Tests

Execute the automated test suite (16 unit and integration tests):

```bash
pytest -v
```

Output:
```
tests/test_api.py::test_health_endpoint PASSED
tests/test_api.py::test_search_meters PASSED
tests/test_api.py::test_meter_detail_legacy_and_v2 PASSED
tests/test_api.py::test_meter_consumption PASSED
tests/test_api.py::test_meter_location PASSED
tests/test_api.py::test_transformers_list PASSED
tests/test_api.py::test_export_and_filtering PASSED
tests/test_api.py::test_hierarchy_tree PASSED
tests/test_units.py::test_meter_summary_model PASSED
tests/test_units.py::test_cache_ttl PASSED
tests/test_units.py::test_cache_invalidation PASSED
tests/test_units.py::test_parse_energy_timestamp PASSED
tests/test_units.py::test_normalise_nameplate_legacy PASSED
tests/test_units.py::test_normalise_nameplate_v2 PASSED
tests/test_units.py::test_decode_sveltekit_data PASSED
tests/test_units.py::test_hmac_computation PASSED

============================== 16 passed in 5.86s ==============================
```

---

## API Reference & Sample Requests

### 1. Health & Portal Status
Check API status and verify the active authenticated session with the legacy portal.

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

---

### 2. Search & List Meters
Search meters by identifier or serial number with pagination.

```bash
# List first page of meters
curl -s "http://localhost:8000/api/meters?page=1" | jq .

# Search specific serial number
curl -s "http://localhost:8000/api/meters?q=SE33962" | jq .
```

---

### 3. Full Meter Detail (Legacy & V2 Support)
Fetch unified nameplate specifications, network placement, and geographic coordinates.

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

---

### 4. Normalized Consumption Readings
Retrieve 30-minute interval readings converted to ISO-8601 timestamps and numeric values.

```bash
curl -s http://localhost:8000/api/meters/J100000/consumption | jq '.[0:3]'
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
  },
  {
    "timestamp": "2026-06-24T00:30:00",
    "kwh": 48439.58,
    "kvah": 52314.75,
    "volt_r": 220
  }
]
```

---

### 5. Bulk Export with Local Filtering Layer
Leverages the HMAC-signed export route and provides multi-attribute server-side filtering.

```bash
# Filter all Faulty meters made by Genus
curl -s "http://localhost:8000/api/meters/export?status=Faulty&make=Genus" | jq '{total: .total, sample: .data[0]}'

# Filter three-phase meters on transformer DT-006
curl -s "http://localhost:8000/api/meters/export?phase=three&dt_code=DT-006" | jq .
```

---

### 6. Distribution Transformers
List transformers with feeder relationships and capacity (kVA).

```bash
curl -s "http://localhost:8000/api/transformers?page=1" | jq .
```

---

### 7. Reconstructed Network Hierarchy Tree
Returns the complete 7-level structural tree with meter counts at every tier.

```bash
curl -s http://localhost:8000/api/hierarchy | jq '.[0] | {name, code, meter_count, child_circles: .children | length}'
```
```json
{
  "name": "Jaipur Zone 1",
  "code": "Z-01",
  "meter_count": 135,
  "child_circles": 13
}
```

---

## Design Decisions & Trade-Offs

| Decision | Trade-Off | Rationale |
|---|---|---|
| **FastAPI + Async HTTPX** | Requires asynchronous async/await codebase | High throughput, non-blocking I/O against legacy HTTP/2 endpoints, and automatic OpenAPI schema generation. |
| **Pydantic v2 Envelopes** | Stricter validation overhead on ingestion | Normalizes messy legacy strings (`"latitude": "26.938..."`, `"24/06/2026 00:00"`) into strictly-typed numbers and ISO-8601 strings. |
| **Dual Ingestion Strategy** (Search vs. Export) | Maintaining two code paths for meter data | Search endpoint is fast for single lookups; the HMAC-signed bulk endpoint is used for exports, filtering, and hierarchy aggregation. |
| **In-Memory TTL Cache** | Not shared across multiple horizontally-scaled processes | Zero third-party infrastructure dependencies (no Redis requirement to run locally), simple to test and evaluate. Ready to swap for Redis via dependency injection. |

---

## What Was Intentionally Skipped

1. **Write / Mutation Operations**: The assignment explicitly directed that the legacy portal be treated as read-only. We did not probe or expose any update/write operations.
2. **Persistent RDBMS / Database Layer**: We avoided requiring PostgreSQL, MongoDB, or Redis to ensure any reviewer can clone the repository and run `pytest` or `uvicorn` in under 30 seconds with no background services.
3. **Complex Geospatial Indexing (R*Tree / PostGIS)**: For 403 meters, in-memory array filtering executes in $<1$ ms. Full GIS indexing was omitted as premature optimization.

---

## Reflection Summary

*(Full text in [`REFLECTION.md`](REFLECTION.md))*

- **Hardest Part**: Cracking the HMAC-SHA256 signature required for the `/portal/export` endpoint by decompiling the minified client bundle `nodes/4.2Bgc2kUI.js`.
- **Mistake Made**: SvelteKit initially rejected form POST logins with `403 Forbidden` until we determined it strictly enforces the HTTP `Origin` header for CSRF protection.
- **Key Self-Critique**: In-memory caching should be backed by Redis in production to prevent repeated portal logins across multi-worker clusters. Additionally, interval consumption readings should compute deltas ($\Delta \text{kWh}$) rather than only presenting cumulative meter registers.
