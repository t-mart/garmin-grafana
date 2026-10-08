import struct
import unittest

from fitdecode.utils import compute_crc

from garmin_grafana.derive import Point, fastest_window
from garmin_grafana.project import fit, project


class ProjectionTests(unittest.TestCase):
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

    def test_positional_arrays_use_descriptors(self) -> None:
        descriptors = [
            {
                "bodyBatteryValueDescriptorIndex": 2,
                "bodyBatteryValueDescriptorKey": "bodyBatteryLevel",
            },
            {
                "bodyBatteryValueDescriptorIndex": 0,
                "bodyBatteryValueDescriptorKey": "timestamp",
            },
        ]
        data = {
            "bodyBatteryValueDescriptorsDTOList": descriptors,
            "bodyBatteryValuesArray": [[1000, "MEASURED", 0, 3.0]],
        }
        tables = {
            rows.table: rows.rows
            for rows in project(
                "get_stress_data", "key", {"args": ["2026-10-05"], "data": data}
            )
        }
        self.assertEqual(
            tables["bodyBatteryValuesArray"],
            [{"timestamp": 1000, "bodyBatteryLevel": 0}],
        )
        self.assertEqual(tables["stressValuesArray"], [])

    def test_fit_keeps_zero_coordinates_and_laps_with_one_timestamp(self) -> None:
        definition = bytes([0x40, 0, 0]) + struct.pack("<H", 20) + bytes([5])
        definition += bytes(
            [253, 4, 0x86, 0, 4, 0x85, 1, 4, 0x85, 6, 2, 0x84, 73, 4, 0x86]
        )
        data = (
            definition + bytes([0]) + struct.pack("<IiiHI", 1000000000, 0, 0, 1000, 0)
        )
        data += bytes([0x41, 0, 0]) + struct.pack("<H", 19) + bytes([2])
        data += bytes([253, 4, 0x86, 254, 2, 0x84])
        for index in range(2):
            data += bytes([1]) + struct.pack("<IH", 1000000000, index)
        content = struct.pack("<BBHI4s", 12, 0x10, 2100, len(data), b".FIT") + data
        content += struct.pack("<H", compute_crc(content))
        tables = {rows.table: rows for rows in fit("123", content)}
        records = tables["fit_record"]
        self.assertEqual(records.key, {"activityId": 123})
        self.assertEqual(len(records.rows), 1)
        self.assertEqual(records.rows[0]["position_lat"], 0)
        self.assertEqual(records.rows[0]["position_long"], 0)
        self.assertEqual(records.rows[0]["enhanced_speed"], 0)
        self.assertEqual(len(tables["fit_lap"].rows), 2)
