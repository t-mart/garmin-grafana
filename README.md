# Garmin Grafana

This fork of [arpanghosh8453/garmin-grafana](https://github.com/arpanghosh8453/garmin-grafana)
archives Garmin data in TimescaleDB. It includes personal Grafana dashboards with imperial units.

The collector retains complete API responses and original activity files, including fields that the dashboards do not use.
Changed responses remain in the archive. Identical responses share one archive entry.
The collector replaces corrected samples in a transaction. Repeat imports do not add duplicate samples.
There is no retention policy.

The collector computes activity labels, best efforts, step records, sleep stages, and activity weather.
These replace the separate `derive.py` cron job. SQL computes record progression across all imported runs.

## Setup

Use Python 3.14, uv, and a database with the TimescaleDB extension.

```sh
uv sync --locked
uv run --env-file .env.local garmin-grafana init
uv run --env-file .env.local garmin-grafana login
uv run --env-file .env.local garmin-grafana
```

Set these environment variables in `.env.local` or the container environment:

| Variable | Purpose | Default |
| --- | --- | --- |
| `GARMINCONNECT_EMAIL` | Garmin account email | None |
| `GARMINCONNECT_BASE64_PASSWORD` | Base64 Garmin password | None |
| `DATABASE_URL` | PostgreSQL connection URI | Standard `PG*` variables |
| `USER_TIMEZONE` | Local calendar dates and sleep axis | `TZ`, then `America/Chicago` |
| `TOKEN_DIR` | Persistent Garmin token directory | `.local/tokens`; `/data/tokens` in Docker |
| `UPDATE_INTERVAL_SECONDS` | Delay between collection cycles | `300` |
| `RATE_LIMIT_CALLS_SECONDS` | Delay before each Garmin request | `5` |
| `FETCH_SELECTION` | Comma-separated data categories | See `collector.py` |

The default categories match the upstream collector. Additional categories include training status, training readiness, hill score, endurance score, blood pressure, hydration, and lactate threshold.
All responses enter `garmin.responses`. Normalized samples enter the `garmin.samples` hypertable. Original activity files enter `garmin.files`.
The dashboard SQL views expose typed columns from the samples.

Use `login` in a terminal if Garmin requires MFA. Retain the token directory between container runs.

Use the database owner for `init` and the collector. If necessary, ask the database administrator to install the TimescaleDB extension first.
The collector does not require a superuser. Use a separate reader role for Grafana:

```sql
GRANT USAGE ON SCHEMA garmin TO grafana;
GRANT SELECT ON ALL TABLES IN SCHEMA garmin TO grafana;
ALTER DEFAULT PRIVILEGES IN SCHEMA garmin GRANT SELECT ON TABLES TO grafana;
```

Run those grants as the owner after `init`. The reader role must already exist.
The former InfluxDB variables have no effect.

## Historical data

```sh
uv run --env-file .env.local garmin-grafana sync --start 2026-10-05 --end 2026-10-07
```

Both dates are inclusive. The collector processes recent dates first, then exits.
`MANUAL_START_DATE` and `MANUAL_END_DATE` also set these bounds.
Without explicit bounds, the collector refreshes three recent days and covers gaps since the last successful cycle.
Use `--once` for one cycle. Use `--force` to download unchanged activities again.

The collector archives all returned Garmin fields. It cannot retrieve data that Garmin no longer exposes through its APIs.


## Development

The Compose service uses TimescaleDB 2.30.2 and PostgreSQL 18, like the production deployment.
It binds port 55432 on localhost and stores its data in memory. Container removal deletes that data.

```sh
docker compose up --detach --wait
```

Use `postgresql://garmin:garmin@127.0.0.1:55432/garmin` as `DATABASE_URL` for this database.

```sh
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run python -m unittest discover --start-directory tests
```

Set `TEST_DATABASE_URL` to the temporary database URI to include database and dashboard SQL checks.
These checks use synthetic data and require no Garmin credentials or Grafana instance.

```sh
docker compose down
```

## Container

```sh
docker build --tag garmin-grafana:latest .
docker run --rm --env-file .env.local --volume garmin-tokens:/data garmin-grafana:latest
```

Set `DATABASE_URL` to an address that the container can reach.
Run the container with `init` once before its first collection cycle.

The GitHub workflow checks the code, then publishes `latest` to Docker Hub on pushes to `main`.
Set the repository variable `DOCKERHUB_USERNAME` and the repository secret `DOCKERHUB_TOKEN`.
The image name is `<DOCKERHUB_USERNAME>/garmin-grafana:latest`.
Pull requests run checks without publication.

See [dashboards/README.md](dashboards/README.md) for the dashboard datasource configuration.
