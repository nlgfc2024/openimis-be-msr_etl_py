# MSR geospatial integration

## Phase 1 — local workspace and v3 transport (implemented)

Branch: `feature/msr-geospatial-dashboard`. This branch exists in backend and
frontend location, MSR ETL, project, and assembly repositories. Existing assembly
configuration edits were retained. Payroll repositories were not changed.

Backend location, MSR ETL and project modules were installed with `pip install
--no-deps --no-build-isolation -e ...` using the assembly virtual environment.
Frontend assembly already declares local `file:` dependencies for location,
MSR ETL, payroll and project. Location and MSR ETL runtime symlinks resolve to
their local source directories. Project and payroll's existing runtime links/
checkouts were preserved. Location, MSR ETL and project local builds pass; Rollup
reports external dependency warnings (`rc-cascader`, `flat`, `material-table`).
These builds do not establish that the entire frontend application bundles.

`msr_etl.sources.msr_v3_geo.MsrV3GeoClient` provides separate Bearer-authenticated
GET requests for districts and district-scoped catchments. It follows pagination,
retains all raw publication fields, rejects pagination outside the configured
endpoint, refuses redirects, and fails explicitly on HTTP/schema errors. It does
not change household source configuration or write records yet.

The list envelope (`data`, `meta`, `links.next`) was checked against the local
MSR `app/Http/Api/Controllers/v3/GeoLocationController.php`. MSR is at
`http://localhost:8080`; CoreMIS backend is port 8000 and frontend port 3000.
MSR source was inspected read-only.

Add a separate `msr_geo_v3` entry to the backend MSR ETL ModuleConfiguration
`sources` object, retaining all existing source entries:

```json
{
  "base_url": "http://localhost:8080",
  "auth_type": "bearer",
  "auth_bearer_token": "<backend-held service token>",
  "timeout_seconds": 60,
  "retry_total": 3,
  "verify_ssl": true
}
```

Do not put credentials in frontend settings or commit them. This entry is for
the transport client only: the UI connector is intentionally not registered
until the dedicated persistence/staging path exists. Tokens need the MSR v3
location/geospatial read abilities and the corresponding location policies.

Seven mocked transport tests pass, run after Django initialization:

```bash
cd ../openimis-be_py/openIMIS
../.venv/bin/python manage.py shell -c 'import unittest; suite = unittest.defaultTestLoader.loadTestsFromName("msr_etl.tests.test_msr_v3_geo_client"); result = unittest.TextTestRunner(verbosity=2).run(suite); raise SystemExit(not result.wasSuccessful())'
```

Django `manage.py check` now reports no issues. Startup separately logs an MSR
ETL scheduled-task registration warning about `int(None)`; this must be diagnosed
before background synchronization acceptance. No migrations were applied by this
phase. There has been no authenticated live v3 synchronization or map display.

## Phase 2 — stable references and persistence

Verify the MSR resource fields and membership response against source and live
fixtures. Introduce a shared location MSR reference model and migration, unique
by configured source instance + original MSR type + original MSR ID. Keep MSR
code and CoreMIS primary key separate. Link to an existing active Location or
MicroCatchment only on an unambiguous match; never select a duplicate arbitrarily.

Preserve the existing district→R, TA→D, GVH→W, village→V mapping. Wards and other
MSR types stay explicitly typed references rather than being coerced into GVHs.
Persist parents, coordinates, classification, geometry relation, hash, revision,
source and availability metadata. Distinguish omitted properties from explicit
nulls. Do not use signed download URLs as stable identities.

Acceptance: transactional repeated fixture imports update the same references;
parents import first; failures roll back; cross-source IDs cannot collide;
removal clears geometry while delivery failure preserves location identity.

## Phase 3 — background synchronization and import UI

Add the v3 adapter and dedicated writer, then register a location-only connector.
Extend background staging and retry dispatch, which currently explicitly use the
legacy writer. Import membership through verified MSR relationships, never polygon
containment. Preserve legacy household endpoints and sources.

Acceptance: one district (MSR ID 13, code 213 where verified) and one explicitly
matched micro-catchment synchronize idempotently; retries and job progress work.

## Phase 4 — shared location map and project reuse

Expose references through permission-scoped GraphQL, with server-side URL refresh.
Build the shared React map in fe-location and reuse it in the project module.
Label source-area associations accurately, refresh expired URLs and remove stale
layers after replacement/removal. Keep delivery failures distinguishable from
removal. Use authorized programme enrolment records with validated coordinates
for beneficiary markers. Boundary metadata supplies no beneficiary coordinates.

Acceptance: the pilot district and micro-catchment display in CoreMIS; unauthorized
users cannot see restricted references or beneficiary coordinates. Confirm browser
CORS and signed URL host/port reachability against localhost:3000.

## Phase 5 — lifecycle demonstration

Demonstrate publication → sync → display, replacement, removal, restoration,
signed-link expiry/refresh, and file delivery failure using the pilot. Record
expected and observed revisions and hashes. Run backend tests, frontend module
builds, assembly build and permission checks before expanding coverage.
