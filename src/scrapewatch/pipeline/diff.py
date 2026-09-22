"""Compare two snapshots of the same source, field by field.

Everything is keyed by ``Record.key`` — ``(source, external_id)`` — so the diff never
depends on list order: two lists with the same records in different order produce no
changes. It compares ``fields`` only: ``fetched_at`` is bookkeeping about *when* we
looked, not part of *what* we saw, and neither it nor ``url`` nor ``kind`` can change
without the identifier changing too.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from scrapewatch.models import Record


@dataclasses.dataclass(frozen=True)
class FieldChange:
    """One field, on one record, that differed between two snapshots."""

    external_id: str
    field: str
    before: Any
    after: Any


@dataclasses.dataclass
class ChangeSet:
    """What a diff found for a single source: what's new, what's gone, what moved."""

    added: list[Record] = dataclasses.field(default_factory=list)
    removed: list[Record] = dataclasses.field(default_factory=list)
    changed: list[FieldChange] = dataclasses.field(default_factory=list)


def diff(before: list[Record], after: list[Record]) -> ChangeSet:
    """Diff two snapshots of the same source, keyed by ``Record.key``."""
    before_by_key = {record.key: record for record in before}
    after_by_key = {record.key: record for record in after}

    added = [record for key, record in after_by_key.items() if key not in before_by_key]
    removed = [record for key, record in before_by_key.items() if key not in after_by_key]

    changed: list[FieldChange] = []
    for key, before_record in before_by_key.items():
        after_record = after_by_key.get(key)
        if after_record is None:
            continue
        field_names = sorted(set(before_record.fields) | set(after_record.fields))
        for field_name in field_names:
            before_value = before_record.fields.get(field_name)
            after_value = after_record.fields.get(field_name)
            if before_value != after_value:
                changed.append(
                    FieldChange(external_id=key[1], field=field_name, before=before_value, after=after_value)
                )

    return ChangeSet(added=added, removed=removed, changed=changed)
