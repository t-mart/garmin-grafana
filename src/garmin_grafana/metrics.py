import os
from collections.abc import Callable, Generator
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, start_http_server

FETCHES = Counter(
    "garmin_fetches_total",
    "Completed API calls after client retries.",
    ["endpoint", "result"],
)
CYCLES = Counter(
    "garmin_collection_cycles_total", "Completed collection cycles.", ["result"]
)
LAST_SUCCESS = Gauge(
    "garmin_last_collection_success_timestamp_seconds",
    "Unix time of the last successful collection cycle.",
)
IN_PROGRESS = Gauge(
    "garmin_collection_in_progress", "One during a collection cycle, otherwise zero."
)

for result in ("success", "error"):
    CYCLES.labels(result)


def observe_cycle(action: Callable[[], bool]) -> bool:
    with IN_PROGRESS.track_inprogress():
        try:
            success = action()
        except InterruptedError:
            raise
        except Exception:
            CYCLES.labels("error").inc()
            raise
        CYCLES.labels("success" if success else "error").inc()
        if success:
            LAST_SUCCESS.set_to_current_time()
        return success


@contextmanager
def serve() -> Generator[None]:
    port = int(os.getenv("METRICS_PORT", "9000"))
    if port == 0:
        yield
        return
    host = os.getenv("METRICS_HOST", "0.0.0.0")
    try:
        server, thread = start_http_server(port, addr=host)
    except OSError as error:
        raise RuntimeError(
            f"The metrics listener cannot bind {host}:{port} ({error.strerror})."
        ) from None
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
