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
from zoneinfo import ZoneInfo

import fitdecode
import httpx2
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from .activity import fit_samples, summary_sample, tcx_samples
from .derive import compass, parse_weather, weather_url
from .metrics import FETCHES
from .normalize import daily_samples
from .storage import Fields, Sample, Store, digest, timestamp

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
        zone: ZoneInfo,
        delay: float,
        force: bool = False,
        stop: Event | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.zone = zone
        self.delay = delay
        self.force = force
        self.stop = stop or Event()

    def fetch(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self.stop.wait(self.delay):
            raise InterruptedError("Collection stopped.")
        function: Callable[..., Any] = getattr(self.client, method)
        payload = request(method, lambda: function(*args, **kwargs))
        key = digest([args, kwargs])
        self.store.archive(
            method, key, {"args": args, "kwargs": kwargs, "data": payload}
        )
        return payload

    def day(self, day: date, selection: set[str]) -> bool:
        complete = True
        for kind in sorted(selection):
            if self.stop.is_set():
                return False
            try:
                if kind == "activity":
                    activities = self.fetch(
                        "get_activities_by_date", day.isoformat(), day.isoformat()
                    )
                    for activity in activities:
                        self.activity(activity)
                else:
                    value = day.isoformat()
                    if kind == "race_prediction":
                        payload = self.fetch(
                            ENDPOINTS[kind],
                            startdate=value,
                            enddate=value,
                            _type="daily",
                        )
                    elif kind == "lactate_threshold":
                        payload = self.fetch(
                            ENDPOINTS[kind],
                            latest=False,
                            start_date=value,
                            end_date=value,
                        )
                    elif kind == "body_composition":
                        payload = self.fetch(ENDPOINTS[kind], value, value)
                    else:
                        payload = self.fetch(ENDPOINTS[kind], value)
                    samples = daily_samples(kind, payload, day, self.zone)
                    self.store.replace(f"{day}:{kind}", samples)
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
        zones = self.fetch("get_activity_hr_in_timezones", activity_id) or []
        summary = summary_sample(activity, zones)
        self.fetch("get_activity_details", activity_id)
        self.fetch("get_activity_splits", activity_id)
        if "strength" in summary.fields["activityType"]:
            self.fetch("get_activity_exercise_sets", activity_id)
        content = self.download(activity_id, Garmin.ActivityDownloadFormat.ORIGINAL)
        samples = []
        if content:
            self.store.archive_file(activity_id, content)
            try:
                samples = fit_samples(content, activity_id, summary.time)
            except fitdecode.FitError, ValueError:
                LOG.warning("FIT decode failed for activity %s; try TCX", activity_id)
        if not samples:
            content = self.download(activity_id, Garmin.ActivityDownloadFormat.TCX)
            if content:
                self.store.archive_file(f"{activity_id}.tcx", content)
                samples = tcx_samples(content, activity_id, summary.time)
        with self.store.connection.transaction():
            self.store.replace(key, [summary, *samples])
            self.store.set_state(
                key, {"summary": marker, "file": hashlib.sha256(content).hexdigest()}
            )

    def download(
        self, activity_id: str, format: Garmin.ActivityDownloadFormat
    ) -> bytes:
        if self.stop.wait(self.delay):
            raise InterruptedError("Collection stopped.")
        try:
            return request(
                f"download_activity_{format.name.lower()}",
                lambda: self.client.download_activity(activity_id, dl_fmt=format),
            )
        except Exception as error:
            if status_code(error) in {204, 404}:
                return b""
            raise

    def device_sync(self) -> None:
        data = self.fetch("get_device_last_used") or {}
        if data.get("lastUsedDeviceUploadTime"):
            self.store.replace(
                "device",
                [
                    Sample(
                        "DeviceSync", timestamp(data["lastUsedDeviceUploadTime"]), data
                    )
                ],
            )


def collect_weather(store: Store) -> None:
    with httpx2.Client(timeout=30) as client:
        for summary in store.samples("ActivitySummary"):
            marker = digest(
                [
                    summary.time,
                    summary.fields.get("startLatitude"),
                    summary.fields.get("startLongitude"),
                    store.state(f"activity:{summary.entity}"),
                ]
            )
            key = f"weather:{summary.entity}"
            if store.state(key) == marker:
                continue
            point = next(
                (
                    row
                    for row in store.samples("ActivityGPS", summary.entity)
                    if row.fields.get("Latitude") is not None
                    and row.fields.get("Longitude") is not None
                ),
                None,
            )
            if point is None:
                continue
            url = weather_url(
                point.fields["Latitude"],
                point.fields["Longitude"],
                int(summary.time.timestamp()),
            )
            try:
                payload = request(
                    "open_meteo",
                    lambda url=url: client.get(url).raise_for_status().json(),
                )
                store.archive(
                    "open-meteo", summary.entity, {"url": url, "data": payload}
                )
                weather = parse_weather(payload)
                if weather is None:
                    continue
                fields = {
                    "Temperature": weather.temperature,
                    "FeelsLike": weather.feels_like,
                    "Humidity": weather.humidity,
                    "WindSpeed": weather.wind_speed,
                    "WindDirection": weather.wind_direction,
                    "Wind": f"{round(weather.wind_speed)} mph {compass(weather.wind_direction)}",
                }
                with store.connection.transaction():
                    store.replace(
                        key,
                        [
                            Sample(
                                "ActivityWeather", summary.time, fields, summary.entity
                            )
                        ],
                    )
                    store.set_state(key, marker)
            except httpx2.HTTPError:
                LOG.warning("Weather request failed for activity %s", summary.entity)
