import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, LiteralString, cast
from zoneinfo import ZoneInfo

from garmin_grafana.derive import Distance, derive
from garmin_grafana.storage import Sample, Store, connect


def dashboard_queries() -> list[tuple[str, str]]:
    queries = []
    for path in Path("dashboards").glob("*.json"):
        dashboard = json.loads(path.read_text())
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                queries.append(
                    (f"{path.name}:{panel['id']}:{target['refId']}", target["rawSql"])
                )
        for variable in dashboard["templating"]["list"]:
            if variable["type"] == "query":
                queries.append((f"{path.name}:{variable['name']}", variable["query"]))
    return queries


def expand(query: str) -> str:
    for key, value in {
        "${TimeZone:sqlstring}": "'America/Chicago'",
        "${Activity:sqlstring}": "'test-run'",
        "${Distance:sqlstring}": "'1 km'",
        "$__interval_ms": "300000",
        "$__timeFilter(time)": "time BETWEEN '2026-10-01'::timestamptz AND '2026-10-09'::timestamptz",
        "$__timeFrom()": "'2026-10-01T12:00:00Z'",
        "$__timeTo()": "'2026-10-09T00:00:00Z'",
    }.items():
        query = query.replace(key, value)
    return query


@unittest.skipUnless(
    os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL for database checks."
)
class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect(os.environ["TEST_DATABASE_URL"])
        self.addCleanup(self.connection.close)
        self.store = Store(self.connection)
        self.store.initialize()
        self.enterContext(self.connection.transaction(force_rollback=True))
        self.at = datetime(2026, 10, 5, 12, tzinfo=UTC)

    def test_archive_and_replacement_preserve_corrections(self) -> None:
        self.store.archive("test", "key", {"extra": [1, 2]})
        self.store.archive("test", "key", {"extra": [1, 2]})
        self.store.archive("test", "key", {"extra": [1, 3]})
        count = self.connection.execute(
            "SELECT count(*) AS count FROM garmin.responses WHERE endpoint = 'test'"
        ).fetchone()
        assert count is not None
        self.assertEqual(count["count"], 2)
        self.store.archive_file("test", b"original")
        self.assertEqual(self.store.latest_file("test"), b"original")
        self.store.replace(
            "test",
            [Sample("Test", self.at, {"a": 1}), Sample("Test", self.at, {"b": 2})],
        )
        self.assertEqual(self.store.samples("Test")[0].fields, {"a": 1, "b": 2})
        self.store.replace(
            "test", [Sample("Test", self.at + timedelta(seconds=1), {"a": 3})]
        )
        self.assertEqual(len(self.store.samples("Test")), 1)
        with self.assertRaises(ValueError):
            self.store.replace("test", [Sample("Test", self.at, {"a": float("nan")})])
        self.assertEqual(self.store.samples("Test")[0].fields, {"a": 3})

    def test_derived_records_recompute_and_dashboard_queries_execute(self) -> None:
        for activity_id, offset, seconds in (
            ("test-run", 0, 400),
            ("test-other", 1, 300),
        ):
            at = self.at + timedelta(days=offset)
            fields: dict[str, Any] = {
                "activityName": "Test run",
                "activityType": "running",
                "distance": 1000,
            }
            self.store.replace(
                activity_id,
                [
                    Sample("ActivitySummary", at, fields, activity_id),
                    Sample("ActivityGPS", at, {"Distance": 0}, activity_id),
                    Sample(
                        "ActivityGPS",
                        at + timedelta(seconds=seconds),
                        {"Distance": 1000},
                        activity_id,
                    ),
                ],
            )
        derive(self.store, [Distance("1 km", 1000)], ZoneInfo("America/Chicago"))
        derive(self.store, [Distance("1 km", 1000)], ZoneInfo("America/Chicago"))
        rows = self.connection.execute(
            'SELECT "Best", "Record" FROM garmin."BestEffort" WHERE "ActivityID" LIKE \'test-%\' ORDER BY time'
        ).fetchall()
        self.assertEqual(
            [(row["Best"], row["Record"]) for row in rows], [(400, 1), (300, 1)]
        )
        for name, query in dashboard_queries():
            with self.subTest(query=name):
                self.connection.execute(cast(LiteralString, expand(query))).fetchall()
        derive(self.store, [Distance("2 km", 2000)], ZoneInfo("America/Chicago"))
        rows = self.connection.execute(
            'SELECT * FROM garmin."BestEffort" WHERE "ActivityID" LIKE \'test-%\''
        ).fetchall()
        self.assertEqual(rows, [])
