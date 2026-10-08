# Garmin fetch fork goals

## Purpose

Fork the collector behind `thisisarpanghosh/garmin-fetch-data` to use the shared
TimescaleDB service and incorporate [derive.py](derive.py). Keep all dashboards
in this infrastructure repository.

The source repository is
[arpanghosh8453/garmin-grafana](https://github.com/arpanghosh8453/garmin-grafana).
Its README documents InfluxDB 3 support but still recommends InfluxDB 1.11.
The reason for this fork is database consolidation and control over data
collection and derivation.

This document defines the target. It does not change the deployment.

## Current state

- [deployment.yaml](deployment.yaml) runs one collector with a digest-pinned
  upstream image and persistent Garmin authentication tokens.
- [influxdb-deployment.yaml](influxdb-deployment.yaml) runs InfluxDB `1.11.8`
  with the `GarminStats` database, dedicated storage, and a separate backup job.
- [derive-cronjob.yaml](derive-cronjob.yaml) runs `derive.py` every five minutes.
  The script reads source measurements from InfluxDB and writes derived measurements.
- [distances.json](distances.json) defines the distances for best efforts.
  The cluster supplies the time zone through `TZ`.
- The [Garmin dashboards](../grafana/dashboards/garmin/README.md) belong to this
  repository. Activities, Health, and Records use the `garmin_influxdb` datasource.
- The shared [TimescaleDB deployment](../timescaledb/deployment.yaml) uses
  TimescaleDB `2.30.2` with PostgreSQL 18. Other telemetry collectors already use it.

## Fork goals

### Use TimescaleDB as the only runtime database

- Replace InfluxDB clients, queries, writes, and configuration with PostgreSQL
  access and TimescaleDB support.
- Use the shared service at `timescaledb.timescaledb.svc.cluster.local:5432`.
  Keep the connection configurable for other deployments.
- Isolate Garmin data in a dedicated database with separate collector and
  read-only dashboard roles.
- Define typed tables, stable keys, indexes, and versioned schema migrations.
  Use hypertables for time series where appropriate.
- Preserve source data, activity IDs, timestamps, units, and local calendar dates.
  Keep source data available for later derivation.
- Make repeat imports and retries safe through explicit duplicate prevention
  and updates to records that change.
- Document the SQL schema as the interface for external consumers.
  InfluxDB measurement names and line protocol are not compatibility requirements.

### Preserve data collection

- Preserve the current Garmin data coverage, automatic collection, and historical
  imports. Include activity summaries, laps, GPS samples, and health data.
- Preserve token persistence and the authentication setup for accounts with MFA.
- Supply a versioned container that runs without root access in Kubernetes.
- Report collection and derivation failures separately so that operators can
  identify incomplete work and retry it.

### Integrate derivation

Move the behavior and applicable tests from [derive.py](derive.py) and
[test_derive.py](test_derive.py) into the fork. Replace the separate script
ConfigMap and five-minute CronJob with derivation inside the collector workflow.

| Current output | Behavior to preserve |
| --- | --- |
| `ActivityIndex` | One activity ID and descriptive label per activity, with updates after metadata changes. |
| `BestEffort` | Fastest sampled span for each configured distance in each run, plus record progression in time order. |
| `BestEffortRun` | Completion state and invalidation after distance configuration or algorithm changes. |
| `StepRecord` | Maximum step totals for a day, Monday-based week, and calendar month. Ties retain the earliest period. |
| `SleepWindow` | Sleep start and end from wake time, sleep duration, and awake duration, with local clock positions. |
| `SleepStage` | Stage samples every five minutes, without category averages or samples across gaps. |
| `ActivityWeather` | Temperature, apparent temperature, humidity, and wind for the hour nearest the activity start. |

- Keep distances and the time zone configurable. Use `distances.json` as the
  initial distance configuration.
- Preserve the best-effort algorithm's treatment of GPS distance decreases and
  sample overshoot. Preserve duration and pace semantics.
- Preserve the sleep reference axis from 6 PM to 6 PM and the local wake date.
- Preserve Open-Meteo weather enrichment, coordinate precision of two decimals,
  and attribution. Keep weather failures separate from other data updates.
- Derive data after source data arrives. Recompute affected results after
  historical imports, source corrections, or derivation changes.
- Support explicit derivation from stored source data without another Garmin fetch.
- Preserve equivalent results, without a requirement for identical table names
  or storage of values that SQL can calculate.

### Keep dashboards in this repository

- Remove upstream dashboard files, Grafana datasource provisioning, and bundled
  Grafana deployment assets from the fork.
- Keep Activities, Health, Records, panel code, and Grafana configuration here.
- Supply data and schema documentation from the fork. Keep dashboard design
  and presentation choices here.
- Convert this repository's dashboard queries to SQL as part of the deployment
  transition. Preserve dashboard URLs and behavior.

## Data migration and deployment transition

These changes belong to the implementation phase, after the fork exists.

1. Provide a repeatable import of historical source data from `GarminStats` into
   TimescaleDB. Do not depend on Garmin to supply the full history again.
2. Recompute derived data from the imported source data.
3. Compare source coverage, timestamps, units, and derived results against InfluxDB.
4. Add the Garmin database to the TimescaleDB backup list in
   [backup-patch.yaml](../timescaledb/backup-patch.yaml).
5. Verify a restore under the [database procedures](../../../DATABASES.md).
6. Switch the collector image, credentials, and Grafana datasource to TimescaleDB.
7. Retain InfluxDB data and backups until data comparison, dashboard checks,
   and a restore test pass.
8. After acceptance, retire the dedicated InfluxDB deployment and backup job.
9. Remove the separate derivation job and script ConfigMap.

## Completion criteria

- The fork collects Garmin data and derives results with no runtime InfluxDB dependency.
- Historical imports and repeat runs preserve data without duplicate records.
- Tests cover SQL persistence, retries, derivation parity, and recomputation after
  source or configuration changes.
- Activities, Health, and Records retain their behavior through SQL queries in
  this repository.
- The fork contains no dashboards or Grafana provisioning assets.
- The shared TimescaleDB backup procedure covers Garmin data and passes a restore test.
