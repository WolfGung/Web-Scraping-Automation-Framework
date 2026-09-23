"""The night's data as one Excel workbook: a sheet per source, then what changed.

The CSV and JSON exports are for programs. The workbook is for a person who opens
it in a spreadsheet and wants to read, filter and sort the collection without
setting anything up, so it is the same data, formatted, rather than a new view of
it: each source's sheet carries the export's columns, in the export's order, with
the export's values (`scrapewatch.storage.export_layout` lays out both), and one
more column saying when each record was collected. Numbers are numbers: a price
has two decimals and the currency symbol it was scraped with, a count is an
integer, a time is a date and a flag is TRUE or FALSE.

What moved since the previous snapshot is marked where it happened. A value that
changed gets a light yellow fill in its cell, and a record that is new tonight a
light green fill across its row. The `Changes` sheet lists every change, one row
per changed field or per added or removed record, and says once, at its top, what
the two fills mean, so the data sheets stay plain tables that filter and sort
cleanly.

This module takes plain data and knows nothing about sources, storage or runs:
`scrapewatch.cli` gathers the night's tables and changes and hands them over.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from scrapewatch.pipeline.normalize import CURRENCY_SYMBOLS

#: The column every data sheet ends with: when the record was collected, in UTC,
#: as a date a spreadsheet can sort and filter rather than as text.
COLLECTED_AT = "collected at (UTC)"

CHANGES_TITLE = "Changes"
CHANGES_HEADER = ("source", "record key", "change", "field", "before", "after")

#: The widest a column may grow, in characters. A long title or address stops
#: here instead of turning the sheet into a wall; prose longer than that wraps.
MAX_WIDTH = 60

MONEY_FORMAT = "#,##0.00"
INTEGER_FORMAT = "0"
DATETIME_FORMAT = "yyyy-mm-dd hh:mm"

#: The two marks the data sheets carry, and the colour the `Changes` sheet gives
#: each kind of change — the same yellow and green, so one legend covers both.
CHANGED_FILL = PatternFill(fill_type="solid", fgColor="FFF2CC")
ADDED_FILL = PatternFill(fill_type="solid", fgColor="E2EFDA")
REMOVED_FILL = PatternFill(fill_type="solid", fgColor="FCE4D6")
KIND_FILLS = {"changed": CHANGED_FILL, "added": ADDED_FILL, "removed": REMOVED_FILL}

LEGEND = (
    "In the data sheets, a light yellow cell holds a value that changed since the "
    "previous snapshot, and a light green row is a record that is new tonight."
)

#: Room for the filter button a header cell carries, and for the cell's margins.
_FILTER_BUTTON = 2
_PADDING = 2
#: The empty column between the `Changes` table and its legend.
_SPACER_WIDTH = 2

_HEADER_FONT = Font(bold=True)
_NOTE_FONT = Font(italic=True, color="595959")
_TOP = Alignment(vertical="top")
_TOP_WRAPPED = Alignment(vertical="top", wrap_text=True)

_SYMBOLS = {code: symbol for symbol, code in CURRENCY_SYMBOLS.items()}


@dataclass(frozen=True)
class Sheet:
    """One source's data, laid out as its exports are, and what moved in it tonight.

    `changed` holds a `(key, column)` pair for every value that changed, and
    `added` the key of every record that is new; `key` names the column that holds
    a row's key.
    """

    title: str
    header: Sequence[str]
    rows: Sequence[Sequence[Any]]
    key: str = "external_id"
    changed: frozenset[tuple[str, str]] = frozenset()
    added: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Change:
    """One row of the `Changes` sheet: a changed field, or a record added or removed.

    An added record's fields go in `after` and a removed one's in `before`, and the
    sheet writes them out in words, so a record that is gone can still be read.
    """

    source: str
    key: str
    change: Literal["changed", "added", "removed"]
    field: str | None = None
    before: Any = None
    after: Any = None


def write_workbook(
    path: str | Path,
    sheets: Sequence[Sheet],
    changes: Sequence[Change],
    *,
    uncompared: Sequence[str] = (),
) -> None:
    """Write `sheets` in the order given, then the `Changes` sheet, to `path`.

    `uncompared` names the sheets whose source had no earlier snapshot. A diff with
    nothing on one side calls every record new, which is not news, so the caller
    marks nothing in those sheets and the `Changes` sheet says why in one line
    under its rows — in A2 on a first night, when there are no rows at all.
    """
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet in sheets:
        _write_data_sheet(workbook.create_sheet(sheet.title), sheet)
    _write_changes_sheet(workbook.create_sheet(CHANGES_TITLE), changes, uncompared)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def _write_data_sheet(sheet: Worksheet, data: Sheet) -> None:
    header = list(data.header)
    _write_table(sheet, header, data.rows, currency_column="currency")
    key_index = header.index(data.key)
    for row_number, values in enumerate(data.rows, start=2):
        key = values[key_index]
        for column, name in enumerate(header, start=1):
            if key in data.added:
                sheet.cell(row=row_number, column=column).fill = ADDED_FILL
            elif (key, name) in data.changed:
                sheet.cell(row=row_number, column=column).fill = CHANGED_FILL


def _write_changes_sheet(sheet: Worksheet, changes: Sequence[Change], uncompared: Sequence[str]) -> None:
    rows = [[c.source, c.key, c.change, c.field, c.before, c.after] for c in changes]
    _write_table(sheet, CHANGES_HEADER, rows)
    kind_column = CHANGES_HEADER.index("change") + 1
    for row_number, change in enumerate(changes, start=2):
        sheet.cell(row=row_number, column=kind_column).fill = KIND_FILLS[change.change]

    # The legend sits in the top row, one empty column to the right of the table:
    # always in view under the frozen header, and outside the filtered range, so
    # sorting the table never moves it. It is left unwrapped to run on as one line.
    legend_column = len(CHANGES_HEADER) + 2
    sheet.column_dimensions[get_column_letter(legend_column - 1)].width = _SPACER_WIDTH
    sheet.column_dimensions[get_column_letter(legend_column)].width = MAX_WIDTH
    legend = sheet.cell(row=1, column=legend_column, value=LEGEND)
    legend.font = _NOTE_FONT

    if uncompared:
        note = sheet.cell(row=2 + len(changes), column=1, value=_first_snapshot_note(uncompared))
        note.font = _NOTE_FONT


def _first_snapshot_note(titles: Sequence[str]) -> str:
    names = titles[0] if len(titles) == 1 else f"{', '.join(titles[:-1])} and {titles[-1]}"
    return (
        f"First snapshot of {names}: there is no earlier one to compare with, "
        f"so nothing there is marked as new or changed."
    )


def _write_table(
    sheet: Worksheet,
    header: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    currency_column: str | None = None,
) -> None:
    """A header in bold, then a row per entry: typed, formatted, sized, frozen and filtered."""
    currency_index = header.index(currency_column) if currency_column in header else None
    longest = [len(name) + _FILTER_BUTTON for name in header]
    for column, name in enumerate(header, start=1):
        sheet.cell(row=1, column=column, value=name).font = _HEADER_FONT
    for row_number, values in enumerate(rows, start=2):
        currency = values[currency_index] if currency_index is not None else None
        for column, value in enumerate(values, start=1):
            shown = _shown(value, currency)
            longest[column - 1] = max(longest[column - 1], max(map(len, shown.splitlines() or [""])))
            _put(sheet.cell(row=row_number, column=column), value, currency, shown)
    for column, width in enumerate(longest, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = min(MAX_WIDTH, width + _PADDING)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(header))}{1 + len(rows)}"


def _put(cell: Cell, value: Any, currency: Any, shown: str) -> None:
    """Write one value as its own type, with the format a reader expects of it."""
    cell.alignment = _TOP
    if value is None:
        return
    if isinstance(value, bool):
        cell.value = value
    elif isinstance(value, Decimal):
        cell.value = value
        cell.number_format = _money_format(currency)
    elif isinstance(value, int):
        cell.value = value
        cell.number_format = INTEGER_FORMAT
    elif isinstance(value, float):
        cell.value = value
    elif isinstance(value, datetime):
        cell.value = _naive_utc(value)
        cell.number_format = DATETIME_FORMAT
    else:
        cell.value = shown
        # Text is text, whatever it starts with: left alone, a scraped title that
        # begins with `=` would be stored as a formula and run in the reader's
        # spreadsheet — the hole the CSV export closes with a leading quote.
        cell.data_type = "s"
        if len(shown) > MAX_WIDTH - _PADDING and any(character.isspace() for character in shown):
            # Prose longer than the column wraps; an address or a slug has no
            # space to break at, and wrapping it would only chop it.
            cell.alignment = _TOP_WRAPPED


def _shown(value: Any, currency: Any = None) -> str:
    """A value as the cell shows it, for sizing the column and for writing text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Decimal):
        return f"{_SYMBOLS.get(currency, '')}{value:,.2f}"
    if isinstance(value, datetime):
        return _naive_utc(value).strftime("%Y-%m-%d %H:%M")
    if isinstance(value, dict):
        return "; ".join(f"{name}: {_shown(item)}" for name, item in value.items())
    if isinstance(value, list | tuple):
        return ", ".join(_shown(item) for item in value)
    # A control character is legal in JSON and illegal in the XML a workbook is
    # made of: dropped here, rather than failing the whole night's export.
    return ILLEGAL_CHARACTERS_RE.sub("", str(value))


def _money_format(currency: Any) -> str:
    symbol = _SYMBOLS.get(currency)
    return f'"{symbol}"{MONEY_FORMAT}' if symbol else MONEY_FORMAT


def _naive_utc(value: datetime) -> datetime:
    """A spreadsheet date has no time zone, so a time is stored as UTC and the header says so."""
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
