# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///

import base64
import bisect
import hashlib
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import accumulate, groupby
from zoneinfo import ZoneInfo

METERS_PER_MILE = 1609.344
WEATHER_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
WEATHER_VARIABLES = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
)
# The sleep chart shows clock times on one reference day in January, which has no
# daylight saving change. Its axis runs from 6 PM to 6 PM, so a night is never split.
SLEEP_REFERENCE_DAY = date(2000, 1, 3)
SLEEP_AXIS_START_HOUR = 18
SLEEP_STAGE_SAMPLE_SECONDS = 300
COMPASS_POINTS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)

# Change this value when a code change alters the best efforts. The next job
# then recomputes all runs.
ALGORITHM_VERSION = "2"


@dataclass(frozen=True)
class Distance:
    label: str
    meters: float


@dataclass(frozen=True)
class Point:
    time: int
    meters: float


@dataclass(frozen=True)
class Window:
    seconds: int
    meters: float


@dataclass(frozen=True)
class Activity:
    activity_id: str
    name: str
    kind: str
    city: str
    meters: float
    start: int


@dataclass(frozen=True)
class Effort:
    activity_id: str
    distance: str
    time: int
    seconds: float
    best: float | None
    record: float | None


@dataclass(frozen=True)
class StepRecord:
    period: str
    order: int
    steps: float
    title: str


@dataclass(frozen=True)
class Sleep:
    end: int
    asleep_seconds: int
    awake_seconds: int


@dataclass(frozen=True)
class Stage:
    start: int
    seconds: int
    level: int


@dataclass(frozen=True)
class Weather:
    temperature: float
    feels_like: float
    humidity: float
    wind_speed: float
    wind_direction: float


@dataclass(frozen=True)
class Influx:
    url: str
    database: str
    username: str
    password: str


def load_distances(value: str | None) -> list[Distance]:
    if not value:
        raise ValueError("DISTANCES is empty.")

    entries = json.loads(value)
    if not isinstance(entries, list) or not entries:
        raise ValueError("DISTANCES must contain a nonempty JSON list.")

    distances = []
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or set(entry) != {"label", "meters"}:
            raise ValueError(f"Distance entry {number} must contain label and meters.")

        label = entry["label"]
        meters = entry["meters"]
        if not isinstance(label, str) or not label:
            raise ValueError(f"Distance label {number} must be nonempty text.")
        if (
            isinstance(meters, bool)
            or not isinstance(meters, int | float)
            or not math.isfinite(meters)
            or meters <= 0
        ):
            raise ValueError(f"Distance meters {number} must be a positive number.")

        distances.append(Distance(label=label, meters=float(meters)))

    if len({distance.label for distance in distances}) != len(distances):
        raise ValueError("Distance labels must be unique.")

    return distances


def fingerprint(distances: Sequence[Distance]) -> str:
    payload = json.dumps(
        [ALGORITHM_VERSION, [[distance.label, distance.meters] for distance in distances]]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def fastest_window(points: Sequence[Point], meters: float) -> Window | None:
    """Return the quickest span that covers meters, or None if the run is too short.

    Cumulative distance must not decrease. Each end point is scored once, at the
    latest start that still covers the distance, so no candidate span is skipped.
    """
    best: Window | None = None
    start = 0
    for end, point in enumerate(points):
        while start < end and point.meters - points[start + 1].meters >= meters:
            start += 1
        covered = point.meters - points[start].meters
        if covered < meters:
            continue
        seconds = point.time - points[start].time
        if best is None or seconds < best.seconds:
            best = Window(seconds=seconds, meters=covered)
    return best


def format_duration(seconds: float) -> str:
    hours, remainder = divmod(round(seconds), 3600)
    minutes, remainder = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02}:{remainder:02}"
    return f"{minutes}:{remainder:02}"


def format_pace(seconds: float, meters: float) -> str:
    return f"{format_duration(seconds * METERS_PER_MILE / meters)} /mi"


