import io
import zipfile
from collections.abc import Callable, Iterable
from typing import Any

import fitdecode

from .storage import Fields, Rows

ACCOUNT_FIELDS = (
    "ownerId",
    "ownerFullName",
    "ownerDisplayName",
    "ownerProfileImageUrlSmall",
    "ownerProfileImageUrlMedium",
    "ownerProfileImageUrlLarge",
    "userPro",
)
SLEEP_ARRAYS = (
    "sleepLevels",
    "sleepMovement",
    "sleepRestlessMoments",
    "breathingDisruptionData",
    "wellnessEpochSPO2DataDTOList",
    "wellnessEpochRespirationAveragesList",
)
FIT_MESSAGES = {
    "record": "timestamp",
    "lap": "message_index",
    "session": "message_index",
    "length": "message_index",
}


def rows(table: str, key: Fields, values: Iterable[Fields]) -> Rows:
    return Rows(table, key, list(values))


def listed(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []


def scalars(value: Fields | None, skip: Iterable[str] = ()) -> Fields:
    return {
        name: item
        for name, item in (value or {}).items()
        if not isinstance(item, dict | list) and name not in skip
    }


def one(value: Fields | None) -> list[Fields]:
    return [scalars(value)] if value else []


def each(values: Any) -> list[Fields]:
    return [scalars(value) for value in listed(values)]


def named(values: Fields | None) -> list[Fields]:
    return [{**scalars(value), "key": name} for name, value in (values or {}).items()]


def decode(
    values: Any, descriptors: Any, key: str = "key", index: str = "index"
) -> list[Fields]:
    names = {item[index]: item[key] for item in descriptors or []}
    return [
        {
            names[position]: item
            for position, item in enumerate(value)
            if position in names
        }
        for value in values or []
    ]


def usersummary(day: str, data: Any) -> list[Rows]:
    data = data or {}
    key = {"calendarDate": day}
    return [
        rows("usersummary", key, one(data)),
        rows(
            "bodyBatteryActivityEventList",
            key,
            each(data.get("bodyBatteryActivityEventList")),
        ),
    ]


def daily_heart_rate(day: str, data: Any) -> list[Rows]:
    data = data or {}
    key = {"calendarDate": day}
    return [
        rows("dailyHeartRate", key, one(data)),
        rows(
            "heartRateValues",
            key,
            decode(data.get("heartRateValues"), data.get("heartRateValueDescriptors")),
        ),
    ]


def daily_stress(day: str, data: Any) -> list[Rows]:
    data = data or {}
    key = {"calendarDate": day}
    return [
        rows("dailyStress", key, one(data)),
        rows(
            "stressValuesArray",
            key,
            decode(
                data.get("stressValuesArray"), data.get("stressValueDescriptorsDTOList")
            ),
        ),
        rows(
            "bodyBatteryValuesArray",
            key,
            decode(
                data.get("bodyBatteryValuesArray"),
                data.get("bodyBatteryValueDescriptorsDTOList"),
                "bodyBatteryValueDescriptorKey",
                "bodyBatteryValueDescriptorIndex",
            ),
        ),
    ]


def respiration(day: str, data: Any) -> list[Rows]:
    data = data or {}
    key = {"calendarDate": day}
    return [
        rows("respiration", key, one(data)),
        rows(
            "respirationValuesArray",
            key,
            decode(
                data.get("respirationValuesArray"),
                data.get("respirationValueDescriptorsDTOList"),
            ),
        ),
        rows(
            "respirationAveragesValuesArray",
            key,
            decode(
                data.get("respirationAveragesValuesArray"),
                data.get("respirationAveragesValueDescriptorDTOList"),
                "respirationAveragesValueDescriptionKey",
                "respirationAveragesValueDescriptorIndex",
            ),
        ),
    ]


def hrv(day: str, data: Any) -> list[Rows]:
    data = data or {}
    summary = data.get("hrvSummary")
    key = {"calendarDate": day}
    return [
        rows(
            "hrvSummary",
            key,
            [{**scalars(summary.get("baseline")), **scalars(summary)}]
            if summary
            else [],
        ),
        rows("hrvReadings", key, each(data.get("hrvReadings"))),
    ]


def daily_sleep_data(day: str, data: Any) -> list[Rows]:
    data = data or {}
    sleep = data.get("dailySleepDTO") or {}
    key = {"calendarDate": day}
    return [
        rows("dailySleepData", key, one(data)),
        rows("dailySleepDTO", key, one(sleep)),
        rows("sleepScores", key, named(sleep.get("sleepScores"))),
        rows("sleepNeed", key, one(sleep.get("sleepNeed"))),
        rows("dailyNapDTOS", key, each(sleep.get("dailyNapDTOS"))),
        rows(
            "wellnessSpO2SleepSummaryDTO",
            key,
            one(data.get("wellnessSpO2SleepSummaryDTO")),
        ),
        *(rows(name, key, each(data.get(name))) for name in SLEEP_ARRAYS),
    ]


def daily_summary_chart(day: str, data: Any) -> list[Rows]:
    return [rows("dailySummaryChart", {"calendarDate": day}, each(data))]


def weight(day: str, data: Any) -> list[Rows]:
    summaries = listed((data or {}).get("dailyWeightSummaries"))
    return [
        rows("dailyWeightSummaries", {"summaryDate": day}, each(summaries)),
        rows(
            "allWeightMetrics",
            {"calendarDate": day},
            [
                scalars(metric)
                for summary in summaries
                for metric in listed(summary.get("allWeightMetrics"))
            ],
        ),
    ]


def maxmet(day: str, data: Any) -> list[Rows]:
    return [
        rows(
            "maxmet",
            {"calendarDate": day},
            [
                {**scalars(row[name]), "key": name}
                for row in listed(data)
                for name in ("generic", "cycling")
                if row.get(name)
            ],
        )
    ]


def racepredictions(day: str, data: Any) -> list[Rows]:
    return [rows("racepredictions", {"calendarDate": day}, each(data))]


def fitnessage(day: str, data: Any) -> list[Rows]:
    data = data or {}
    key = {"calendarDate": day}
    return [
        rows("fitnessage", key, one(data)),
        rows("components", key, named(data.get("components"))),
    ]


def mylastused(_: str, data: Any) -> list[Rows]:
    return [rows("mylastused", {}, one(data))]


def activities(_: str, data: Any) -> list[Rows]:
    result = []
    for activity in listed(data):
        key = {"activityId": activity["activityId"]}
        kind = activity.get("activityType") or {}
        event = activity.get("eventType") or {}
        result += [
            rows(
                "activities",
                key,
                [
                    {
                        **scalars(activity, ACCOUNT_FIELDS),
                        "activityType": kind.get("typeId"),
                        "eventType": event.get("typeId"),
                    }
                ],
            ),
            rows("activityType", {"typeId": kind.get("typeId")}, one(kind)),
            rows("eventType", {"typeId": event.get("typeId")}, one(event)),
            rows("splitSummaries", key, each(activity.get("splitSummaries"))),
        ]
    return result


def hr_time_in_zones(activity_id: str, data: Any) -> list[Rows]:
    return [rows("hrTimeInZones", {"activityId": int(activity_id)}, each(data))]


def open_meteo(activity_id: str, data: Any) -> list[Rows]:
    hourly = (data or {}).get("hourly") or {}
    return [
        rows(
            "activity_weather",
            {"activity_id": int(activity_id)},
            [
                {name: values[index] for name, values in hourly.items()}
                for index in range(len(hourly.get("time") or []))
            ],
        )
    ]


DOCUMENTS: dict[str, Callable[[str, Any], list[Rows]]] = {
    "get_stats": usersummary,
    "get_heart_rates": daily_heart_rate,
    "get_stress_data": daily_stress,
    "get_respiration_data": respiration,
    "get_hrv_data": hrv,
    "get_sleep_data": daily_sleep_data,
    "get_steps_data": daily_summary_chart,
    "get_weigh_ins": weight,
    "get_max_metrics": maxmet,
    "get_race_predictions": racepredictions,
    "get_fitnessage_data": fitnessage,
    "get_device_last_used": mylastused,
    "get_activities_by_date": activities,
    "get_activity_hr_in_timezones": hr_time_in_zones,
    "open-meteo": open_meteo,
}


def project(endpoint: str, key: str, payload: Fields) -> list[Rows]:
    function = DOCUMENTS.get(endpoint)
    if function is None:
        return []
    args = payload.get("args") or [key]
    subject = (payload.get("kwargs") or {}).get("startdate") or args[0]
    return function(str(subject), payload.get("data"))


def fit_files(content: bytes) -> list[bytes]:
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return [content]
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return [
            archive.read(name)
            for name in archive.namelist()
            if name.lower().endswith(".fit")
        ]


def fit(activity_id: str, content: bytes) -> list[Rows]:
    messages: dict[str, dict[Any, Fields]] = {name: {} for name in FIT_MESSAGES}
    for file in fit_files(content):
        with fitdecode.FitReader(
            io.BytesIO(file), error_handling=fitdecode.ErrorHandling.RAISE
        ) as reader:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name not in messages:
                    continue
                fields = {
                    field.name: field.value
                    for field in frame.fields
                    if not field.name.startswith("unknown")
                    and not isinstance(field.value, tuple | list)
                }
                identity = fields.get(FIT_MESSAGES[frame.name])
                if identity is not None:
                    messages[frame.name].setdefault(identity, {}).update(fields)
    key = {"activityId": int(activity_id)}
    return [
        rows(f"fit_{name}", key, values.values()) for name, values in messages.items()
    ]
