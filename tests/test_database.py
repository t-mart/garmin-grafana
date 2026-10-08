import json
import os
import re
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import LiteralString, cast

from garmin_grafana.derive import Distance, derive
from garmin_grafana.project import project
from garmin_grafana.storage import Rows, Store, connect


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


def expand(query: str, activity: str = "1") -> str:
    query = re.sub(
        r"\$__timeFilter\(([^)]*)\)",
        r"\1 BETWEEN '2026-10-01'::timestamptz AND '2026-10-09'::timestamptz",
        query,
    )
    for key, value in {
        "${TimeZone:sqlstring}": "'America/Chicago'",
        "${Activity:sqlstring}": f"'{activity}'",
        "${Distance:sqlstring}": "'1 km'",
        "$__interval_ms": "300000",
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

    def count(self, query: LiteralString) -> int:
        row = self.connection.execute(query).fetchone()
        assert row is not None
        return row["count"]

    def test_archive_and_replacement_preserve_corrections(self) -> None:
        for extra in ([1, 2], [1, 2], [1, 3]):
            self.store.archive("test", "key", {"extra": extra})
        self.assertEqual(
            self.count("SELECT count(*) FROM garmin.responses WHERE endpoint = 'test'"),
            2,
        )
        day = {
            "args": ["2000-01-01"],
            "data": {
                "heartRateValueDescriptors": [
                    {"key": "timestamp", "index": 0},
                    {"key": "heartrate", "index": 1},
                ],
                "heartRateValues": [[946684800000, 60], [946684920000, 61]],
                "unknownField": 1,
            },
        }
        self.store.replace(project("get_heart_rates", "key", day))
        day["data"]["heartRateValues"] = [[946684800000, 62]]
        self.store.replace(project("get_heart_rates", "key", day))
        row = self.connection.execute(
            'SELECT "timestamp", heartrate FROM garmin."heartRateValues" '
            "WHERE \"calendarDate\" = '2000-01-01'"
        ).fetchall()
        self.assertEqual(
            row, [{"timestamp": datetime(2000, 1, 1, tzinfo=UTC), "heartrate": 62}]
        )

    def test_best_efforts_recompute_and_dashboard_queries_execute(self) -> None:
        for activity_id, offset, seconds in ((1, 0, 400), (2, 1, 300)):
            at = self.at + timedelta(days=offset)
            activity = {
                "activityId": activity_id,
                "activityName": "Test run",
                "activityType": {"typeId": 1, "typeKey": "running"},
                "startTimeGMT": at.isoformat(),
                "startTimeLocal": at.replace(tzinfo=None).isoformat(),
                "distance": 1000,
            }
            self.store.replace(
                project("get_activities_by_date", "key", {"data": [activity]})
            )
            self.store.replace(
                [
                    Rows(
                        "fit_record",
                        {"activityId": activity_id},
                        [
                            {"timestamp": at, "distance": 0},
                            {
                                "timestamp": at + timedelta(seconds=seconds),
                                "distance": 1000,
                            },
                        ],
                    )
                ]
            )
        derive(self.store, [Distance("1 km", 1000)])
        derive(self.store, [Distance("1 km", 1000)])
        rows = self.connection.execute(
            "SELECT seconds FROM garmin.best_effort "
            "WHERE activity_id IN (1, 2) ORDER BY activity_id"
        ).fetchall()
        self.assertEqual([row["seconds"] for row in rows], [400, 300])
        for name, query in dashboard_queries():
            with self.subTest(query=name):
                self.connection.execute(cast(LiteralString, expand(query))).fetchall()
        derive(self.store, [Distance("2 km", 2000)])
        self.assertEqual(
            self.count(
                "SELECT count(*) FROM garmin.best_effort WHERE activity_id IN (1, 2)"
            ),
            0,
        )