def format_date(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


def format_range(start: date, end: date) -> str:
    if (start.year, start.month) == (end.year, end.month):
        return f"{start:%b} {start.day} to {end.day}, {end.year}"
    if start.year == end.year:
        return f"{start:%b} {start.day} to {end:%b} {end.day}, {end.year}"
    return f"{format_date(start)} to {format_date(end)}"


def local_date(time: int, zone: ZoneInfo) -> date:
    return datetime.fromtimestamp(time, zone).date()


def activity_label(activity: Activity, zone: ZoneInfo) -> str:
    """Return the dropdown text for an activity, for example
    "2026-10-05 Pflugerville - Tempo (Running, 2.51 mi)".

    The city and the activity type appear only when the name does not contain them.
    """
    name = activity.name or "Untitled"
    if activity.city and activity.city.lower() not in name.lower():
        name = f"{activity.city} - {name}"

    kind = activity.kind.replace("_", " ").capitalize()
    details = [kind] if kind and kind.lower() not in name.lower() else []
    if activity.meters > 0:
        details.append(f"{activity.meters / METERS_PER_MILE:.2f} mi")

    suffix = f" ({', '.join(details)})" if details else ""
    return f"{local_date(activity.start, zone).isoformat()} {name}{suffix}"


def escape_tag(value: str) -> str:
    return value.replace(",", r"\,").replace("=", r"\=").replace(" ", r"\ ")


def field_value(value: str | float) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return repr(float(value))


def line(
    measurement: str, tags: dict[str, str], fields: dict[str, str | float], time: int
) -> str:
    tag_text = "".join(f",{key}={escape_tag(value)}" for key, value in sorted(tags.items()))
    field_text = ",".join(f"{key}={field_value(value)}" for key, value in sorted(fields.items()))
    return f"{measurement}{tag_text} {field_text} {time}"


def index_lines(
    activities: Iterable[Activity], indexed: dict[str, str], zone: ZoneInfo
) -> list[str]:
    """Return ActivityIndex points for new and changed activities.

    Each Option holds the dropdown text and the activity ID, separated by "|".
    """
    options = (
        (activity, f"{activity_label(activity, zone)}|{activity.activity_id}")
        for activity in activities
    )
    return [
        line("ActivityIndex", {"ActivityID": activity.activity_id}, {"Option": option}, activity.start)
        for activity, option in options
        if indexed.get(activity.activity_id) != option
    ]


def run_lines(
    run: Activity,
    points: Sequence[Point],
    distances: Sequence[Distance],
    marker: str,
    zone: ZoneInfo,
) -> list[str]:
    run_date = format_date(local_date(run.start, zone))
    windows = ((distance, fastest_window(points, distance.meters)) for distance in distances)
    efforts = [
        line(
            "BestEffort",
            {"ActivityID": run.activity_id, "Distance": distance.label},
            {
                "ActivityName": run.name,
                "Date": run_date,
                "Duration": format_duration(window.seconds),
                "Meters": window.meters,
                "Pace": format_pace(window.seconds, window.meters),
                "Seconds": window.seconds,
            },
            run.start,
        )
        for distance, window in windows
        if window is not None
    ]
    done = line(
        "BestEffortRun",
        {"ActivityID": run.activity_id},
        {"ActivityName": run.name, "Fingerprint": marker},
        run.start,
    )
    return [*efforts, done]


def progression_lines(efforts: Iterable[Effort]) -> list[str]:
    """Return the Best and Record fields of efforts whose values changed.

    Best is the fastest time at the distance up to and including the effort.
    Record is 1 when the effort sets a new best, otherwise 0.
    """
    lines = []
    ordered = sorted(efforts, key=lambda effort: (effort.distance, effort.time))
    for distance, group in groupby(ordered, key=lambda effort: effort.distance):
        best = math.inf
        for effort in group:
            record = 1.0 if effort.seconds < best else 0.0
            best = min(best, effort.seconds)
            if (effort.best, effort.record) != (best, record):
                lines.append(
                    line(
                        "BestEffort",
                        {"ActivityID": effort.activity_id, "Distance": distance},
                        {"Best": best, "Record": record},
                        effort.time,
                    )
                )
    return lines


def step_totals(
    days: Iterable[tuple[date, int]], period_start: Callable[[date], date]
) -> dict[date, int]:
    totals: dict[date, int] = {}
    for day, steps in days:
        start = period_start(day)
        totals[start] = totals.get(start, 0) + steps
    return totals


def step_records(daily: dict[date, int]) -> list[StepRecord]:
    """Return the best day, week, and month. Weeks start on Monday. Ties keep the
    earliest period."""
    if not daily:
        return []

    days = sorted(daily.items())
    weeks = step_totals(days, lambda day: day - timedelta(days=day.weekday()))
    months = step_totals(days, lambda day: day.replace(day=1))
    best_day, day_steps = max(days, key=lambda item: item[1])
    week_start, week_steps = max(weeks.items(), key=lambda item: item[1])
    month_start, month_steps = max(months.items(), key=lambda item: item[1])
    week_end = week_start + timedelta(days=6)
    return [
        StepRecord("Day", 1, day_steps, f"Best day: {format_date(best_day)}"),
        StepRecord("Week", 2, week_steps, f"Best week: {format_range(week_start, week_end)}"),
        StepRecord("Month", 3, month_steps, f"Best month: {month_start:%B %Y}"),
    ]


def step_record_lines(
    records: Iterable[StepRecord], stored: dict[str, tuple[float, str]]
) -> list[str]:
    # Each period keeps one point at time 0, so a new record replaces the old one.
    return [
        line(
            "StepRecord",
            {"Period": record.period},
            {"Order": record.order, "Steps": record.steps, "Title": record.title},
            0,
        )
        for record in records
        if stored.get(record.period) != (record.steps, record.title)
    ]


def clock_position(moment: int, zone: ZoneInfo) -> float:
    """Return the local clock time of moment on the sleep reference day, in epoch ms."""
    local = datetime.fromtimestamp(moment, zone)
    day = SLEEP_REFERENCE_DAY + timedelta(days=int(local.hour < SLEEP_AXIS_START_HOUR))
    return datetime.combine(day, local.time(), zone).timestamp() * 1000


def sleep_start(sleep: Sleep) -> int:
    """Garmin stamps each night at the wake time. The night starts at the wake time
    minus the sleep and the awake durations."""
    return sleep.end - sleep.asleep_seconds - sleep.awake_seconds


def sleep_window(sleep: Sleep, zone: ZoneInfo) -> tuple[float, float]:
    return clock_position(sleep_start(sleep), zone), clock_position(sleep.end, zone)


def pending_sleeps(
    sleeps: Iterable[Sleep],
    stored: dict[int, tuple[float, float, float | None]],
    zone: ZoneInfo,
) -> list[Sleep]:
    """Return nights with a new or changed window, or without stage samples."""
    return [
        sleep
        for sleep in sleeps
        if stored.get(sleep.end, (None, None, None))[:2] != sleep_window(sleep, zone)
        or stored[sleep.end][2] is None
    ]


def stage_sample_lines(sleep: Sleep, stages: Sequence[Stage], zone: ZoneInfo) -> list[str]:
    """Return one SleepStage point every 5 minutes of the night.

    Each point carries the stage at that moment, the clock time on the sleep axis,
    and the local midnight of the wake date, so the chart draws one column per night.
    """
    night = local_date(sleep.end, zone)
    day = datetime.combine(night, time(), zone).timestamp() * 1000
    ordered = sorted(stages, key=lambda stage: stage.start)
    starts = [stage.start for stage in ordered]
    lines = []
    for moment in range(sleep_start(sleep), sleep.end, SLEEP_STAGE_SAMPLE_SECONDS):
        index = bisect.bisect_right(starts, moment) - 1
        if index < 0 or moment >= ordered[index].start + ordered[index].seconds:
            continue
        lines.append(
            line(
                "SleepStage",
                {"Night": night.isoformat()},
                {
                    "Clock": clock_position(moment, zone),
                    "Day": day,
                    "Stage": ordered[index].level,
                },
                moment,
            )
        )
    return lines


def sleep_window_line(sleep: Sleep, zone: ZoneInfo, samples: int) -> str:
    start, end = sleep_window(sleep, zone)
    return line("SleepWindow", {}, {"End": end, "Samples": samples, "Start": start}, sleep.end)


def weather_url(latitude: float, longitude: float, start: int) -> str:
    """Return the Open-Meteo request for the hour nearest to start.

    The coordinates have two decimals, about 1 km, which is finer than the weather model.
    """
    hour = datetime.fromtimestamp(round(start / 3600) * 3600, UTC).strftime("%Y-%m-%dT%H:%M")
    params = {
        "latitude": f"{latitude:.2f}",
        "longitude": f"{longitude:.2f}",
        "start_hour": hour,
        "end_hour": hour,
        "hourly": ",".join(WEATHER_VARIABLES),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "GMT",
    }
    return f"{WEATHER_URL}?{urllib.parse.urlencode(params)}"


def parse_weather(response: dict) -> Weather | None:
    hourly = response.get("hourly") or {}
    values = [(hourly.get(name) or [None])[0] for name in WEATHER_VARIABLES]
    if any(value is None for value in values):
        return None
    return Weather(*values)


def compass(degrees: float) -> str:
    return COMPASS_POINTS[round(degrees / 22.5) % len(COMPASS_POINTS)]


def weather_line(activity: Activity, weather: Weather) -> str:
    return line(
        "ActivityWeather",
        {"ActivityID": activity.activity_id},
        {
            "FeelsLike": weather.feels_like,
            "Humidity": weather.humidity,
            "Temperature": weather.temperature,
            "Wind": f"{round(weather.wind_speed)} mph {compass(weather.wind_direction)}",
        },
        activity.start,
    )


def pending(
    runs: Iterable[Activity],
    gps_ids: set[str],
    processed: dict[str, tuple[str, str]],
    marker: str,
) -> list[Activity]:
    return [
        run
        for run in runs
        if run.activity_id in gps_ids and processed.get(run.activity_id) != (marker, run.name)
    ]


def literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def request(
    influx: Influx, path: str, params: dict[str, str], body: bytes, content_type: str
) -> bytes:
    credentials = base64.b64encode(f"{influx.username}:{influx.password}".encode()).decode()
    url = f"{influx.url}{path}?{urllib.parse.urlencode(params)}"
    headers = {"Authorization": f"Basic {credentials}", "Content-Type": content_type}
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=body, headers=headers), timeout=60
        ) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        message = error.read().decode(errors="replace").strip()
        raise ValueError(f"InfluxDB returned HTTP {error.code}: {message}") from None


