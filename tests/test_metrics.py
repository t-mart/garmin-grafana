import os
import socket
import unittest
from datetime import date
from threading import Event
from unittest.mock import Mock, patch
from urllib.request import urlopen

from prometheus_client import CollectorRegistry, Counter, Gauge

from garmin_grafana import cli, collector, metrics


class MetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = CollectorRegistry()
        for module, name, value in (
            (
                collector,
                "FETCHES",
                Counter(
                    "fetches", "Calls", ["endpoint", "result"], registry=self.registry
                ),
            ),
            (
                metrics,
                "CYCLES",
                Counter("cycles", "Cycles", ["result"], registry=self.registry),
            ),
            (
                metrics,
                "LAST_SUCCESS",
                Gauge("last_success", "Success time", registry=self.registry),
            ),
            (
                metrics,
                "IN_PROGRESS",
                Gauge("in_progress", "Active cycle", registry=self.registry),
            ),
        ):
            self.enterContext(patch.object(module, name, value))

    def test_request_results(self) -> None:
        self.assertEqual(
            collector.request("get_stats", lambda: {"steps": 2}), {"steps": 2}
        )
        for code, result in ((404, "empty"), (429, "error")):
            with self.assertRaises(RuntimeError):
                collector.request(
                    "get_stats", Mock(side_effect=RuntimeError(f"HTTP {code}"))
                )
            self.assertEqual(
                self.registry.get_sample_value(
                    "fetches_total", {"endpoint": "get_stats", "result": result}
                ),
                1,
            )
        self.assertEqual(
            self.registry.get_sample_value(
                "fetches_total", {"endpoint": "get_stats", "result": "success"}
            ),
            1,
        )

    def test_cycle_failures_preserve_last_success(self) -> None:
        def succeed() -> bool:
            self.assertEqual(self.registry.get_sample_value("in_progress"), 1)
            return True

        self.assertTrue(metrics.observe_cycle(succeed))
        previous = self.registry.get_sample_value("last_success")
        self.assertIsNotNone(previous)
        self.assertNotEqual(previous, 0)
        self.assertFalse(metrics.observe_cycle(lambda: False))
        with self.assertRaises(RuntimeError):
            metrics.observe_cycle(
                Mock(side_effect=RuntimeError("Database unavailable"))
            )
        self.assertEqual(self.registry.get_sample_value("last_success"), previous)
        self.assertEqual(
            self.registry.get_sample_value("cycles_total", {"result": "success"}), 1
        )
        self.assertEqual(
            self.registry.get_sample_value("cycles_total", {"result": "error"}), 2
        )
        self.assertEqual(self.registry.get_sample_value("in_progress"), 0)

    def test_interruption_is_not_a_failure(self) -> None:
        store = Mock()
        source = Mock(stop=Event())

        def stop(*_: object) -> bool:
            source.stop.set()
            return False

        source.day.side_effect = stop
        with (
            patch.object(cli, "derive") as derive,
            patch.object(cli, "collect_weather"),
            self.assertRaises(InterruptedError),
        ):
            metrics.observe_cycle(
                lambda: cli.collect_cycle(
                    store, source, date(2026, 10, 7), date(2026, 10, 8), {"steps"}, True
                )
            )
        self.assertEqual(source.day.call_count, 1)
        derive.assert_not_called()
        store.set_state.assert_called_once_with("pending-start", "2026-10-07")
        self.assertEqual(self.registry.get_sample_value("last_success"), 0)
        self.assertEqual(self.registry.get_sample_value("in_progress"), 0)
        self.assertIsNone(
            self.registry.get_sample_value("cycles_total", {"result": "error"})
        )

    def test_partial_cycle_does_not_advance_checkpoint(self) -> None:
        store = Mock()
        source = Mock(stop=Event())
        source.day.side_effect = [False, True]
        with patch.object(cli, "derive"), patch.object(cli, "collect_weather"):
            complete = metrics.observe_cycle(
                lambda: cli.collect_cycle(
                    store, source, date(2026, 10, 7), date(2026, 10, 8), {"steps"}, True
                )
            )
        self.assertFalse(complete)
        self.assertEqual(source.day.call_count, 2)
        store.set_state.assert_called_once_with("pending-start", "2026-10-07")
        self.assertEqual(self.registry.get_sample_value("last_success"), 0)

    def test_listener_serves_metrics_and_reports_busy_port(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        environment = {"METRICS_HOST": "127.0.0.1", "METRICS_PORT": str(port)}
        with patch.dict(os.environ, environment), metrics.serve():
            with urlopen(f"http://127.0.0.1:{port}/metrics") as response:
                self.assertIn(b"garmin_collection_cycles_total", response.read())
            with self.assertRaises(RuntimeError), metrics.serve():
                pass
