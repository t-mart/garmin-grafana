import base64
import hashlib
import logging
import os
import re
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from threading import Event
from typing import Any

import fitdecode
import httpx2
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from .derive import weather_url
from .metrics import FETCHES
from .project import fit, project
from .storage import Fields, Store, digest

ENDPOINTS = {
    "daily_avg": "get_stats",
    "sleep": "get_sleep_data",
    "steps": "get_steps_data",
    "heartrate": "get_heart_rates",
    "stress": "get_stress_data",
    "breathing": "get_respiration_data",
    "hrv": "get_hrv_data",
    "fitness_age": "get_fitnessage_data",
    "vo2": "get_max_metrics",
    "race_prediction": "get_race_predictions",
    "body_composition": "get_weigh_ins",
    "lifestyle": "get_lifestyle_logging_data",
    "training_status": "get_training_status",
    "training_readiness": "get_training_readiness",
    "hill_score": "get_hill_score",
    "endurance_score": "get_endurance_score",
    "blood_pressure": "get_blood_pressure",
    "hydration": "get_hydration_data",
    "lactate_threshold": "get_lactate_threshold",
}
DEFAULT_SELECTION = "daily_avg,sleep,steps,heartrate,stress,breathing,hrv,fitness_age,vo2,activity,race_prediction,body_composition,lifestyle"
LOG = logging.getLogger(__name__)


def mfa_code() -> str:
    if not sys.stdin.isatty():
        raise ValueError(
            "MFA requires an interactive login. Run garmin-grafana login in a terminal."
        )
    return input("Garmin MFA code: ").strip()


def login() -> Garmin:
    encoded = os.getenv("GARMINCONNECT_BASE64_PASSWORD")
    password = base64.b64decode(encoded, validate=True).decode() if encoded else None
    client = Garmin(
        email=os.getenv("GARMINCONNECT_EMAIL"),
        password=password,
        is_cn=os.getenv("GARMINCONNECT_IS_CN", "false").lower() == "true",
        prompt_mfa=mfa_code,
    )
    token_dir = Path(os.getenv("TOKEN_DIR", ".local/tokens")).expanduser()
    token_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    request("login", lambda: client.login(str(token_dir)))
    return client


def status_code(error: Exception) -> int | None:
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status is not None:
        return int(status)
    if isinstance(error.__cause__, Exception):
        return status_code(error.__cause__)
    match = re.search(r"(?:API Error|Error|HTTP)\s*(\d{3})", str(error))
    return int(match[1]) if match else None


def request[T](endpoint: str, action: Callable[[], T]) -> T:
    for result in ("success", "error", "empty"):
        FETCHES.labels(endpoint, result)
    try:
        value = action()
    except InterruptedError:
        raise
    except Exception as error:
        result = "empty" if status_code(error) in {204, 404} else "error"
        FETCHES.labels(endpoint, result).inc()
        raise
    FETCHES.labels(endpoint, "success").inc()
    return value


