import urllib.parse
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .storage import Sample, Store

METERS_PER_MILE = 1609.344
WEATHER_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
WEATHER_VARIABLES = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
)
SLEEP_REFERENCE_DAY = date(2000, 1, 3)
SLEEP_AXIS_START_HOUR = 18
COMPASS_POINTS = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)


@dataclass(frozen=True)
class Distance:
    label: str
    meters: float


DISTANCES = (
    Distance("1 km", 1000),
    Distance("1 mi", METERS_PER_MILE),
    Distance("5 km", 5000),
    Distance("5 mi", 5 * METERS_PER_MILE),
    Distance("10 km", 10000),
)


@dataclass(frozen=True)
class Point:
    time: float
    meters: float


@dataclass(frozen=True)
class Window:
    seconds: float
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
class StepRecord:
    period: str
    order: int
    steps: float
    title: str


@dataclass(frozen=True)
class Weather:
    temperature: float
    feels_like: float
    humidity: float
    wind_speed: float
    wind_direction: float


def fastest_window(points: Sequence[Point], meters: float) -> Window | None:
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
    name = activity.name or "Untitled"
    if activity.city and activity.city.lower() not in name.lower():
        name = f"{activity.city} - {name}"
    kind = activity.kind.replace("_", " ").capitalize()
    details = [kind] if kind and kind.lower() not in name.lower() else []
    if activity.meters > 0:
        details.append(f"{activity.meters / METERS_PER_MILE:.2f} mi")
    suffix = f" ({', '.join(details)})" if details else ""
    return f"{local_date(activity.start, zone).isoformat()} {name}{suffix}"


def step_totals(
    days: Iterable[tuple[date, int]], period_start: Callable[[date], date]
) -> dict[date, int]:
    totals: dict[date, int] = {}
    for day, steps in days:
        start = period_start(day)
        totals[start] = totals.get(start, 0) + steps
    return totals


def step_records(daily: dict[date, int]) -> list[StepRecord]:
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
        StepRecord(
            "Week", 2, week_steps, f"Best week: {format_range(week_start, week_end)}"
        ),
        StepRecord("Month", 3, month_steps, f"Best month: {month_start:%B %Y}"),
    ]


def clock_position(moment: int, zone: ZoneInfo) -> float:
    local = datetime.fromtimestamp(moment, zone)
    day = SLEEP_REFERENCE_DAY + timedelta(days=int(local.hour < SLEEP_AXIS_START_HOUR))
    return datetime.combine(day, local.time(), zone).timestamp() * 1000


def weather_url(latitude: float, longitude: float, start: int) -> str:
    hour = datetime.fromtimestamp(round(start / 3600) * 3600, UTC).strftime(
        "%Y-%m-%dT%H:%M"
    )
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


def parse_weather(response: dict[str, Any]) -> Weather | None:
    hourly = response.get("hourly") or {}
    values = [(hourly.get(name) or [None])[0] for name in WEATHER_VARIABLES]
    if any(value is None for value in values):
        return None
    return Weather(*(float(value) for value in values if value is not None))


def compass(degrees: float) -> str:
    return COMPASS_POINTS[round(degrees / 22.5) % len(COMPASS_POINTS)]


def activity_from_sample(sample: Sample) -> Activity:
    fields = sample.fields
    return Activity(
        sample.entity,
        fields.get("activityName") or "",
        fields.get("activityType") or "",
        fields.get("locationName") or "",
        fields.get("distance") or 0,
        int(sample.time.timestamp()),
    )


def effort_samples(
    activity: Activity,
    samples: Sequence[Sample],
    distances: Sequence[Distance],
    zone: ZoneInfo,
) -> list[Sample]:
    points: list[Point] = []
    meters = 0.0
    for sample in samples:
        value = sample.fields.get("Distance")
        if value is not None:
            meters = max(meters, value)
            points.append(Point(sample.time.timestamp(), meters))
    efforts = []
    for distance in distances:
        window = fastest_window(points, distance.meters)
        if window is not None:
            efforts.append(
                Sample(
                    "BestEffort",
                    datetime.fromtimestamp(activity.start, UTC),
                    {
                        "Distance": distance.label,
                        "ActivityName": activity.name,
                        "Date": format_date(local_date(activity.start, zone)),
                        "Duration": format_duration(window.seconds),
                        "Meters": distance.meters,
                        "CoveredMeters": window.meters,
                        "Pace": format_pace(window.seconds, window.meters),
                        "Seconds": window.seconds,
                    },
                    f"{activity.activity_id}:{distance.label}",
                )
            )
    return efforts


def derive(store: Store, distances: Sequence[Distance], zone: ZoneInfo) -> None:
    from .storage import digest

    for sample in store.samples("ActivitySummary"):
        activity = activity_from_sample(sample)
        marker = digest(
            [
                sample.fields,
                sample.time,
                store.state(f"activity:{sample.entity}"),
                [(distance.label, distance.meters) for distance in distances],
                zone.key,
            ]
        )
        key = f"derive:{sample.entity}"
        if store.state(key) == marker:
            continue
        fields = {"Label": activity_label(activity, zone)}
        efforts = (
            effort_samples(
                activity, store.samples("ActivityGPS", sample.entity), distances, zone
            )
            if "running" in activity.kind
            else []
        )
        with store.connection.transaction():
            store.replace(
                key,
                [Sample("ActivityIndex", sample.time, fields, sample.entity), *efforts],
            )
            store.set_state(key, marker)
    daily = {
        sample.time.astimezone(zone).date(): sample.fields["totalSteps"]
        for sample in store.samples("DailyStats")
        if sample.fields.get("totalSteps") is not None
    }
    store.replace(
        "step-records",
        [
            Sample(
                "StepRecord",
                datetime(1970, 1, 1, tzinfo=UTC),
                {"Order": record.order, "Steps": record.steps, "Title": record.title},
                record.period,
            )
            for record in step_records(daily)
        ],
    )
