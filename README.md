# CoreMIS MSR ETL Backend Module

`openimis-be-msr_etl` integrates the Malawi UBR API with CoreMIS/openIMIS.

The module is intentionally separate from the generic `api_etl` module. Both modules can remain installed, but `msr_etl` exposes MSR-specific GraphQL names so it does not collide with the generic ETL schema.

## Purpose

The MSR ETL module supports a frontend-driven synchronization cycle:

1. The frontend sends filters: location codes (`district`, `ta`, `village`) and UBR-specific targeting criteria (PMT percentile range, wealth quintiles, classification, gender, age range).
2. The backend fetches matching data from the external UBR API.
3. The backend returns the fetched records to the frontend for preview/selection.
4. The frontend sends selected records back to the backend.
5. The backend transforms and saves the records through the existing openIMIS `individual` and `location` modules.

This cycle is available for both:

- UBR household/individual data.
- UBR geo-location data.

## Installation

The module is a normal openIMIS backend module. In local development, add it to `openimis-be_py/openimis.json`:

```json
{
  "name": "msr_etl",
  "pip": "-e /path/to/openimis-be-msr_etl_py"
}
```

Install openIMIS module requirements as usual from the backend project.

## Configuration

Configuration is loaded from openIMIS `ModuleConfiguration` (module `msr_etl`, layer `be`). Defaults live in `DEFAULT_CONFIG` in `msr_etl/apps.py`.

### Module settings

| Key | Default | Purpose |
|---|---|---|
| `sink_model_lookup_field` | `json_ext__ubr_id` | Field used to match imported individuals to existing ones |
| `sink_update_existing` | `true` | Update matched individuals instead of skipping them |
| `sink_import_username` | `""` | User the import workflow runs as; empty uses the user who started the import |
| `gql_query_msr_etl_rule_perms` | `["953001"]` | Rights for msr_etl queries |
| `gql_mutation_execute_msr_etl_rule_perms` | `["953002"]` | Rights for import mutations |
| `staging_retention_hours` | `48` | How long synced unit payloads are kept |
| `sync_unit_max_attempts` | `3` | Sync retries per staged unit |
| `sync_sweep_interval_minutes` | `5` | Sweeper interval for orphaned/failed units |
| `sync_orphan_grace_minutes` | `10` | Idle time before the sweeper takes over a job's units |
| `job_stale_after_hours` | `12` | When an unfinished job is marked failed |
| `sources` | `{"ubr": {...}}` | Per-source settings, keyed by `source_type` |

### Sources

Each entry under `sources` is one `source_type`. Its `connector` picks the code that fetches and transforms its data; only sources whose connector is registered are offered for import.

```json
{
  "sources": {
    "ubr": {
      "connector": "msr_api",
      "display_name": "Malawi Social Registry (MSR)",
      "kind": "both",
      "base_url": "https://malawiubr.org/api/v2",
      "households_endpoint_path": "/get_households_data",
      "geo_locations_endpoint_path": "/get_geo_locations",
      "auth_type": "basic",
      "auth_basic_username": "",
      "auth_basic_password": "",
      "auth_bearer_token": "",
      "headers": {"Accept": "application/json"},
      "timeout_seconds": 300,
      "retry_total": 3,
      "retry_backoff_factor": 1.0,
      "percentile_chunk_size": 10,
      "percentile_chunk_delay_seconds": 1.0,
      "verify_ssl": true,
      "ca_bundle_path": "",
      "programme_parameter_id": 2,
      "disability_parameter_id": 6,
      "filter_schema": {"individual": [...], "location": [...]}
    }
  }
}
```

| Key | Purpose |
|---|---|
| `connector` | Registered connector for this source. `ubr` falls back to `msr_api` when unset |
| `display_name` | Label shown in the frontend's source dropdown |
| `kind` | `individual`, `location` or `both` (default): which imports offer this source |
| `base_url` | API base URL. For `msr_api`, empty falls back to `https://malawiubr.org/api/v2` |
| `households_endpoint_path`, `geo_locations_endpoint_path` | Endpoint paths appended to `base_url` |
| `auth_type` | `noauth`, `basic` or `bearer`, with the matching credential keys |
| `headers` | Extra request headers |
| `timeout_seconds`, `retry_total`, `retry_backoff_factor` | Per-request timeout and retry policy |
| `percentile_chunk_size`, `percentile_chunk_delay_seconds` | Percentile categories per household request, and the pause between them |
| `verify_ssl`, `ca_bundle_path` | TLS verification; see below |
| `programme_parameter_id`, `disability_parameter_id` | UBR parameter ids read by the adapter |
| `filter_schema` | Filter fields per import kind. Drives the frontend form and the allowlist of accepted `filters` |

#### How saved config combines with defaults

`ModuleConfiguration` replaces top-level keys wholesale, so a saved `sources` would otherwise drop every default under it. `get_source_config()` merges each source's saved settings over that source's `DEFAULT_CONFIG` entry, one level deep:

