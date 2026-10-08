import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from .storage import Fields, Rows, Store, digest

METERS_PER_MILE = 1609.344
WEATHER_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
WEATHER_VARIABLES = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
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


def best_efforts(
    points: Sequence[Point], distances: Sequence[Distance]
) -> list[Fields]:
    efforts = []
    for distance in distances:
        window = fastest_window(points, distance.meters)
        if window is not None:
            efforts.append(
                {
                    "distance": distance.label,
                    "meters": distance.meters,
                    "covered_meters": window.meters,
                    "seconds": window.seconds,
                    "duration": format_duration(window.seconds),
                    "pace": format_pace(window.seconds, window.meters),
                }
            )
    return efforts


def derive(store: Store, distances: Sequence[Distance]) -> None:
    activities = store.connection.execute(
        'SELECT a."activityId", t."typeKey" FROM garmin.activities a '
        'LEFT JOIN garmin."activityType" t ON t."typeId" = a."activityType"'
    ).fetchall()
    for activity in activities:
        entity = activity["activityId"]
        marker = digest(
            [
                store.state(f"activity:{entity}"),
                [(distance.label, distance.meters) for distance in distances],
            ]
        )
        key = f"derive:{entity}"
        if store.state(key) == marker:
            continue
        efforts = []
        if "running" in (activity["typeKey"] or ""):
            points: list[Point] = []
            meters = 0.0
            for row in store.connection.execute(
                'SELECT "timestamp", distance FROM garmin.fit_record '
                'WHERE "activityId" = %s AND distance IS NOT NULL ORDER BY "timestamp"',
                (entity,),
            ):
                meters = max(meters, row["distance"])
                points.append(Point(row["timestamp"].timestamp(), meters))
            efforts = best_efforts(points, distances)
        with store.connection.transaction():
            store.replace([Rows("best_effort", {"activity_id": entity}, efforts)])
            store.set_state(key, marker)