def query(influx: Influx, statement: str) -> list[dict]:
    body = urllib.parse.urlencode({"q": statement}).encode()
    response = json.loads(
        request(
            influx,
            "/query",
            {"db": influx.database, "epoch": "s"},
            body,
            "application/x-www-form-urlencoded",
        )
    )
    result = response["results"][0]
    if "error" in result:
        raise ValueError(f"InfluxDB query failed: {result['error']}")
    return [
        dict(zip(series["columns"], values))
        for series in result.get("series", [])
        for values in series["values"]
    ]


def write(influx: Influx, lines: Sequence[str]) -> None:
    request(
        influx,
        "/write",
        {"db": influx.database, "precision": "s"},
        "\n".join(lines).encode(),
        "text/plain; charset=utf-8",
    )


def load_activities(influx: Influx) -> list[Activity]:
    rows = query(
        influx,
        'SELECT "activityName", "activityType", "locationName", "distance", "ActivityID" '
        "FROM \"ActivitySummary\" WHERE \"activityType\" != 'No Activity'",
    )
    activities = {
        row["ActivityID"]: Activity(
            activity_id=row["ActivityID"],
            name=row["activityName"] or "",
            kind=row["activityType"] or "",
            city=row["locationName"] or "",
            meters=row["distance"] or 0.0,
            start=row["time"],
        )
        for row in rows
    }
    return list(activities.values())


