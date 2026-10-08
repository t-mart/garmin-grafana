import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import cached_property
from importlib.resources import files
from typing import Any, LiteralString, cast

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

type Fields = dict[str, Any]

TIME_TYPES = {"date", "timestamp with time zone", "timestamp without time zone"}


@dataclass(frozen=True)
class Rows:
    table: str
    key: Fields
    rows: list[Fields]


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


def convert(kind: str, value: Any) -> Any:
    if value is None or kind not in TIME_TYPES or isinstance(value, date):
        return value
    if isinstance(value, int | float):
        moment = datetime.fromtimestamp(value / 1000, UTC)
    else:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
    if kind == "date":
        return moment.date()
    if kind == "timestamp without time zone":
        return moment.replace(tzinfo=None)
    return moment


class Store:
    def __init__(self, connection: psycopg.Connection[dict[str, Any]]) -> None:
        self.connection = connection

    @cached_property
    def columns(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for row in self.connection.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = 'garmin'"
        ):
            result.setdefault(row["table_name"], {})[row["column_name"]] = row[
                "data_type"
            ]
        return result

    def initialize(self) -> None:
        schema = files("garmin_grafana").joinpath("schema.sql").read_text()
        with self.connection.transaction():
            self.connection.execute(cast(LiteralString, schema))
        self.__dict__.pop("columns", None)

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

    def replace(self, projection: Iterable[Rows]) -> None:
        with self.connection.transaction():
            for rows in projection:
                columns = self.columns[rows.table]
                table = sql.Identifier("garmin", rows.table)
                condition = sql.SQL(" AND ").join(
                    sql.SQL("{} = %s").format(sql.Identifier(name)) for name in rows.key
                )
                self.connection.execute(
                    sql.SQL("DELETE FROM {} WHERE {}").format(
                        table, condition if rows.key else sql.SQL("true")
                    ),
                    [convert(columns[name], value) for name, value in rows.key.items()],
                )
                records = [
                    {
                        name: convert(columns[name], value)
                        for name, value in {**row, **rows.key}.items()
                        if name in columns
                    }
                    for row in rows.rows
                ]
                names = sorted({name for record in records for name in record})
                if not names:
                    continue
                with self.connection.cursor() as cursor:
                    cursor.executemany(
                        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                            table,
                            sql.SQL(", ").join(map(sql.Identifier, names)),
                            sql.SQL(", ").join([sql.Placeholder()] * len(names)),
                        ),
                        [[record.get(name) for name in names] for record in records],
                    )

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
