# Garmin dashboards

| File | Dashboard | URL |
| --- | --- | --- |
| `activities.json` | Activities | `/d/garmin-activities` |
| `day.json` | Day | `/d/garmin-day` |
| `records.json` | Records | `/d/garmin-records` |
| `trends.json` | Trends | `/d/garmin-trends` |

The dashboards use the provisioned PostgreSQL datasource with UID `garmin_timescaledb` and name `garmin-timescaledb`.
The UID connects each dashboard to the datasource. Its provisioned configuration supplies the database credentials and TimescaleDB option.
Import the JSON files without a datasource selector.

Activities shows the selected activity, independent of the dashboard time range.
Its variable uses SQL `__text` and `__value` columns for the label and activity ID.
Day shows the samples within the time range. Its default time range is today.
Its stat tiles show the last day in the time range and the night that ends on that day. Today counts so far.
Health shows metrics within the time range. Daily averages include complete local days and exclude today.
Trends shows one value for each day or night. Its default time range is the last 30 days.
Each bar shows the average per day in its period. The Group bars by variable sets the period.
Auto uses days up to 100 days, weeks up to two years, and months after that.
Line charts show each day as a dot and the average of the last 7 days as a line.
Day totals exclude today. Night values include last night.
The Garmin links in Day and Trends do not keep the time range, because the two dashboards need different ranges.
To see a Trends range in Day, zoom in and select Open this range in Day.
Records shows the fastest effort in each run and the record progression across imported history.
Its Distance variable reads the distances that have recorded efforts.

The queries read tables in the `garmin` schema. Distances, speeds, temperatures, and weights use imperial display units.
Vertical oscillation uses centimeters. Stride length uses meters.
The tables keep the Garmin and FIT units. The queries convert them.

The Health sleep chart, the Trends sleep schedule, and the Activities pace chart require the Business Charts plugin (`volkovlabs-echarts-panel`).
The Health sleep chart samples the Garmin sleep levels every five minutes and shows local clock times on a reference day.

Links between dashboards use the URLs above. If a dashboard UID changes, update those links.