- A key missing from the saved source falls back to its default (e.g. a saved `sources.ubr` without `filter_schema` gets the default UBR filters).
- A key present in the saved source replaces the default as a whole. A saved `filter_schema` must therefore include both `individual` and `location` if both imports should have filters.
- Sources with no `DEFAULT_CONFIG` entry get only their saved settings.

#### `filter_schema` fields

Each field is `{name, label, type, required?, options?, min?, max?, maxLevel?}`, with `type` one of `location`, `select`, `multiselect`, `number`, `boolean` or `text`. A `location` field is sent as `district`/`ta`/`gvh`/`village`. The schedule mutations reject any `filters` key that is not in the source's schema for that import.

#### TLS

For `CERTIFICATE_VERIFY_FAILED`, keep `verify_ssl` true and set `ca_bundle_path` to the CA chain used by the remote server. Set `verify_ssl` to false only in controlled environments.

## GraphQL API

All fields are namespaced with `msr` to avoid conflicts with the generic `api_etl` module.

### Fetch UBR Individuals

Use this query to fetch household records from UBR. The response contains raw UBR records so the frontend can preview and select records before saving.

```graphql
query FetchMsrUbrIndividuals(
  $district: String!
  $ta: String!
  $gvh: String
  $village: String
  $lowerPercentileCategory: Int
  $upperPercentileCategory: Int
  $wealthQuintiles: [Int]
  $classification: [Int]
  $gender: String
  $minAge: Int
  $maxAge: Int
) {
  msrUbrIndividuals(
    district: $district
    ta: $ta
    gvh: $gvh
    village: $village
    lowerPercentileCategory: $lowerPercentileCategory
    upperPercentileCategory: $upperPercentileCategory
    wealthQuintiles: $wealthQuintiles
    classification: $classification
    gender: $gender
    minAge: $minAge
    maxAge: $maxAge
  ) {
    count
    district
    ta
    village
    individuals
  }
}
```

Example variables (narrowly targeted fetch):

```json
{
  "district": "101",
  "ta": "10101",
  "gvh": "1010101",
  "village": "10101001",
  "lowerPercentileCategory": 0,
  "upperPercentileCategory": 20,
  "wealthQuintiles": [1, 2, 3],
  "gender": "Female",
  "minAge": 18,
  "maxAge": 60
}
```

**Location filters**:

| Argument | Meaning | UBR param | Default |
|----------|---------|-----------|---------|
| `district` | District code (required) | `district_code` | none |
| `ta` | Traditional authority code (required) | `traditional_authority_code` | none |
| `gvh` | Group Village Head code | `group_village_head_code` | not sent |
| `village` | Village code | `village_code` | all villages in TA |

**Targeting filters** (all optional — forwarded directly to the UBR API):

| Argument | Meaning | UBR param | Default |
|----------|---------|-----------|---------|
| `lowerPercentileCategory` | Lower bound of PMT percentile range (0–100) | `lower_percentile_category` | `0` |
| `upperPercentileCategory` | Upper bound of PMT percentile range (0–100) | `upper_percentile_category` | `10` |
| `wealthQuintiles` | List of wealth quintile IDs to include | `wealth_quintile` (comma-separated) | `[1, 2, 3]` (Poorest, Poorer, Poor) |
| `classification` | Alternative quintile override — replaces `wealthQuintiles` when provided | `wealth_quintile` | not sent |
| `gender` | Gender string as returned by UBR (e.g. `"Male"`, `"Female"`) | `gender` | not sent |
| `minAge` | Minimum age of household members | `minAge` | not sent |
| `maxAge` | Maximum age of household members | `maxAge` | not sent |

Wealth quintile values:

| ID | Label |
|----|-------|
| 1 | Poorest |
| 2 | Poorer |
| 3 | Poor |
| 4 | Better |
| 5 | Rich |

Backend behavior:

- `district` and `ta` are required for household queries and imports.
- `gvh` and `village` are optional, but a village can only be supplied together with its parent GVH.
- When both `gvh` and `village` are provided, the backend verifies the complete District → TA → GVH → Village hierarchy.
- Every outbound UBR location parameter comes from the frontend request; the backend validates but does not derive missing parameters.
- The requested percentile range is split into non-overlapping inclusive chunks before calling UBR. With the default size of `10`, `0–20` becomes `0–9`, `10–19`, and `20–20`.
- Each successful percentile chunk is transformed and pushed immediately. Batch identifiers include the chunk bounds, and reruns use `json_ext__ubr_id` to distinguish existing individuals from new records.
- A later chunk failure does not roll back earlier successful workflow imports; the failed chunk bounds are included in the ETL error so the operation can be diagnosed and safely rerun.
- Each import results in a UBR API call with all selected location relationship parameters.
- `classification` takes precedence over `wealthQuintiles` when both are supplied.
- Location codes are validated against active openIMIS `Location` records before any UBR API call is made.

### Save Selected Individuals

