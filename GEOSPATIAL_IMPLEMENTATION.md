# MSR geospatial integration

## Phase 1 — local workspace and v3 transport (implemented)

Branch: `feature/geospatial-dashboard`. This branch exists in backend and
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

Do not put credentials in frontend settings or commit them. Phase 3 registers
the dedicated connector; it remains disabled until configured. Tokens need the MSR v3
location/geospatial read abilities and the corresponding location policies.

Seven mocked transport tests pass, run after Django initialization:

```bash
cd ../openimis-be_py/openIMIS
../.venv/bin/python manage.py shell -c 'import unittest; suite = unittest.defaultTestLoader.loadTestsFromName("msr_etl.tests.test_msr_v3_geo_client"); result = unittest.TextTestRunner(verbosity=2).run(suite); raise SystemExit(not result.wasSuccessful())'
```

Django `manage.py check` reports no issues. The scheduled-task registration
warning about `int(None)` was fixed in Phase 3 by handling configuration defaults
before app initialization completes. No migrations were applied by this
phase. There has been no authenticated live v3 synchronization or map display.

## Phase 2 — stable references and persistence (implemented)

Implemented in the local backend location repository: `MsrLocationReference`,
migration `0033_msr_location_reference`, and `MsrReferenceWriter`. See that
repository's `GEOSPATIAL_REFERENCES.md` for usage, matching rules and tests.
Fifteen persistence tests pass against an isolated PostgreSQL schema, exercising
the actual migration. Django checks and migration drift checks pass. Application
migrations remain unapplied; no live synchronization has occurred.

The writer links existing active CoreMIS records by unambiguous code and parent
scope. Unmatched references remain unlinked. It does not create CoreMIS locations
or infer catchment membership. GraphQL exposure belongs to Phase 4.

The MSR resource fields were verified against local source; live fixtures and
membership responses still require verification. The reference model is unique
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

## Phase 3 — background synchronization and import UI (implemented)

The location-only `msr_geo_v3` connector now feeds district-scoped background
staging through `MsrGeoSource` → `MsrGeoAdapter` → `MsrGeoSink`. Each unit is a
complete district snapshot: district, TAs, GVHs, villages and catchments, followed
by all pages of the verified membership endpoint:
`GET /api/v2/micro-catchments/{catchmentId}/villages?district_id=13`.

The dedicated writer persists references and memberships together. Failed fetches
do not become empty snapshots. Failed writes roll back before retry status is
recorded; retries reuse staged payloads. Signed URLs are removed before staging.
Snapshots bind to their original MSR instance, preventing source configuration
changes from redirecting staged identities. A `GEO_DISTRICT` staging type keeps
MSR IDs distinct from legacy location codes in sync logs.

Location migration `0034_msrcatchmentvillage` adds explicit verified memberships
between MSR references. Moves and authoritative empty responses replace only
the affected catchments atomically. This does not infer village coverage from
GVHs, repurpose CoreMIS TA/GVH associations, or infer polygon containment.
MSR ETL migration `0005_alter_msretlsyncunit_unit_type` depends on that schema.

The import UI exposes source-specific filters even before CoreMIS locations exist,
requires a positive integer MSR district ID, shows source guidance, and hides the
legacy full-country Initial Pull action when v3 is selected. Existing progress
and sync logs remain in use. No credentials are included in source-type queries.

Activation:

1. Review `manage.py migrate --plan` and apply pending migrations with
   `manage.py migrate` using the backend virtual environment.
2. Merge this entry into the existing backend `msr_etl` ModuleConfiguration
   `sources` object, keeping household sources and their endpoints intact:

   ```json
   "msr_geo_v3": {
     "enabled": true,
     "base_url": "http://localhost:8080",
     "source_instance": "msr-malawi",
     "auth_bearer_token": "<backend-held service token>"
   }
   ```

   Connector, kind and district filter defaults come from `DEFAULT_CONFIG`.
   The token needs access to both v3 publications and v2 micro-catchment villages.
3. Restart backend workers and rebuild the local fe-msr_etl module. In Import Data
   → Location select MSR geospatial publications, enter MSR district ID `13`, and
   choose Pull Data. Use progress and View sync log to inspect the `GEO_DISTRICT`
   unit. Reference and verified membership counts can be checked in Django.

Verification: 95 backend tests pass in a temporary, rolled-back PostgreSQL schema,
including legacy staging/jobs regressions, source scheduling, retry rollback,
membership replacement and source-instance binding. Both location reference and
membership migrations pass forward/reversal checks. Frontend tests and the local
module build pass (existing external `flat` dependency warning remains).

Application migrations and credential configuration were not changed. No live
authenticated MSR synchronization or browser session has been demonstrated.
Unmatched references remain available for later review, rather than creating
CoreMIS locations automatically. Map/GraphQL exposure remains Phase 4.

Live acceptance pending: one district (MSR ID 13, code 213 where verified) and one
explicitly matched micro-catchment synchronize idempotently with verified village
memberships, retries and job progress.

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
