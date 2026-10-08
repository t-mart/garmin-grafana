import argparse
import logging
import os
import signal
from datetime import date, datetime, timedelta
from threading import Event
from zoneinfo import ZoneInfo

from .collector import DEFAULT_SELECTION, ENDPOINTS, Collector, collect_weather, login
from .derive import DISTANCES, derive
from .storage import Store, connect


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Archive Garmin data in TimescaleDB.")
    result.add_argument(
        "command",
        nargs="?",
        choices=("sync", "init", "login", "derive"),
        default="sync",
    )
    result.add_argument(
        "--once", action="store_true", help="Fetch the date range, then exit."
    )
    result.add_argument(
        "--start", type=date.fromisoformat, default=os.getenv("MANUAL_START_DATE")
    )
    result.add_argument(
        "--end", type=date.fromisoformat, default=os.getenv("MANUAL_END_DATE")
    )
    result.add_argument(
        "--force", action="store_true", help="Download unchanged activities again."
    )
    return result


def run(args: argparse.Namespace) -> None:
    if args.command == "login":
        login()
        return
    zone = ZoneInfo(os.getenv("USER_TIMEZONE") or os.getenv("TZ") or "America/Chicago")
    selection = {
        value.strip()
        for value in os.getenv("FETCH_SELECTION", DEFAULT_SELECTION).split(",")
    }
    unknown = selection - ENDPOINTS.keys() - {"activity"}
    if unknown:
        raise ValueError(f"Unknown FETCH_SELECTION: {', '.join(sorted(unknown))}")
    delay = float(os.getenv("RATE_LIMIT_CALLS_SECONDS", "5"))
    interval = float(os.getenv("UPDATE_INTERVAL_SECONDS", "300"))
    if delay < 0 or interval <= 0:
        raise ValueError(
            "The request delay must be nonnegative. The update interval must be positive."
        )
    with connect(os.getenv("DATABASE_URL", "")) as connection:
        store = Store(connection)
        if args.command == "init":
            store.initialize()
            return
        acquired = connection.execute(
            "SELECT pg_try_advisory_lock(74190326) AS acquired"
        ).fetchone()
        if not acquired or not acquired["acquired"]:
            raise ValueError("Another collector holds the database lock.")
        if args.command == "derive":
            derive(store, DISTANCES, zone)
            collect_weather(store)
            return
        stop = Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        collector = Collector(login(), store, zone, delay, args.force, stop)
        collector.fetch("get_user_profile")
        while not stop.is_set():
            today = datetime.now(zone).date()
            end = args.end or today
            last = store.state("pending-start") or store.state("last-success")
            start = args.start or min(
                end - timedelta(days=2),
                date.fromisoformat(last) if last and not args.end else end,
            )
            if start > end:
                raise ValueError("The start date must not follow the end date.")
            complete = True
            if not args.start and not args.end:
                store.set_state("pending-start", start.isoformat())
            collector.device_sync()
            day = end
            while day >= start and not stop.is_set():
                complete = collector.day(day, selection) and complete
                day -= timedelta(days=1)
            derive(store, DISTANCES, zone)
            collect_weather(store)
            if complete and not stop.is_set() and not args.start and not args.end:
                store.set_state("last-success", today.isoformat())
                store.set_state("pending-start", None)
            if args.once or args.start or args.end:
                if not complete:
                    raise RuntimeError(
                        "Some Garmin requests failed. Repeat the date range to retry them."
                    )
                return
            stop.wait(interval)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    try:
        run(parser().parse_args())
    except (ValueError, RuntimeError) as error:
        logging.error("%s", error)
        raise SystemExit(1) from None
    except Exception as error:
        logging.error("Collector failed (%s)", type(error).__name__)
        raise SystemExit(1) from None
