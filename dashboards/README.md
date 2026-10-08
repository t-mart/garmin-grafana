# Garmin dashboards

This repository owns these dashboards. They do not follow the upstream
garmin-grafana dashboard. Grafana shows them in the Garmin folder.

| File              | Dashboard  | URL                    |
| ----------------- | ---------- | ---------------------- |
| `activities.json` | Activities | `/d/garmin-activities` |
| `health.json`     | Health     | `/d/garmin-health`     |
| `records.json`    | Records    | `/d/garmin-records`    |

Activities shows one activity: a summary with the weather, the laps, two maps,
and charts. It does not use the time range. Health shows
averages for the time range, then sections for heart and breathing, stress and
Body Battery, sleep, activity, and fitness and body. The default range is the
last 24 hours. Records shows the current running records,
one chart for each distance, the Garmin race predictions, and the step records.
Each chart shows the fastest effort in each run and the record at each date.

Links between the dashboards use the URLs in the table. If you change a
dashboard `uid`, also change the links that use it.

## Data

The `garmin-fetch` Deployment writes the Garmin data to InfluxDB. The panels
use the `garmin_influxdb` datasource.

The `derive` CronJob in `kubernetes/apps/bay/garmin` adds these measurements
every 5 minutes:

- `ActivityIndex` contains one option for each activity. The `Activity`
  variable in Activities reads it. Each option contains a label and the
  activity ID, separated by `|`.
- `BestEffort` contains the fastest time for each distance in each run. `Best`
  is the record at the date of the run. `Record` is 1 when the run set a new
  record. The Records tiles and charts read it.
- `BestEffortRun` records the runs that the job processed. Only the job reads
  it.
- `StepRecord` contains the most steps in a day, a week, and a month. Weeks
  start on Monday. The Records step tiles read it.
- `SleepWindow` contains the start and the end of each night, as clock times on
  a reference day. The job uses it to find new and changed nights. `Samples`
  counts the `SleepStage` points of the night.
- `SleepStage` contains the sleep stage every 5 minutes of each night, with the
  clock time on the reference day. The Health "Sleep stages by night" chart
  reads it.
- `ActivityWeather` contains the temperature, the apparent temperature, the
  humidity, and the wind for each activity with GPS data. The values are for
  the hour nearest to the start. The Activities summary reads it.

The weather data comes from Open-Meteo (https://open-meteo.com) under CC BY 4.0.
The job sends the start position of each activity to Open-Meteo. It rounds the
position to two decimals, about 1 km.

Garmin stamps each daily record, such as `DailyStats`, at the local midnight at
the start of its day. A range that starts later in the day does not contain that
time, so the daily queries use `time >= ${__from}ms - 1d` instead of
`$timeFilter`. This includes the day in which the range starts.
The average tiles also use `time <= now() - 1d`, which leaves out the record of
today until the day ends.

Health charts group the data by the automatic interval of Grafana. Each chart
has a minimum interval at the resolution of its data, so a short range shows
the original samples. Stress values below 0 are Garmin status codes, so the
stress queries ignore them. Sleep stages are categories, so the job samples
them every 5 minutes and does not average them.

The Health "Sleep stages by night" chart uses the Business Charts plugin
(`volkovlabs-echarts-panel`). The Grafana deployment installs the plugin. The
JavaScript that draws the chart is in the `getOption` option of the panel.

The "Current records" tiles show the `Duration` text. The stat panel finds that
field by its display name, and the display name is the caption template. If you
change the caption, also change the `Fields` pattern of the panel.

## Change the record distances

1. Edit `kubernetes/apps/bay/garmin/distances.json`. Keep the distances in
   ascending order.
2. In `records.json`, make the `query` and the `options` of the `Distance`
   variable agree with the new labels.

After the change, the `derive` job computes the best efforts for all runs
again.

