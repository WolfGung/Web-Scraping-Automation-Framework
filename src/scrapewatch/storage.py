"""Snapshots that survive a process: every fetch kept, the last two compared, the latest exported.

Every run's snapshot of every source is written to a database, never overwritten, so
`latest_snapshots` can always hand a diff the two it should compare. The same
SQLAlchemy code runs against SQLite (the default, so the repository works after one
clone) or PostgreSQL (the Compose profile) — nothing here is SQLite-specific except
creating the parent directory of a SQLite file, which PostgreSQL has no equivalent of.

Datetimes and decimals are stored as text, not as native column types: SQLite has no
native timezone-aware timestamp, and no exact decimal type either, and a diff that
reports 51.77 becoming 51.770000001 because of a lossy round trip is a lie about the
site. `_tag_fields`/`_untag_fields` mark a `Decimal` with `{"__decimal__": "<text>"}`,
recursively through lists and dicts, so it comes back exactly what went in; `None`
passes through unmarked, so "unknown" never turns into "zero" on the way out. A raw
field that already contains the reserved `__decimal__` key is refused with a
`ValueError` rather than silently misread as a tagged decimal on the way back.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from scrapewatch.models import Record
from scrapewatch.pipeline.diff import ChangeSet

_SQLITE_FILE_PREFIX = "sqlite:///"


class Base(DeclarativeBase):
    """Declarative base for the four tables storage owns: runs, snapshots, records, changes."""


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[str] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    sources_json: Mapped[str] = mapped_column(Text)
    stats_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class SnapshotRow(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    source: Mapped[str] = mapped_column(String, index=True)
    taken_at: Mapped[str] = mapped_column(String)
    record_count: Mapped[int] = mapped_column(Integer)


class RecordRow(Base):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"), index=True)
    source: Mapped[str] = mapped_column(String)
    external_id: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    fetched_at: Mapped[str] = mapped_column(String)
    fields_json: Mapped[str] = mapped_column(Text)


class ChangeRow(Base):
    """One row per thing a diff found: a changed field, or a whole record added/removed.

    `change_type` says which of the three this row is, so `field`/`before_json`/
    `after_json` mean one thing, not two: they carry the field name and its two values
    only for `"changed"`. An `"added"`/`"removed"` row stores no payload — the whole
    record is already in the snapshot on that side of the diff, so duplicating it here
    would just be another place for it to go stale.
    """

    __tablename__ = "changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    source: Mapped[str] = mapped_column(String)
    external_id: Mapped[str] = mapped_column(String)
    change_type: Mapped[str] = mapped_column(String)  # "changed" | "added" | "removed"
    field: Mapped[str | None] = mapped_column(String, nullable=True)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)


@dataclass(frozen=True)
class Run:
    """An opaque handle to a started run: just enough to pass back into other calls."""

    id: int


@dataclass(frozen=True)
class Snapshot:
    """An opaque handle to a saved snapshot."""

    id: int
    source: str
    taken_at: datetime
    record_count: int


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


#: The key `_encode_value` tags a `Decimal` with. A raw field carrying this key itself
#: would be indistinguishable from a tagged decimal on the way back, so encoding it
#: is refused outright rather than risking a silent misread.
_DECIMAL_TAG = "__decimal__"


def _encode_value(value: Any, field_name: str) -> Any:
    """Tag every `Decimal` reachable from `value`, however deep in a list or dict.

    `field_name` is only for the error message: a raw field that already uses the
    reserved `__decimal__` key would come back as a `Decimal` it never was, so that
    is refused loudly here instead of being stored wrong.
    """
    if isinstance(value, Decimal):
        return {_DECIMAL_TAG: str(value)}
    if isinstance(value, dict):
        if _DECIMAL_TAG in value:
            raise ValueError(f"{field_name}: value uses the reserved key {_DECIMAL_TAG!r}, cannot be stored")
        return {key: _encode_value(item, field_name) for key, item in value.items()}
    if isinstance(value, list):
        return [_encode_value(item, field_name) for item in value]
    return value


def _decode_value(value: Any) -> Any:
    """Undo `_encode_value`. Only a dict with *exactly* the tag key is a decimal."""
    if isinstance(value, dict):
        if set(value) == {_DECIMAL_TAG}:
            return Decimal(value[_DECIMAL_TAG])
        return {key: _decode_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    return value


def _tag_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: _encode_value(value, key) for key, value in fields.items()}


def _untag_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: _decode_value(value) for key, value in fields.items()}


def _dump_value(value: Any, field_name: str) -> str | None:
    """Serialise a single before/after value for the `changes` table. `None` stays `None`."""
    if value is None:
        return None
    return json.dumps(_encode_value(value, field_name))


def _row_to_record(row: RecordRow) -> Record:
    return Record(
        source=row.source,
        external_id=row.external_id,
        kind=row.kind,
        fetched_at=datetime.fromisoformat(row.fetched_at),
        fields=_untag_fields(json.loads(row.fields_json)),
        url=row.url,
    )


class Storage:
    """Snapshots on SQLite (or PostgreSQL) by default: `open()` before anything else."""

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        self._engine = None

    def open(self) -> None:
        """Create the schema, and — for a SQLite file — its parent directory."""
        self._ensure_sqlite_directory()
        self._engine = create_engine(self._db_url)
        Base.metadata.create_all(self._engine)

    def _ensure_sqlite_directory(self) -> None:
        if not self._db_url.startswith(_SQLITE_FILE_PREFIX):
            return
        path_part = self._db_url[len(_SQLITE_FILE_PREFIX) :]
        if not path_part or path_part == ":memory:":
            return
        Path(path_part).parent.mkdir(parents=True, exist_ok=True)

    def _session(self) -> Session:
        if self._engine is None:
            raise RuntimeError("Storage.open() must be called before use")
        return Session(self._engine)

    def start_run(self, sources: list[str]) -> Run:
        with self._session() as session:
            row = RunRow(started_at=_now_iso(), finished_at=None, sources_json=json.dumps(sources), stats_json=None)
            session.add(row)
            session.commit()
            return Run(id=row.id)

    def save_snapshot(self, run: Run, source: str, records: list[Record]) -> Snapshot:
        with self._session() as session:
            taken_at = _now_iso()
            snapshot_row = SnapshotRow(run_id=run.id, source=source, taken_at=taken_at, record_count=len(records))
            session.add(snapshot_row)
            session.flush()  # assigns snapshot_row.id, needed by the records below

            for record in records:
                session.add(
                    RecordRow(
                        snapshot_id=snapshot_row.id,
                        source=record.source,
                        external_id=record.external_id,
                        kind=record.kind,
                        url=record.url,
                        fetched_at=record.fetched_at.isoformat(),
                        fields_json=json.dumps(_tag_fields(record.fields)),
                    )
                )
            session.commit()
            return Snapshot(id=snapshot_row.id, source=source, taken_at=datetime.fromisoformat(taken_at),
                             record_count=len(records))

    def latest_snapshots(self, source: str, n: int = 2) -> list[list[Record]]:
        """The `n` most recent snapshots of `source`, newest first."""
        with self._session() as session:
            snapshot_rows = session.scalars(
                select(SnapshotRow).where(SnapshotRow.source == source).order_by(SnapshotRow.id.desc()).limit(n)
            ).all()
            result: list[list[Record]] = []
            for snapshot_row in snapshot_rows:
                record_rows = session.scalars(
                    select(RecordRow).where(RecordRow.snapshot_id == snapshot_row.id)
                ).all()
                result.append([_row_to_record(row) for row in record_rows])
            return result

    def save_changes(self, run: Run, source: str, changeset: ChangeSet) -> None:
        """Persist one `changes` row per field change, plus one per whole record added/removed.

        Only a `"changed"` row carries `field`/`before_json`/`after_json`: an
        `"added"`/`"removed"` row's record is already in the snapshot on that side of
        the diff, so it stores no payload of its own to go stale.
        """
        with self._session() as session:
            for change in changeset.changed:
                session.add(
                    ChangeRow(
                        run_id=run.id,
                        source=source,
                        external_id=change.external_id,
                        change_type="changed",
                        field=change.field,
                        before_json=_dump_value(change.before, change.field),
                        after_json=_dump_value(change.after, change.field),
                    )
                )
            for record in changeset.added:
                session.add(
                    ChangeRow(
                        run_id=run.id,
                        source=source,
                        external_id=record.external_id,
                        change_type="added",
                        field=None,
                        before_json=None,
                        after_json=None,
                    )
                )
            for record in changeset.removed:
                session.add(
                    ChangeRow(
                        run_id=run.id,
                        source=source,
                        external_id=record.external_id,
                        change_type="removed",
                        field=None,
                        before_json=None,
                        after_json=None,
                    )
                )
            session.commit()

    def finish_run(self, run: Run, stats: dict) -> None:
        with self._session() as session:
            row = session.get(RunRow, run.id)
            if row is None:
                raise KeyError(f"no such run: {run.id}")
            row.finished_at = _now_iso()
            row.stats_json = json.dumps(stats, default=str)
            session.commit()

    def export(self, source: str, fmt: Literal["csv", "json"], path: str | Path) -> None:
        """Write the latest snapshot of `source`, sorted by `external_id`, so repeat exports match byte for byte."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        snapshots = self.latest_snapshots(source, n=1)
        records = sorted(snapshots[0], key=lambda record: record.external_id) if snapshots else []
        field_keys = sorted({key for record in records for key in record.fields})
        header = [*field_keys, "external_id", "url"]

        rows = []
        for record in records:
            row: dict[str, Any] = {key: record.fields.get(key) for key in field_keys}
            row["external_id"] = record.external_id
            row["url"] = record.url
            rows.append(row)

        if fmt == "csv":
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=header)
                writer.writeheader()
                writer.writerows(rows)
        elif fmt == "json":
            path.write_text(json.dumps(rows, default=str, indent=2), encoding="utf-8")
        else:
            raise ValueError(f"fmt: unknown export format {fmt!r}")
