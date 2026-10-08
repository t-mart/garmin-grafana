# Garmin dashboards

| File | Dashboard | URL |
| --- | --- | --- |
| `activities.json` | Activities | `/d/garmin-activities` |
| `health.json` | Health | `/d/garmin-health` |
| `records.json` | Records | `/d/garmin-records` |

Add a PostgreSQL datasource in Grafana with the Garmin database and a reader role.
Enable its TimescaleDB option. Import the JSON files, then select that datasource in each dashboard's Datasource variable.
Grafana resolves the selection to a datasource UID. The dashboard does not contain database credentials or require a particular datasource name.

Activities shows the selected activity, independent of the dashboard time range.
Its variable uses SQL `__text` and `__value` columns for the label and activity ID.
Health shows metrics within the time range. Daily averages include complete local days and exclude today.
Records shows the fastest effort in each run and the record progression across imported history.
Its Distance variable reads the distances that have recorded efforts.

The queries use views in the `garmin` schema. Distances, speeds, temperatures, and weights use imperial display units.
Vertical oscillation uses centimeters. Stride length uses meters.
Raw Garmin values retain their original units in the archive.

The Health sleep chart requires the Business Charts plugin (`volkovlabs-echarts-panel`).
It uses five-minute stage samples and local clock times on a reference day.
The collector timezone must match the dashboard timezone for this chart.

Links between dashboards use the URLs above. If a dashboard UID changes, update those links.