def load_index(influx: Influx) -> dict[str, str]:
    rows = query(influx, 'SELECT "Option", "ActivityID" FROM "ActivityIndex"')
    return {row["ActivityID"]: row["Option"] for row in rows}


def load_gps_ids(influx: Influx) -> set[str]:
    rows = query(influx, 'SHOW TAG VALUES FROM "ActivityGPS" WITH KEY = "ActivityID"')
    return {row["value"] for row in rows}


def load_processed(influx: Influx) -> dict[str, tuple[str, str]]:
    rows = query(influx, 'SELECT "Fingerprint", "ActivityName", "ActivityID" FROM "BestEffortRun"')
    return {row["ActivityID"]: (row["Fingerprint"], row["ActivityName"]) for row in rows}


def load_sleeps(influx: Influx) -> list[Sleep]:
    rows = query(influx, 'SELECT "sleepTimeSeconds", "awakeSleepSeconds" FROM "SleepSummary"')
    sleeps = {
        row["time"]: Sleep(
            end=row["time"],
            asleep_seconds=row["sleepTimeSeconds"],
            awake_seconds=row["awakeSleepSeconds"] or 0,
        )
        for row in rows
        if row["sleepTimeSeconds"]
    }
    return list(sleeps.values())


def load_sleep_windows(influx: Influx) -> dict[int, tuple[float, float, float | None]]:
    rows = query(influx, 'SELECT "Start", "End", "Samples" FROM "SleepWindow"')
    return {row["time"]: (row["Start"], row["End"], row["Samples"]) for row in rows}