After preview/selection, send selected raw UBR household records back to the backend.

```graphql
mutation SaveMsrUbrIndividuals($individuals: [GenericScalar]!) {
  saveMsrUbrIndividuals(input: {
    individuals: $individuals
  }) {
    internalId
  }
}
```

The backend then:

1. Transforms UBR household members with `UBRIndividualAdapter`.
2. Splits existing and new individuals with `IndividualImportSink`.
3. Saves through `IndividualImportService` and the configured individual import/update workflows.

### Fetch UBR Locations

Use this query to fetch UBR geo-location data. The response is grouped into batches by openIMIS location type.

```graphql
query FetchMsrUbrLocations($district: String, $ta: String, $village: String) {
  msrUbrLocations(district: $district, ta: $ta, village: $village) {
    count
    batches {
      dataType
      count
      locations
    }
  }
}
```

Example variables:

```json
{
  "district": "101",
  "ta": "10101"
}
```

Returned batch types:

- `D`: District
- `W`: Traditional authority/catchment level used by openIMIS
- `V`: Village

### Save Selected Locations

After preview/selection, send selected location batches back to the backend.

```graphql
mutation SaveMsrUbrLocations($locationBatches: [GenericScalar]!) {
  saveMsrUbrLocations(input: {
    locationBatches: $locationBatches
  }) {
    internalId
  }
}
```

The backend accepts batches in either of these shapes:

```json
{
  "dataType": "V",
  "locations": []
}
```

or:

```json
{
  "data_type": "V",
  "data": []
}
```

The backend then:

1. Ensures default Malawi regions exist.
2. Transforms UBR geo-location records with `UBRLocationAdapter`.
3. Creates or updates openIMIS `Location` records through `LocationImportSink`.

### Full ETL Execution (server-side pipeline)

The module exposes a direct ETL execution mutation that runs the full fetch-transform-save pipeline in a single server-side operation, without a frontend preview step. All UBR individual filters are supported:

```graphql
mutation ExecuteMsrEtlService {
  executeMsrEtlService(input: {
    nameOfService: "UBRIndividualService"
    district: "101"
    ta: "10101"
    gvh: "1010101"
    village: "10101001"
    lowerPercentileCategory: 0
    upperPercentileCategory: 20
    wealthQuintiles: [1, 2, 3]
    gender: "Female"
    minAge: 18
    maxAge: 60
  }) {
    internalId
  }
}
```

Supported `nameOfService` values:

- `UBRIndividualService` — fetches and saves household member records.
- `UBRLocationService` — fetches and saves geo-location records.

> For the frontend preview/select/save workflow, prefer `msrUbrIndividuals`, `saveMsrUbrIndividuals`, `msrUbrLocations`, and `saveMsrUbrLocations` instead.

## Permissions

Queries require:

```python
MsrEtlConfig.gql_query_msr_etl_rule_perms
```

Mutations require:

```python
MsrEtlConfig.gql_mutation_execute_msr_etl_rule_perms
```

Default rights:

- Query: `953001`
- Mutation: `953002`

## Module Architecture

### ETL pipeline

```
GraphQL query / mutation
        │
        ▼
   DataSource          ← pulls raw records from UBR API (ubr_source.py)
        │
        ▼
   DataAdapter         ← transforms UBR records into openIMIS-compatible dicts (ubr_adapter.py)
        │
        ▼
    DataSink           ← upserts into openIMIS Individual / Location modules (sinks/)
```

For the preview workflow the pipeline is split across two round trips:
- **Query** → `DataSource.fetch()` only (returns raw records to the frontend).
- **Mutation** → `DataAdapter` + `DataSink` only (receives selected records from the frontend).

### Key files

| File | Responsibility |
|------|---------------|
| `msr_etl/schema.py` | GraphQL query and mutation registration |
| `msr_etl/gql_queries.py` | MSR-specific GraphQL output types |
| `msr_etl/gql_mutations.py` | Save and execute mutations |
| `msr_etl/services/base.py` | `MsrETLService` abstract base class |
| `msr_etl/services/ubr_service.py` | `UBRIndividualService`, `UBRLocationService` |
| `msr_etl/sources/ubr_source.py` | UBR API fetch logic and filter building |
| `msr_etl/adapters/ubr_adapter.py` | UBR-to-openIMIS record transformation |
| `msr_etl/sinks/individual_import_sink.py` | Sync to the openIMIS individual module |
| `msr_etl/sinks/location_import_sink.py` | Sync to the openIMIS location module |
| `msr_etl/models.py` | `UBRWealthQuintiles` and `UBRRegion` enums |

## Development Notes

Run a syntax check from the repository root:

```bash
openimis-be_py/.venv/bin/python -m compileall -q openimis-be-msr_etl_py/msr_etl
```

Run focused Django tests from the backend project when a PostgreSQL test database is available:

```bash
cd openimis-be_py/openIMIS
../.venv/bin/python manage.py test msr_etl.tests
```
