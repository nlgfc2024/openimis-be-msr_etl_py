# CoreMIS MSR ETL Backend Module

`openimis-be-msr_etl` integrates the Malawi UBR API with CoreMIS/openIMIS.

The module is intentionally separate from the generic `api_etl` module. Both modules can remain installed, but `msr_etl` exposes MSR-specific GraphQL names so it does not collide with the generic ETL schema.

## Purpose

The MSR ETL module supports a frontend-driven synchronization cycle:

1. The frontend sends location filters: `district`, `ta`, and optionally `village`.
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

Configuration is loaded from openIMIS `ModuleConfiguration` using the module name `msr_etl`.

Default configuration is defined in `msr_etl/apps.py`:

```json
{
  "auth_type": "basic",
  "auth_basic_username": "",
  "auth_basic_password": "",
  "auth_bearer_token": "",
  "source_headers": {},
  "source_batch_size": 50,
  "sink_model_lookup_field": "json_ext__external_id",
  "sink_update_existing": true,
  "gql_query_msr_etl_rule_perms": ["953001"],
  "gql_mutation_execute_msr_etl_rule_perms": ["953002"]
}
```

Supported authentication modes for the UBR API are:

- `noauth`
- `basic`
- `bearer`

## GraphQL API

All fields are namespaced with `msr` to avoid conflicts with the generic `api_etl` module.

### Fetch UBR Individuals

Use this query to fetch household records from UBR. The response contains raw UBR records so the frontend can preview and select records before saving.

```graphql
query FetchMsrUbrIndividuals($district: String, $ta: String, $village: String) {
  msrUbrIndividuals(district: $district, ta: $ta, village: $village) {
    count
    district
    ta
    village
    individuals
  }
}
```

Example variables:

```json
{
  "district": "101",
  "ta": "10101",
  "village": "10101001"
}
```

Backend behavior:

- `district` filters by UBR/openIMIS district code.
- `ta` filters by traditional authority code.
- `village` is sent to UBR as `village_code`.
- If no `district` is provided, the backend iterates all active openIMIS districts.
- If no `ta` is provided, the backend iterates active TAs under the selected district.

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

### Legacy Full ETL Execution

The module still exposes a full ETL execution mutation for operational use:

```graphql
mutation ExecuteMsrEtlService {
  executeMsrEtlService(input: {
    nameOfService: "UBRIndividualService",
    district: "101",
    ta: "10101",
    village: "10101001"
  }) {
    internalId
  }
}
```

For the frontend preview/save workflow, prefer `msrUbrIndividuals`, `saveMsrUbrIndividuals`, `msrUbrLocations`, and `saveMsrUbrLocations`.

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

## Location Filter Semantics

The frontend should send codes, not names:

| Frontend field | Meaning | UBR parameter |
| --- | --- | --- |
| `district` | District code | `district_code` |
| `ta` | Traditional authority code | `traditional_authority_code` |
| `village` | Village code | `village_code` |

For individual fetches, the backend validates the hierarchy against active openIMIS `Location` records before calling UBR.

For location fetches, the backend fetches UBR geo locations and filters returned records by the supplied codes.

## Main Code Paths

- `msr_etl/schema.py`: GraphQL query and mutation registration.
- `msr_etl/gql_queries.py`: MSR-specific GraphQL output types.
- `msr_etl/gql_mutations.py`: Save and execute mutations.
- `msr_etl/sources/ubr_source.py`: UBR API fetch logic.
- `msr_etl/adapters/ubr_adapter.py`: UBR-to-openIMIS transformation logic.
- `msr_etl/sinks/individual_import_sink.py`: Sync to the individual module.
- `msr_etl/sinks/location_import_sink.py`: Sync to the location module.

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
