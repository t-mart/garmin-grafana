<p align="center">
<img src="https://i.imgur.com/PYsbwqj.png" width="450" height="164" align="center">
</p>

# Grafana for Garmin Dashboard

A docker container to fetch data from Garmin servers and store the data in a
local ~~influxdb~~ TimescaleDB database.

This project is a fork of
[arpanghosh8453/garmin-grafana](https://github.com/arpanghosh8453/garmin-grafana)
with different goals:

- Use TimescaleDB as the primary database for all Garmin data.
- Integrate derivation logic from `derive.py` into the main collector workflow.
- Reorganize the dashboards
- Use imperial units

## Features

- Automatic data collection from Garmin
- Collects comprehensive health metrics including:
  - Heart Rate Data
  - Hourly steps Heatmap
  - Daily Step Count
  - Sleep Data and patterns (SpO2, Breathing rate, Sleep movements, HRV)
  - Sleep regularity heatmap (Visualize sleep routine)
  - Stress Data
  - Body Battery data
  - Calories
  - Sleep Score
  - Activity Minutes and HR zones
  - Activity Timeline (workouts)
  - GPS data from workouts (track, pace, altitude, HR)
  - And more...
- Automated data fetching in regular interval (set and forget)
- Historical data backfilling

## Why use this project?

- **Free and Fully Open Source**: 100% transparent and open project — modify,
  distribute extend, and self-host as you wish, with no hidden costs. Just
  credit the author and support this project as you please!
- **Local Ownership**: Keep a complete, private backup of your Garmin data. The
  script automatically syncs new data after each Garmin Connect upload — no
  manual action needed ("set and forget").
- **Full Visualization Freedom**: You're not limited by Garmin’s app. Combine
  multiple metrics on a single panel, zoom into specific time windows, view raw
  (non-averaged) data over days or weeks, and build fully custom dashboards.
- **Deeper Insights - All day metrics**: Explore your data to discover patterns,
  optimize performance, and track trends over longer periods of time. Export for
  advanced analysis (Python, Excel, etc.) from Grafana, set custom alerts, or
  create new personalized metrics. This project fetches _almost_ everything from
  your Garmin watch - not just limited to Activities analytics like most other
  online platforms
- **No 3rd party data sharing**: You avoid sharing your sensitive health related
  data with any 3rd party service provider while having a great data
  visualization platform for free!


## Historical data fetching (bulk update)

> [!TIP] Please note that this process is intentionally rate limited with a 5
> second wait period between each day update to ensure the Garmin servers are
> not overloaded with requests when using bulk update. You can update the value
> with `RATE_LIMIT_CALLS_SECONDS` ENV variable in the `garmin-fetch-data`
> container, but lowering it is not recommended,

> [!NOTE] Please note that this process, if repeated multiple times, **DOES NOT
> create any duplicate data** in the database if used with InfluxDB. InfluxDB
> being a time series database, uses timestamp and tags combined to create a
> hash that is used as primary key. So writing the same values with same
> timestamp and tags effectively overwrites the previous field values.

#### Procedure

1. Please run the above docker based installation steps `1` to `4` first (to set
   up the Garmin Connect login session tokens if not done already).
2. Stop the running container and remove it with `docker compose down` if
   running already
3. Run command
   `docker compose run --rm -e MANUAL_START_DATE=YYYY-MM-DD -e MANUAL_END_DATE=YYYY-MM-DD garmin-fetch-data`
   to update the data between the two dates. You need to replace the
   `YYYY-MM-DD` with the actual dates in that format, for example
   `docker compose run --rm -e MANUAL_START_DATE=2025-04-12 -e MANUAL_END_DATE=2025-04-14 garmin-fetch-data`.
   The `MANUAL_END_DATE` variable is optional, if not provided, the script
   assumes it to be the current date. `MANUAL_END_DATE` must be in future to the
   `MANUAL_START_DATE` variable passed, and in case they are same, data is still
   pulled for that specific date.

> [!TIP] If you are running this more than once to update the old data after
> container update, and want to only fetch specific data points instead of
> everything for the bulk fetch (to save time and resources), you can set
> `FETCH_SELECTION` to the measurements you want to fetch again. You can
> override the value of compose like this
> `docker compose run --rm -e MANUAL_START_DATE=YYYY-MM-DD -e MANUAL_END_DATE=YYYY-MM-DD -e FETCH_SELECTION=activity,sleep garmin-fetch-data`
> if you just want to update/re-fetch the past activities and sleep data and
> nothing else. Look at the compose file comments to know what values are
> available for this variable.

1. Please note that the bulk data fetching is done in **reverse chronological
   order**. So you will have recent data first and it will keep going back until
   it hits `MANUAL_START_DATE`. You can have this running in background. If this
   terminates after some time unexpectedly, you can check back the last
   successful update date from the container stdout logs and use that as the
   `MANUAL_END_DATE` when running bulk update again as it's done in reverse
   chronological order.
2. After successful bulk fetching, you will see a `Bulk update success` message
   and the container will exit and remove itself automatically.
3. Now you can run the regular periodic update with `docker compose up -d`

> [!IMPORTANT] Garmin puts **Intraday historic data** older than **six months**
> in **cold storage (archived database)** and they are not available to the
> regular API endpoints directly anymore. You can do a manual refresh request
> for that day from the app, and only then the data becomes available for 7 days
> before it goes back to cold storage again. There is a daily server-side limit
> on the refresh requests (estimated around 20-40 per day) - So it's not
> possible to refresh the data in bulk while importing. if you have used this
> script to bulk fetch your past data older than 6 months, the intraday data
> points (indtaday HR rates, intraday sleep stages, etc.) will be missing for
> the older dates - although the daily average data points remain available the
> API endpoints for any past dates (regardless of how old they are) and hence
> remains unaffected. Please check out
> [Issue #77](https://github.com/arpanghosh8453/garmin-grafana/issues/77) if you
> want to know more about this. This is not a limitation of this project as it
> is imposed by Garmin's API design.