class Collector:
    def __init__(
        self,
        client: Garmin,
        store: Store,
        delay: float,
        force: bool = False,
        stop: Event | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.delay = delay
        self.force = force
        self.stop = stop or Event()

    def fetch(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self.stop.wait(self.delay):
            raise InterruptedError("Collection stopped.")
        function: Callable[..., Any] = getattr(self.client, method)
        data = request(method, lambda: function(*args, **kwargs))
        key = digest([args, kwargs])
        payload = {"args": args, "kwargs": kwargs, "data": data}
        self.store.archive(method, key, payload)
        self.store.replace(project(method, key, payload))
        return data

    def day(self, day: date, selection: set[str]) -> bool:
        complete = True
        value = day.isoformat()
        for kind in sorted(selection):
            if self.stop.is_set():
                return False
            try:
                if kind == "activity":
                    for activity in (
                        self.fetch("get_activities_by_date", value, value) or []
                    ):
                        self.activity(activity)
                elif kind == "race_prediction":
                    self.fetch(
                        ENDPOINTS[kind], startdate=value, enddate=value, _type="daily"
                    )
                elif kind == "lactate_threshold":
                    self.fetch(
                        ENDPOINTS[kind], latest=False, start_date=value, end_date=value
                    )
                elif kind == "body_composition":
                    self.fetch(ENDPOINTS[kind], value, value)
                else:
                    self.fetch(ENDPOINTS[kind], value)
                LOG.info("Stored %s for %s", kind, day)
            except GarminConnectAuthenticationError, GarminConnectTooManyRequestsError:
                raise
            except InterruptedError:
                return False
            except Exception as error:
                if status_code(error) in {204, 404}:
                    LOG.info("No %s endpoint data for %s", kind, day)
                    continue
                complete = False
                LOG.error("Failed %s for %s (%s)", kind, day, type(error).__name__)
        return complete

    def activity(self, activity: Fields) -> None:
        activity_id = str(activity["activityId"])
        key = f"activity:{activity_id}"
        marker = digest(activity)
        stored = self.store.state(key)
        if (
            not self.force
            and isinstance(stored, dict)
            and stored.get("summary") == marker
        ):
            return
        self.fetch("get_activity_hr_in_timezones", activity_id)
        self.fetch("get_activity_details", activity_id)
        self.fetch("get_activity_splits", activity_id)
        if "strength" in (activity.get("activityType") or {}).get("typeKey", ""):
            self.fetch("get_activity_exercise_sets", activity_id)
        content = self.download(activity_id)
        projection = []
        if content:
            self.store.archive_file(activity_id, content)
            try:
                projection = fit(activity_id, content)
            except fitdecode.FitError, ValueError:
                LOG.warning("FIT decode failed for activity %s", activity_id)
        with self.store.connection.transaction():
            self.store.replace(projection)
            self.store.set_state(
                key, {"summary": marker, "file": hashlib.sha256(content).hexdigest()}
            )

    def download(self, activity_id: str) -> bytes:
        if self.stop.wait(self.delay):
            raise InterruptedError("Collection stopped.")
        try:
            return request(
                "download_activity_original",
                lambda: self.client.download_activity(
                    activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
                ),
            )
        except Exception as error:
            if status_code(error) in {204, 404}:
                return b""
            raise

    def device_sync(self) -> None:
        self.fetch("get_device_last_used")


def collect_weather(store: Store) -> None:
    activities = store.connection.execute(
        'SELECT "activityId", "startTimeGMT", "startLatitude", "startLongitude" '
        'FROM garmin.activities WHERE "startLatitude" IS NOT NULL '
        'AND "startLongitude" IS NOT NULL'
    ).fetchall()
    with httpx2.Client(timeout=30) as client:
        for activity in activities:
            entity = str(activity["activityId"])
            url = weather_url(
                activity["startLatitude"],
                activity["startLongitude"],
                int(activity["startTimeGMT"].timestamp()),
            )
            key = f"weather:{entity}"
            if store.state(key) == url:
                continue
            try:
                data = request(
                    "open_meteo",
                    lambda url=url: client.get(url).raise_for_status().json(),
                )
            except httpx2.HTTPError:
                LOG.warning("Weather request failed for activity %s", entity)
                continue
            payload = {"url": url, "data": data}
            store.archive("open-meteo", entity, payload)
            with store.connection.transaction():
                store.replace(project("open-meteo", entity, payload))
                store.set_state(key, url)


def rebuild(store: Store) -> None:
    with store.connection.transaction():
        store.connection.execute("DELETE FROM garmin.state WHERE key LIKE 'derive:%'")
        with store.connection.cursor(name="responses") as cursor:
            cursor.execute(
                "SELECT DISTINCT ON (endpoint, key) endpoint, key, payload "
                "FROM garmin.responses ORDER BY endpoint, key, last_seen DESC"
            )
            for row in cursor:
                store.replace(project(row["endpoint"], row["key"], row["payload"]))
                if row["endpoint"] == "open-meteo":
                    store.set_state(f"weather:{row['key']}", row["payload"]["url"])
        with store.connection.cursor(name="files") as cursor:
            cursor.execute(
                "SELECT DISTINCT ON (key) key, content FROM garmin.files "
                "WHERE key NOT LIKE '%.tcx' ORDER BY key, captured_at DESC"
            )
            for row in cursor:
                try:
                    store.replace(fit(row["key"], bytes(row["content"])))
                except fitdecode.FitError, ValueError:
                    LOG.warning("FIT decode failed for activity %s", row["key"])