def load_stages(influx: Influx, sleep: Sleep) -> list[Stage]:
    rows = query(
        influx,
        'SELECT "SleepStageLevel", "SleepStageSeconds" FROM "SleepIntraday" '
        f"WHERE time >= {sleep_start(sleep)}s AND time < {sleep.end}s",
    )
    # Upstream repeats the last stage at the wake time without a duration.
    return [
        Stage(start=row["time"], seconds=row["SleepStageSeconds"], level=row["SleepStageLevel"])
        for row in rows
        if row["SleepStageLevel"] is not None and row["SleepStageSeconds"]
    ]


def load_weather_ids(influx: Influx) -> set[str]:
    rows = query(influx, 'SHOW TAG VALUES FROM "ActivityWeather" WITH KEY = "ActivityID"')
    return {row["value"] for row in rows}


def load_start(influx: Influx, activity_id: str) -> tuple[float, float] | None:
    rows = query(
        influx,
        'SELECT first("Latitude") AS "Latitude", first("Longitude") AS "Longitude" '
        f'FROM "ActivityGPS" WHERE "ActivityID" = {literal(activity_id)}',
    )
    if not rows or rows[0]["Latitude"] is None or rows[0]["Longitude"] is None:
        return None
    return rows[0]["Latitude"], rows[0]["Longitude"]


def fetch_weather(url: str) -> Weather | None:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return parse_weather(json.load(response))
    except urllib.error.HTTPError as error:
        message = error.read().decode(errors="replace").strip()
        raise ValueError(f"Open-Meteo returned HTTP {error.code}: {message}") from None


def load_efforts(influx: Influx) -> list[Effort]:
    rows = query(
        influx,
        'SELECT "Seconds", "Best", "Record", "ActivityID", "Distance" FROM "BestEffort"',
    )
    return [
        Effort(
            activity_id=row["ActivityID"],
            distance=row["Distance"],
            time=row["time"],
            seconds=row["Seconds"],
            best=row["Best"],
            record=row["Record"],
        )
        for row in rows
    ]


