import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.resources import files
from typing import Any, LiteralString, cast

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

type Fields = dict[str, Any]


def timestamp(value: str | float | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, UTC)
    return timestamp(datetime.fromisoformat(value))


def json_default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return list(value)
    raise TypeError(f"Unsupported JSON type: {type(value).__name__}")


def encode(value: Any) -> str:
    return json.dumps(value, default=json_default, sort_keys=True, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


@dataclass(frozen=True)
class Sample:
    measurement: str
    time: datetime
    fields: Fields
    entity: str = ""


class Store:
    def __init__(self, connection: psycopg.Connection[dict[str, Any]]) -> None:
        self.connection = connection

    def initialize(self) -> None:
        schema = files("garmin_grafana").joinpath("schema.sql").read_text()
        with self.connection.transaction():
            self.connection.execute(cast(LiteralString, schema))

    def archive(self, endpoint: str, key: str, payload: Any) -> None:
        self.connection.execute(
            "INSERT INTO garmin.responses (endpoint, key, digest, payload) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (endpoint, key, digest) "
            "DO UPDATE SET last_seen = now()",
            (endpoint, key, digest(payload), Jsonb(payload, dumps=encode)),
        )

    def archive_file(self, key: str, content: bytes) -> None:
        self.connection.execute(
            "INSERT INTO garmin.files (key, digest, content) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (key, hashlib.sha256(content).hexdigest(), content),
        )

    def latest_file(self, key: str) -> bytes | None:
        row = self.connection.execute(
            "SELECT content FROM garmin.files WHERE key = %s "
            "ORDER BY captured_at DESC LIMIT 1",
            (key,),
        ).fetchone()
        return bytes(row["content"]) if row else None

    def replace(self, source: str, samples: Iterable[Sample]) -> None:
        merged: dict[tuple[str, datetime, str], Fields] = {}
        for sample in samples:
            key = (sample.measurement, sample.time, sample.entity)
            merged.setdefault(key, {}).update(sample.fields)
        with self.connection.transaction():
            self.connection.execute(
                "DELETE FROM garmin.samples WHERE source = %s", (source,)
            )
            with self.connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO garmin.samples (source, measurement, time, entity, fields) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    [
                        (source, *key, Jsonb(value, dumps=encode))
                        for key, value in merged.items()
                    ],
                )

    def samples(self, measurement: str, entity: str | None = None) -> list[Sample]:
        rows = self.connection.execute(
            "SELECT time, entity, fields FROM garmin.samples WHERE measurement = %s "
            "AND (%s::text IS NULL OR entity = %s) ORDER BY time, entity",
            (measurement, entity, entity),
        ).fetchall()
        return [
            Sample(measurement, row["time"], row["fields"], row["entity"])
            for row in rows
        ]

    def state(self, key: str) -> Any:
        row = self.connection.execute(
            "SELECT value FROM garmin.state WHERE key = %s", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: Any) -> None:
        self.connection.execute(
            "INSERT INTO garmin.state (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, Jsonb(value)),
        )


def connect(dsn: str = "") -> psycopg.Connection[dict[str, Any]]:
    return psycopg.Connection[dict[str, Any]].connect(
        dsn, autocommit=True, row_factory=dict_row, connect_timeout=10
    )
