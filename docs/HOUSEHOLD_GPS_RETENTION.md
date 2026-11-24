# Household GPS retention

## Scope

MSR household coordinates continue to come from the existing production
`POST https://malawiubr.org/api/v2/get_households_data` source. This change does
not switch household imports to localhost or change the configured endpoint,
filters, pagination, household codes or member matching. Local MSR v3 remains
the separately configured boundary and verified-membership source.

An authenticated production response was checked on 24 November 2026. Its first
complete household contained populated `gps_latitude`, `gps_longitude`,
`gps_accuracy` and `gps_elevation`. This was a field-availability sample, not an
audit of every production household or coordinate accuracy.

## Existing CoreMIS facilities reused

The UBR adapter extracts the current GPS fields into an internal household-only
payload. The sink removes that payload before producing the existing member CSV,
so GPS does not require new individual schema fields and is not copied onto
individual records.

A small subclass of the existing `IndividualImportService` attaches one GPS
payload per household code to the upload's existing `json_ext`, before triggering
the existing workflow. GPS follows the normal host `GroupDataSource` lifecycle:
when an accepted group data source is linked to a household, an MSR ETL receiver
merges GPS into that household's existing `json_ext.household_gps`. Pending group
proposals do not publish GPS to the household. Approval-based workflows retain
the upload metadata until the normal approval links the household.

Existing household metadata, membership and project links are preserved. The
write uses the host model's audited `save(user=...)`; no household is created by
the GPS receiver. Existing workflows must link accepted `GroupDataSource` rows
through their normal model save, as the current CoreMIS workflow does. Custom
workflows that bypass this lifecycle need a separate integration review.

## Stored contract

`household_gps` contains:

| Key | Meaning |
| --- | --- |
| `latitude`, `longitude` | Finite WGS84 coordinate pair from current household GPS fields |
| `accuracy`, `elevation` | Finite source values, or null; negative accuracy is discarded |
| `source_household_id` | Original MSR household identity, when supplied |
| `source_type` | Existing ETL source tag used for household isolation |
| `coordinate_type` | `household`; this is not an individual GPS measurement |
| `source_fields` | Names of the original latitude/longitude fields |
| `imported_at` | CoreMIS upload creation time, used for ordering concurrent uploads |

No meaning or units beyond the source contract are assigned to accuracy/elevation.
The importer does not use `*_new` coordinates until their publication semantics
are confirmed, and does not fall back to village coordinates or polygon centroids.

Missing, nonnumeric, non-finite or out-of-range coordinate pairs produce no GPS
update; the last accepted household coordinates remain. Valid zero coordinates
are retained without assuming they are a sentinel or placing them inside Malawi.
A new valid pair replaces the old pair and its quality fields together. Missing
optional quality values become null rather than retaining the old pair's accuracy.
There is no authoritative GPS withdrawal contract yet; absent values are not
treated as a withdrawal instruction.

Repeated members in one household produce one upload payload. Contradictory GPS
for the same household code in a batch fails before importing any rows. Source
tags and household codes must both match the accepted target. Older upload
timestamps cannot replace newer accepted metadata; replaying the same upload is
a no-op. This ordering is based on CoreMIS uploads, not a source GPS revision:
replaying an older source snapshot as a new upload cannot establish source order.

## Configuration compatibility

For the default `ubr` source, legacy top-level `auth_type`, credential fields,
`source_headers` and `ubr_programme_parameter_id` are used as fallbacks. Explicit
`sources.ubr` settings take precedence, including deliberately empty credentials
or `noauth`. Other sources, including `msr_geo_v3`, do not inherit these values.
No credentials or database configuration are changed by this implementation.

## Validation and activation

Run the pure coordinate checks:

```bash
python -m unittest msr_etl.tests.test_household_gps
```

From the configured CoreMIS assembly, run isolated PostgreSQL checks:

```bash
python manage.py shell -c 'from msr_etl.tests.run_household_gps_host import run; run()'
```

The runner shadows household/upload tables in a disposable schema, rolls fixtures
back and disables unrelated search indexing for those fixtures. It reuses an
existing actor read-only. Tests exercise the real host upload service with a
controlled approval callback; they do not claim to run a production import or
demonstrate all custom workflow variants.

After restarting the backend, existing households receive GPS on their next
normal approved household import/update. `sink_update_existing=False` continues
to skip existing records and therefore their GPS updates. Historical imports are
not automatically backfilled. A narrowly scoped operator pilot reimport and
aggregate GPS verification remain an activation check; this code change does
not reimport production households automatically.

No database migration, frontend change, new Mapping query or GPS permission grant
is introduced. GPS resides in the existing household/upload JSON metadata and
follows those records' existing access rules. The following chunk must enforce
Mapping GPS and household/project access before adding household map markers.

### Verification on 24 November 2026

- Four pure coordinate/batch tests passed.
- All 56 isolated PostgreSQL household, adapter, sink and configuration tests
  passed, including the pure checks above.
- All 102 existing geospatial/import coordination regression tests passed.
- Django system checks found no issues and the MSR ETL migration check found no
  model changes.
- The local package remains installed in editable mode, now version 1.3.0.
- Tests did not import production households or alter pilot household, project,
  boundary or membership records. Live household GPS activation is pending a
  scoped operator reimport after backend restart.
