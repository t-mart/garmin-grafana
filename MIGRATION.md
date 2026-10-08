# Migration to relational tables

Do these steps one time on the production database. Then delete this file.

The migration adds the relational tables to the `garmin` schema and fills them from the archive.
The old `garmin.samples` table and its views stay until the last step. Thus you can roll back until then.

## Prepare

You need `psql` and a checkout of this commit. Use the database owner role.

```nu
$env.DATABASE_URL = "postgresql://owner:password@host:5432/garmin"
```

Stop the collector. The `rebuild` command needs the database lock.

## Migrate

```nu
uv run garmin-grafana init
uv run garmin-grafana rebuild
```

The rebuild reads every archived response and FIT file. It can take several minutes.

If you did not set the default privileges for the Grafana reader role, grant access to the new tables.
If the role is not `grafana`, change the role name in this command:

```nu
psql $env.DATABASE_URL --command "GRANT SELECT ON ALL TABLES IN SCHEMA garmin TO grafana"
```

## Deploy

1. Import the three files in `dashboards/` into Grafana. Overwrite the existing dashboards.
2. Deploy the new image and start the collector.
3. Open each dashboard and make sure that the panels show data.

The first collection cycle requests weather again for some activities. This is expected.

## Roll back

If the new version fails, deploy the previous image and the previous dashboards.
The previous version ignores the new tables.

## Clean up

When the dashboards work, drop the old projection and delete this file.

```nu
psql $env.DATABASE_URL --command "DROP TABLE garmin.samples CASCADE"
rm MIGRATION.md
```
