"""The same facts twice: a JSON payload for machines, an HTML page for people.

Both are built from the same ``ChangeSet``s, one per source, so nothing in the report
can say something the diff didn't find — the report only ever restates it. A source
with zero changes is stated, not omitted: silence would read as "not checked", not as
"checked, nothing moved".
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from scrapewatch.models import Record
from scrapewatch.pipeline.diff import ChangeSet, FieldChange

_STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }
h1 { font-size: 1.4rem; }
section { margin-bottom: 1.5rem; }
table { border-collapse: collapse; margin-top: 0.5rem; }
th, td { border: 1px solid #ccc; padding: 4px 10px; text-align: left; }
th { background: #f0f0f0; }
.counts { color: #444; }
"""


@dataclass
class ChangeReport:
    """A change report for a whole run: one ``ChangeSet`` per source, with a timestamp."""

    generated_at: datetime
    changesets: dict[str, ChangeSet] = field(default_factory=dict)

    @classmethod
    def from_changesets(cls, changesets: dict[str, ChangeSet], generated_at: datetime) -> ChangeReport:
        return cls(generated_at=generated_at, changesets=dict(changesets))

    def _source_summary(self, changeset: ChangeSet) -> dict[str, Any]:
        return {
            "added": len(changeset.added),
            "removed": len(changeset.removed),
            "changed": len(changeset.changed),
            "changes": [
                {"external_id": c.external_id, "field": c.field, "before": c.before, "after": c.after}
                for c in changeset.changed
            ],
        }

    def to_json(self) -> str:
        """The machine-readable form: counts and changes, ``Decimal`` values as text."""
        payload = {
            "generated_at": self.generated_at.isoformat(),
            "sources": {source: self._source_summary(cs) for source, cs in self.changesets.items()},
        }
        return json.dumps(payload, default=str)

    def to_html(self) -> str:
        """A standalone HTML document: inline CSS, no external requests, everything escaped."""
        sections = "".join(self._render_source(source, cs) for source, cs in self.changesets.items())
        return (
            "<!doctype html>\n"
            '<html lang="en"><head><meta charset="utf-8">'
            "<title>ScrapeWatch change report</title>"
            f"<style>{_STYLE}</style></head><body>"
            f"<h1>Change report — {html.escape(self.generated_at.isoformat())}</h1>"
            f"{sections}"
            "</body></html>"
        )

    def _render_source(self, source: str, changeset: ChangeSet) -> str:
        added, removed, changed = len(changeset.added), len(changeset.removed), len(changeset.changed)
        heading = f"<h2>{html.escape(source)}</h2>"
        if added == 0 and removed == 0 and changed == 0:
            return f"<section>{heading}<p>No changes.</p></section>"

        parts = [heading, f'<p class="counts">{added} added, {removed} removed, {changed} changed.</p>']
        if changeset.changed:
            rows = "".join(self._render_change_row(c) for c in changeset.changed)
            parts.append(f"<table><tr><th>ID</th><th>Field</th><th>Before</th><th>After</th></tr>{rows}</table>")
        if changeset.added:
            items = "".join(f"<li>{self._render_record(r)}</li>" for r in changeset.added)
            parts.append(f"<h3>Added</h3><ul>{items}</ul>")
        if changeset.removed:
            items = "".join(f"<li>{self._render_record(r)}</li>" for r in changeset.removed)
            parts.append(f"<h3>Removed</h3><ul>{items}</ul>")
        return f"<section>{''.join(parts)}</section>"

    @staticmethod
    def _render_record(record: Record) -> str:
        field_text = ", ".join(
            f"{html.escape(str(k))}={html.escape(ChangeReport._display(v))}" for k, v in record.fields.items()
        )
        return f"{html.escape(record.external_id)}: {field_text}"

    @staticmethod
    def _render_change_row(change: FieldChange) -> str:
        eid = html.escape(str(change.external_id))
        field_name = html.escape(str(change.field))
        before = html.escape(ChangeReport._display(change.before))
        after = html.escape(ChangeReport._display(change.after))
        return f"<tr><td>{eid}</td><td>{field_name}</td><td>{before}</td><td>{after}</td></tr>"

    @staticmethod
    def _display(value: Any) -> str:
        """Render a field value for HTML: a missing/unknown value is not the string "None"."""
        return "(absent)" if value is None else str(value)
