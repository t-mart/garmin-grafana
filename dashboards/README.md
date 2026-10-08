# Garmin dashboards

| File | Dashboard | URL |
| --- | --- | --- |
| `activities.json` | Activities | `/d/garmin-activities` |
| `health.json` | Health | `/d/garmin-health` |
| `records.json` | Records | `/d/garmin-records` |

The dashboards use the provisioned PostgreSQL datasource with UID `garmin_timescaledb` and name `garmin-timescaledb`.
The UID connects each dashboard to the datasource. Its provisioned configuration supplies the database credentials and TimescaleDB option.
Import the JSON files without a datasource selector.

Activities shows the selected activity, independent of the dashboard time range.
Its variable uses SQL `__text` and `__value` columns for the label and activity ID.
Health shows metrics within the time range. Daily averages include complete local days and exclude today.
Records shows the fastest effort in each run and the record progression across imported history.
Its Distance variable reads the distances that have recorded efforts.

The queries read tables in the `garmin` schema. Distances, speeds, temperatures, and weights use imperial display units.
Vertical oscillation uses centimeters. Stride length uses meters.
The tables keep the Garmin and FIT units. The queries convert them.

The Health sleep chart requires the Business Charts plugin (`volkovlabs-echarts-panel`).
It samples the Garmin sleep levels every five minutes and shows local clock times on a reference day.

Links between dashboards use the URLs above. If a dashboard UID changes, update those links.
