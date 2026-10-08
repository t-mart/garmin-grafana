import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

import fitdecode

from .storage import Fields, Sample, timestamp

RECORD_FIELDS = {
    "Altitude": ("enhanced_altitude", "altitude"),
    "Distance": ("distance",),
    "HeartRate": ("heart_rate",),
    "Speed": ("enhanced_speed", "speed"),
    "Cadence": ("cadence",),
    "Fractional_Cadence": ("fractional_cadence",),
    "Temperature": ("temperature",),
    "Power": ("power",),
    "Vertical_Oscillation": ("vertical_oscillation",),
    "Stance_Time": ("stance_time",),
    "Vertical_Ratio": ("vertical_ratio",),
    "Step_Length": ("step_length",),
}
LAP_FIELDS = {
    "Distance": ("total_distance",),
    "Elapsed_Time": ("total_elapsed_time",),
    "Ascent": ("total_ascent",),
    "Descent": ("total_descent",),
    "Avg_HR": ("avg_heart_rate",),
    "Max_HR": ("max_heart_rate",),
    "Avg_Speed": ("enhanced_avg_speed", "avg_speed"),
    "Max_Speed": ("enhanced_max_speed", "max_speed"),
}


def aliases(record: Fields, mapping: dict[str, tuple[str, ...]]) -> Fields:
    return {
        name: next((record[key] for key in keys if record.get(key) is not None), None)
        for name, keys in mapping.items()
    }


def fit_samples(content: bytes, activity_id: str, start: datetime) -> list[Sample]:
    if zipfile.is_zipfile(io.BytesIO(content)):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            fits = [
                archive.read(name)
                for name in archive.namelist()
                if name.lower().endswith(".fit")
            ]
        if not fits:
            raise ValueError("The activity archive contains no FIT files.")
        return [
            sample for fit in fits for sample in fit_samples(fit, activity_id, start)
        ]
    samples: list[Sample] = []
    with fitdecode.FitReader(
        io.BytesIO(content), error_handling=fitdecode.ErrorHandling.RAISE
    ) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            record = {field.name: field.value for field in frame.fields}
            moment = (
                record.get("timestamp")
                if frame.name == "record"
                else record.get("start_time") or record.get("timestamp")
            )
            if moment is None:
                continue
            at = timestamp(moment)
            if frame.name == "record":
                fields = {
                    **record,
                    **aliases(record, RECORD_FIELDS),
                    "DurationSeconds": (at - start).total_seconds(),
                }
                fields["Fractional_Cadence"] = fields["Fractional_Cadence"] or 0
                for field, key in (
                    ("Latitude", "position_lat"),
                    ("Longitude", "position_long"),
                ):
                    fields[field] = (
                        record[key] * 180 / 2**31
                        if record.get(key) is not None
                        else None
                    )
                samples.append(Sample("ActivityGPS", at, fields, activity_id))
            elif frame.name in {"lap", "session", "length"}:
                fields = {
                    **record,
                    **aliases(record, LAP_FIELDS),
                    "Index": int(record.get("message_index") or 0) + 1,
                }
                samples.append(
                    Sample(f"Activity{frame.name.title()}", at, fields, activity_id)
                )
    return samples


def summary_sample(activity: Fields, zones: list[Fields]) -> Sample:
    fields = {
        **activity,
        "activityType": (activity.get("activityType") or {}).get("typeKey", "other"),
        "elapsedDuration": activity.get("elapsedDuration") or activity.get("duration"),
    }
    for zone in zones:
        number = zone.get("zoneNumber")
        fields[f"hrZoneLowBoundary_{number}"] = zone.get("zoneLowBoundary")
        fields[f"hrTimeInZone_{number}"] = zone.get("secsInZone")
    return Sample(
        "ActivitySummary",
        timestamp(activity["startTimeGMT"]),
        fields,
        str(activity["activityId"]),
    )


def tcx_samples(content: bytes, activity_id: str, start: datetime) -> list[Sample]:
    root = ET.fromstring(content)
    samples = []
    for trackpoint in root.findall(".//{*}Trackpoint"):
        moment = trackpoint.findtext("{*}Time")
        if not moment:
            continue
        fields: Fields = {
            "DurationSeconds": (timestamp(moment) - start).total_seconds()
        }
        for name, path in {
            "Latitude": "{*}Position/{*}LatitudeDegrees",
            "Longitude": "{*}Position/{*}LongitudeDegrees",
            "Altitude": "{*}AltitudeMeters",
            "Distance": "{*}DistanceMeters",
            "HeartRate": "{*}HeartRateBpm/{*}Value",
            "Speed": ".//{*}Speed",
            "Cadence": "{*}Cadence",
            "Power": ".//{*}Watts",
        }.items():
            value = trackpoint.findtext(path)
            fields[name] = float(value) if value is not None else None
        fields["Fractional_Cadence"] = 0
        samples.append(Sample("ActivityGPS", timestamp(moment), fields, activity_id))
    for index, lap in enumerate(root.findall(".//{*}Lap"), 1):
        moment = lap.get("StartTime")
        if not moment:
            continue
        fields = {"Index": index}
        for name, path in {
            "Distance": "{*}DistanceMeters",
            "Elapsed_Time": "{*}TotalTimeSeconds",
            "Avg_HR": "{*}AverageHeartRateBpm/{*}Value",
            "Max_HR": "{*}MaximumHeartRateBpm/{*}Value",
        }.items():
            value = lap.findtext(path)
            fields[name] = float(value) if value is not None else None
        samples.append(Sample("ActivityLap", timestamp(moment), fields, activity_id))
    return samples
