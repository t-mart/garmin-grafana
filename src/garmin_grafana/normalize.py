from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .derive import clock_position
from .storage import Fields, Sample, timestamp


def daily_samples(kind: str, payload: Any, day: date, zone: ZoneInfo) -> list[Sample]:
    midnight = datetime.combine(day, time(), zone)
    data = payload or {}
    if kind == "daily_avg":
        return [Sample("DailyStats", midnight, data)] if data else []
    if kind == "sleep":
        return sleep_samples(data, zone)
    arrays = {
        "heartrate": ("heartRateValues", "HeartRateIntraday", "HeartRate", 1),
        "stress": ("stressValuesArray", "StressIntraday", "stressLevel", 1),
        "breathing": (
            "respirationValuesArray",
            "BreathingRateIntraday",
            "BreathingRate",
            1,
        ),
    }
    if kind in arrays:
        key, measurement, field, index = arrays[kind]
        samples = array_samples(data.get(key) or [], measurement, field, index)
        if kind == "stress":
            samples += array_samples(
                data.get("bodyBatteryValuesArray") or [],
                "BodyBatteryIntraday",
                "BodyBatteryLevel",
                2,
            )
        return samples
    if kind == "steps":
        return [
            Sample(
                "StepsIntraday",
                timestamp(row["startGMT"]),
                {**row, "StepsCount": row.get("steps")},
            )
            for row in data
        ]
    if kind == "hrv":
        samples = [
            Sample("HRV_Intraday", timestamp(row["readingTimeGMT"]), row)
            for row in data.get("hrvReadings") or []
        ]
        if data.get("hrvSummary"):
            samples.append(Sample("HRVSummary", midnight, data["hrvSummary"]))
        return samples
    if kind == "body_composition":
        return [
            Sample(
                "BodyComposition",
                timestamp(row["timestampGMT"]) if row.get("timestampGMT") else midnight,
                row,
                str(row.get("samplePk") or row.get("sourceType") or ""),
            )
            for group in data.get("dailyWeightSummaries") or []
            for row in group.get("allWeightMetrics") or []
        ]
    if kind == "vo2":
        rows = data if isinstance(data, list) else [data]
        return [
            Sample(
                "VO2_Max",
                midnight,
                {
                    **row,
                    "VO2_max_value": (row.get("generic") or {}).get(
                        "vo2MaxPreciseValue"
                    ),
                    "VO2_max_value_cycling": (row.get("cycling") or {}).get(
                        "vo2MaxPreciseValue"
                    ),
                },
            )
            for row in rows
            if row
        ]
    if kind == "race_prediction":
        rows = data if isinstance(data, list) else [data]
        return [Sample("RacePredictions", midnight, row) for row in rows if row]
    return [Sample(kind, midnight, {"data": data})] if data else []


def array_samples(
    rows: Iterable[list[Any]], measurement: str, field: str, index: int
) -> list[Sample]:
    return [
        Sample(measurement, timestamp(row[0]), {field: row[index]})
        for row in rows
        if row[index] is not None
    ]


def sleep_samples(data: Fields, zone: ZoneInfo) -> list[Sample]:
    summary = data.get("dailySleepDTO") or {}
    samples: list[Sample] = []
    end_value = summary.get("sleepEndTimestampGMT")
    if end_value:
        fields = {
            **summary,
            **{
                key: value
                for key, value in data.items()
                if not isinstance(value, dict | list)
            },
        }
        fields["sleepScore"] = (
            (summary.get("sleepScores") or {}).get("overall") or {}
        ).get("value")
        samples.append(Sample("SleepSummary", timestamp(end_value), fields))
    for key, field, time_key, value_key in (
        ("sleepHeartRate", "heartRate", "startGMT", "value"),
        ("sleepStress", "stressValue", "startGMT", "value"),
        ("sleepBodyBattery", "bodyBattery", "startGMT", "value"),
        ("hrvData", "hrvData", "startGMT", "value"),
        ("sleepRestlessMoments", "sleepRestlessValue", "startGMT", "value"),
        (
            "wellnessEpochSPO2DataDTOList",
            "spo2Reading",
            "epochTimestamp",
            "spo2Reading",
        ),
        (
            "wellnessEpochRespirationDataDTOList",
            "respirationValue",
            "startTimeGMT",
            "respirationValue",
        ),
    ):
        samples.extend(
            Sample("SleepIntraday", timestamp(row[time_key]), {field: row[value_key]})
            for row in data.get(key) or []
            if row.get(value_key) is not None
        )
    for key, prefix in (
        ("sleepMovement", "SleepMovementActivity"),
        ("sleepLevels", "SleepStage"),
    ):
        for row in data.get(key) or []:
            start, end = timestamp(row["startGMT"]), timestamp(row["endGMT"])
            samples.append(
                Sample(
                    "SleepIntraday",
                    start,
                    {
                        f"{prefix}Level": row.get("activityLevel"),
                        f"{prefix}Seconds": (end - start).total_seconds(),
                    },
                )
            )
    if not end_value:
        return samples
    end = timestamp(end_value)
    start_value = summary.get("sleepStartTimestampGMT")
    start = (
        timestamp(start_value)
        if start_value
        else end
        - timedelta(
            seconds=(summary.get("sleepTimeSeconds") or 0)
            + (summary.get("awakeSleepSeconds") or 0)
        )
    )
    stages = sorted(
        (timestamp(row["startGMT"]), timestamp(row["endGMT"]), row.get("activityLevel"))
        for row in data.get("sleepLevels") or []
        if row.get("activityLevel") is not None
    )
    night = end.astimezone(zone).date()
    midnight = datetime.combine(night, time(), zone).timestamp() * 1000
    index = 0
    while start < end and index < len(stages):
        while index < len(stages) and start >= stages[index][1]:
            index += 1
        if index < len(stages) and stages[index][0] <= start < stages[index][1]:
            samples.append(
                Sample(
                    "SleepStage",
                    start,
                    {
                        "Day": midnight,
                        "Clock": clock_position(int(start.timestamp()), zone),
                        "Stage": stages[index][2],
                    },
                    night.isoformat(),
                )
            )
        start += timedelta(minutes=5)
    return samples