def load_daily_steps(influx: Influx, zone: ZoneInfo) -> dict[date, int]:
    rows = query(influx, 'SELECT "totalSteps" FROM "DailyStats"')
    return {
        local_date(row["time"], zone): row["totalSteps"]
        for row in rows
        if row["totalSteps"] is not None
    }


def load_step_records(influx: Influx) -> dict[str, tuple[float, str]]:
    rows = query(influx, 'SELECT "Steps", "Title", "Period" FROM "StepRecord"')
    return {row["Period"]: (row["Steps"], row["Title"]) for row in rows}


def load_points(influx: Influx, activity_id: str) -> list[Point]:
    rows = query(
        influx,
        f'SELECT "Distance" FROM "ActivityGPS" WHERE "ActivityID" = {literal(activity_id)}',
    )
    # GPS noise can lower the cumulative distance, and the window search needs it to grow.
    meters = accumulate((row["Distance"] for row in rows), max)
    return [Point(time=row["time"], meters=value) for row, value in zip(rows, meters)]


def environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is empty.")
    return value


def main() -> None:
    distances = load_distances(os.environ.get("DISTANCES"))
    zone = ZoneInfo(environment("TZ"))
    influx = Influx(
        url=environment("INFLUXDB_URL"),
        database=environment("INFLUXDB_DATABASE"),
        username=environment("INFLUXDB_USERNAME"),
        password=environment("INFLUXDB_PASSWORD"),
    )
    activities = load_activities(influx)

    index = index_lines(activities, load_index(influx), zone)
    if index:
        write(influx, index)
    print(f"Indexed {len(index)} activities.")

    marker = fingerprint(distances)
    processed = load_processed(influx)
    gps_ids = load_gps_ids(influx)
    all_runs = [activity for activity in activities if "running" in activity.kind]
    runs = pending(all_runs, gps_ids, processed, marker)
    for run in runs:
        if run.activity_id in processed:
            query(influx, f'DELETE FROM "BestEffort" WHERE "ActivityID" = {literal(run.activity_id)}')
        lines = run_lines(run, load_points(influx, run.activity_id), distances, marker, zone)
        write(influx, lines)
        print(f"Recorded {len(lines) - 1} best efforts for run {run.activity_id} ({run.name}).")
    print(f"Updated best efforts for {len(runs)} runs.")

    progression = progression_lines(load_efforts(influx))
    if progression:
        write(influx, progression)
    print(f"Updated the record progression of {len(progression)} efforts.")

    steps = step_record_lines(
        step_records(load_daily_steps(influx, zone)), load_step_records(influx)
    )
    if steps:
        write(influx, steps)
    print(f"Updated {len(steps)} step records.")

    nights = pending_sleeps(load_sleeps(influx), load_sleep_windows(influx), zone)
    for sleep in nights:
        night = local_date(sleep.end, zone).isoformat()
        query(influx, f'DELETE FROM "SleepStage" WHERE "Night" = {literal(night)}')
        samples = stage_sample_lines(sleep, load_stages(influx, sleep), zone)
        write(influx, [*samples, sleep_window_line(sleep, zone, len(samples))])
    print(f"Updated {len(nights)} nights of sleep.")

    # Weather comes last, so an Open-Meteo failure does not delay the other updates.
    weather_ids = load_weather_ids(influx)
    added = 0
    for activity in activities:
        if activity.activity_id not in gps_ids or activity.activity_id in weather_ids:
            continue
        start = load_start(influx, activity.activity_id)
        if start is None:
            continue
        weather = fetch_weather(weather_url(*start, activity.start))
        if weather is not None:
            write(influx, [weather_line(activity, weather)])
            added += 1
    print(f"Added weather to {added} activities.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print(f"Derive failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
