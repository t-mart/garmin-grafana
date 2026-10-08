import struct
import unittest
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fitdecode.utils import compute_crc

from garmin_grafana.activity import fit_samples, tcx_samples
from garmin_grafana.derive import (
    Point,
    fastest_window,
    step_records,
)
from garmin_grafana.normalize import daily_samples, sleep_samples


class DerivationTests(unittest.TestCase):
    def test_tcx_track_and_lap(self) -> None:
        content = b"""<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
        <Activities><Activity><Lap StartTime="2026-10-05T12:00:00Z">
        <DistanceMeters>1000</DistanceMeters><TotalTimeSeconds>400</TotalTimeSeconds>
        <Track><Trackpoint><Time>2026-10-05T12:00:01Z</Time>
        <Position><LatitudeDegrees>0</LatitudeDegrees><LongitudeDegrees>0</LongitudeDegrees></Position>
        <DistanceMeters>5</DistanceMeters><HeartRateBpm><Value>100</Value></HeartRateBpm>
        </Trackpoint></Track></Lap></Activity></Activities></TrainingCenterDatabase>"""
        rows = tcx_samples(content, "123", datetime(2026, 10, 5, 12, tzinfo=UTC))
        self.assertEqual(rows[0].fields["Latitude"], 0)
        self.assertEqual(rows[0].fields["DurationSeconds"], 1)
        self.assertEqual(rows[1].fields["Elapsed_Time"], 400)

    def test_best_effort_uses_elapsed_samples(self) -> None:
        points = [
            Point(0, 0),
            Point(30, 100),
            Point(300, 200),
            Point(320, 300),
            Point(340, 400),
        ]
        window = fastest_window(points, 200)
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual((window.seconds, window.meters), (40, 200))
        self.assertIsNone(fastest_window(points, 500))

    def test_step_weeks_start_on_monday_and_ties_keep_earliest(self) -> None:
        records = step_records(
            {date(2026, 10, 4): 10, date(2026, 10, 5): 10, date(2026, 10, 6): 10}
        )
        self.assertEqual([record.steps for record in records], [10, 20, 30])
        self.assertIn("Oct 4", records[0].title)
        self.assertIn("Oct 5 to 11", records[1].title)

    def test_sleep_uses_actual_start_and_keeps_deep_stage_zero(self) -> None:
        start = datetime(2026, 3, 8, 7, 55, tzinfo=UTC)
        end = start + timedelta(minutes=20)
        data = {
            "dailySleepDTO": {
                "sleepStartTimestampGMT": start.timestamp() * 1000,
                "sleepEndTimestampGMT": end.timestamp() * 1000,
            },
            "sleepLevels": [
                {
                    "startGMT": start.isoformat(),
                    "endGMT": end.isoformat(),
                    "activityLevel": 0,
                }
            ],
        }
        samples = sleep_samples(data, ZoneInfo("America/Chicago"))
        stages = [sample for sample in samples if sample.measurement == "SleepStage"]
        self.assertEqual(len(stages), 4)
        self.assertTrue(all(sample.fields["Stage"] == 0 for sample in stages))
        self.assertEqual(
            stages[1].fields["Clock"] - stages[0].fields["Clock"], 65 * 60 * 1000
        )

    def test_daily_date_and_zero_values(self) -> None:
        zone = ZoneInfo("America/Chicago")
        rows = daily_samples(
            "daily_avg",
            {"totalSteps": 0, "unknown": {"extra": 1}},
            date(2026, 10, 5),
            zone,
        )
        self.assertEqual(rows[0].time.astimezone(UTC).hour, 5)
        self.assertEqual(rows[0].fields["unknown"], {"extra": 1})
        rows = daily_samples(
            "stress",
            {"stressValuesArray": [[1000, 0], [2000, -1]]},
            date(2026, 10, 5),
            zone,
        )
        self.assertEqual([row.fields["stressLevel"] for row in rows], [0, -1])

    def test_fit_zero_coordinates_and_enhanced_speed(self) -> None:
        definition = bytes([0x40, 0, 0]) + struct.pack("<H", 20) + bytes([5])
        definition += bytes(
            [253, 4, 0x86, 0, 4, 0x85, 1, 4, 0x85, 6, 2, 0x84, 73, 4, 0x86]
        )
        data = (
            definition + bytes([0]) + struct.pack("<IiiHI", 1000000000, 0, 0, 1000, 0)
        )
        content = struct.pack("<BBHI4s", 12, 0x10, 2100, len(data), b".FIT") + data
        content += struct.pack("<H", compute_crc(content))
        samples = fit_samples(
            content,
            "123",
            datetime(1989, 12, 31, tzinfo=UTC) + timedelta(seconds=1000000000),
        )
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].fields["Latitude"], 0)
        self.assertEqual(samples[0].fields["Longitude"], 0)
        self.assertEqual(samples[0].fields["Speed"], 0)
        self.assertEqual(samples[0].fields["Fractional_Cadence"], 0)
