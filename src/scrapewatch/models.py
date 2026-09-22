"""Record models: what a source captured, and what survived validation.

``RawRecord`` is what a fetcher produces: text as the page wrote it, keyed by the
source's own identifier for the thing it describes. ``Record`` is what normalisation
produces from a ``RawRecord``: the same identity, plus a ``kind`` that says what shape
the fields carry, and fields whose values a database can compare and store directly —
a ``Decimal`` for a price, an ``int`` for a count, a ``bool`` for a flag. Pydantic
already renders a ``Decimal`` inside an ``Any``-typed field as an exact-text JSON
string, so no custom encoder is needed to keep ``"51.77"`` from becoming a float.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class RawRecord(BaseModel):
    """What a fetcher saw, before anything has been trusted or parsed."""

    source: str
    external_id: str
    fetched_at: datetime
    fields: dict[str, Any]
    url: str


class Record(BaseModel):
    """What ``normalize`` produces: validated fields, typed for storage and diffing."""

    source: str
    external_id: str
    kind: Literal["book", "quote", "product"]
    fetched_at: datetime
    fields: dict[str, Any]
    url: str

    @property
    def key(self) -> tuple[str, str]:
        """The identity a diff keys on: the source and the source's own identifier."""
        return (self.source, self.external_id)
