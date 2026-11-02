"""The same reports as a workbook, for people who want to sort and filter.

openpyxl rather than a CSV dump: the point of the Excel option is the things a CSV
cannot carry - typed numbers that sort correctly, frozen headers, column widths that
fit, an autofilter, and a native chart that keeps working when the farmer edits a cell.

Provenance is a column, never a footnote. A sheet of readings where "simulated" is
only mentioned in the title is a sheet that will be quoted out of context.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

HEADER_FILL = PatternFill("solid", fgColor="E9F6EC")
TITLE_FONT = Font(bold=True, size=14, color="15803D")
HEADER_FONT = Font(bold=True, size=10, color="17251D")
MUTED_FONT = Font(size=9, color="6C7F71")
THIN = Side(style="thin", color="D9E6DC")
BORDER = Border(bottom=THIN)


def _sheet_title(ws: Worksheet, title: str, subtitle: str = "") -> int:
    ws["A1"] = title
    ws["A1"].font = TITLE_FONT
    row = 2
    if subtitle:
        ws["A2"] = subtitle
        ws["A2"].font = MUTED_FONT
        row = 3
    ws["A%d" % row] = "Generated %s UTC" % datetime.now(timezone.utc).strftime(
        "%d %b %Y, %H:%M")
    ws["A%d" % row].font = MUTED_FONT
    return row + 2


def _write_table(ws: Worksheet, start: int, headers: Sequence[str],
                 rows: Sequence[Sequence[Any]]) -> int:
    for column, name in enumerate(headers, start=1):
        cell = ws.cell(row=start, column=column, value=name)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = BORDER
        cell.alignment = Alignment(vertical="center")

    for r, row in enumerate(rows, start=start + 1):
        for c, value in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=value)

    last = start + len(rows)
    if rows:
        ws.auto_filter.ref = "A%d:%s%d" % (start, get_column_letter(len(headers)), last)
    ws.freeze_panes = ws.cell(row=start + 1, column=1)

    # Widths from the content, capped so one long sentence does not blow the sheet out.
    for column, name in enumerate(headers, start=1):
        longest = len(str(name))
        for row in rows:
            if column - 1 < len(row):
                longest = max(longest, len(str(row[column - 1] if row[column - 1] is not None else "")))
        ws.column_dimensions[get_column_letter(column)].width = min(46, max(10, longest + 2))
    return last


def _num(value: Any) -> Any:
    """Keep numbers numeric so a spreadsheet can sort and chart them."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _when(value: Any) -> str:
    return str(value or "").replace("T", " ")[:16]


def build(report_id: str, data: Dict[str, Any], *,
          farm: Optional[Dict[str, Any]] = None, title: str = "") -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    heading = title or report_id.replace("-", " ").title()

    builder = {
        "soil": _soil,
        "advisory": _advisory,
        "calls": _calls,
        "sensor-history": _sensor_history,
    }.get(report_id)

    if builder is None:
        ws = wb.create_sheet("Report")
        _sheet_title(ws, heading, "No builder for this report.")
    else:
        builder(wb, data, heading)

    _about_sheet(wb, farm or {})
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _about_sheet(wb: Workbook, farm: Dict[str, Any]) -> None:
    ws = wb.create_sheet("About")
    row = _sheet_title(ws, "About this workbook",
                       "Where the figures came from, and what they are not.")
    rows = [
        ("Farmer", farm.get("farmer_name") or farm.get("name") or "-"),
        # Named only when the download was for one plot; "-" is the honest answer
        # for a workbook covering the whole farm.
        ("Field", farm.get("field_name") or "-"),
        ("Village", farm.get("village") or "-"),
        ("District", farm.get("district") or "-"),
        ("State", farm.get("state") or "-"),
        ("Land (acres)", _num(farm.get("area_acres") or farm.get("land_acres") or "-")),
        ("Crop", farm.get("crop") or "-"),
        ("", ""),
        ("Weather", "Open-Meteo"),
        ("Soil background", "ISRIC SoilGrids"),
        ("Mandi prices", "Agmarknet via data.gov.in"),
        ("Maps", "OpenStreetMap"),
        ("Sensor", "The farm's own 7-in-1 probe, where one is connected"),
        ("", ""),
        ("Note", "Rows marked simulation were generated for demonstration and did not "
                 "come from a device in a field."),
        ("Note", "Rows marked farmer_reported were typed in by the farmer rather than "
                 "measured."),
    ]
    _write_table(ws, row, ["Field", "Value"], rows)


def _soil(wb: Workbook, data: Dict[str, Any], heading: str) -> None:
    reading = data.get("reading") or {}
    validation = data.get("validation") or {}
    parameters = data.get("parameters") or []
    index = data.get("index") or {}

    ws = wb.create_sheet("Soil")
    mode = str(reading.get("mode") or "")
    row = _sheet_title(ws, heading,
                       "SIMULATED - not measured in a field" if mode == "simulation"
                       else "Measured by the farm sensor")

    summary = [
        ("Soil index", _num(index.get("index"))),
        ("Band", index.get("label")),
        ("Validation", validation.get("status")),
        ("Completeness", _num(validation.get("completeness"))),
        ("Device", reading.get("device_id")),
        ("Mode", mode),
        ("Provenance", reading.get("provenance")),
        ("Recorded", _when(reading.get("recorded_at"))),
    ]
    row = _write_table(ws, row, ["Field", "Value"], summary) + 2

    rows = []
    for p in parameters:
        low_high = p.get("typical_range") or []
        rows.append((p.get("label"), _num(p.get("value")), p.get("unit"), p.get("band"),
                     _num(low_high[0]) if len(low_high) == 2 else None,
                     _num(low_high[1]) if len(low_high) == 2 else None))
    last = _write_table(ws, row, ["Parameter", "Value", "Unit", "Band",
                                  "Typical low", "Typical high"], rows)

    if rows:
        chart = BarChart()
        chart.title = "Measured parameters"
        chart.height, chart.width = 8, 18
        chart.add_data(Reference(ws, min_col=2, min_row=row, max_row=last), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=row + 1, max_row=last))
        chart.legend = None
        ws.add_chart(chart, "G%d" % row)

    notes = [(n,) for n in (list(validation.get("errors") or [])
                            + list(validation.get("warnings") or []))]
    if notes:
        _write_table(ws, last + 3, ["Validation notes"], notes)


def _advisory(wb: Workbook, data: Dict[str, Any], heading: str) -> None:
    advisories = data.get("advisories") or []
    ws = wb.create_sheet("Advisories")
    row = _sheet_title(ws, heading, "%d advisory record(s), most recent first"
                       % len(advisories))
    rows = [(_when(a.get("created_at")), a.get("intent"), a.get("language"),
             a.get("question"), len(a.get("agents_invoked") or []),
             a.get("request_id")) for a in advisories]
    _write_table(ws, row, ["When", "Intent", "Language", "Question", "Agents",
                           "Request id"], rows)

    counts: Dict[str, int] = {}
    for a in advisories:
        key = str(a.get("intent") or "general")
        counts[key] = counts.get(key, 0) + 1
    if counts:
        ws2 = wb.create_sheet("By topic")
        r = _sheet_title(ws2, "What the farmer asks about", "Advisories grouped by intent")
        ordered = sorted(counts.items(), key=lambda kv: -kv[1])
        last = _write_table(ws2, r, ["Intent", "Advisories"], ordered)
        chart = BarChart()
        chart.title = "Advisories by intent"
        chart.height, chart.width = 9, 18
        chart.add_data(Reference(ws2, min_col=2, min_row=r, max_row=last),
                       titles_from_data=True)
        chart.set_categories(Reference(ws2, min_col=1, min_row=r + 1, max_row=last))
        chart.legend = None
        ws2.add_chart(chart, "E%d" % r)


def _calls(wb: Workbook, data: Dict[str, Any], heading: str) -> None:
    calls = data.get("calls") or []
    ws = wb.create_sheet("Calls")
    row = _sheet_title(ws, heading, "%d call(s), most recent first" % len(calls))
    rows = []
    for c in calls:
        detail = c.get("detail") or {}
        turns = detail.get("turns") or []
        rows.append((_when(c.get("created_at")), c.get("phone"), c.get("direction"),
                     c.get("status"), c.get("mode"), c.get("language"), len(turns),
                     (turns[-1].get("question") if turns else "")))
    _write_table(ws, row, ["When", "Phone", "Direction", "Status", "Mode", "Language",
                           "Turns", "Last thing said"], rows)

    # Every spoken turn, so a transcript can actually be read and filtered.
    transcript = []
    for c in calls:
        for i, turn in enumerate(((c.get("detail") or {}).get("turns") or []), start=1):
            transcript.append((_when(c.get("created_at")), c.get("phone"), i,
                               turn.get("question"), turn.get("reply")))
    if transcript:
        ws2 = wb.create_sheet("Transcripts")
        r = _sheet_title(ws2, "Transcripts", "Every spoken turn, oldest call first")
        _write_table(ws2, r, ["Call", "Phone", "Turn", "Farmer said", "Saarthi said"],
                     transcript)


def _sensor_history(wb: Workbook, data: Dict[str, Any], heading: str) -> None:
    readings = data.get("readings") or []
    ws = wb.create_sheet("Readings")
    simulated = sum(1 for r in readings if str(r.get("mode")) == "simulation")
    row = _sheet_title(ws, heading,
                       "%d reading(s); %d are SIMULATED" % (len(readings), simulated))

    ordered = list(reversed(readings))
    rows = [(_when(r.get("recorded_at")), r.get("mode"), r.get("provenance"),
             _num(r.get("moisture_pct")), _num(r.get("temperature_c")), _num(r.get("ph")),
             _num(r.get("ec_ds_m")), _num(r.get("nitrogen_mg_kg")),
             _num(r.get("phosphorus_mg_kg")), _num(r.get("potassium_mg_kg")))
            for r in ordered]
    last = _write_table(ws, row, ["Recorded", "Mode", "Provenance", "Moisture %",
                                  "Temp C", "pH", "EC dS/m", "N mg/kg", "P mg/kg",
                                  "K mg/kg"], rows)

    if len(rows) > 1:
        chart = LineChart()
        chart.title = "Soil moisture over time"
        chart.height, chart.width = 9, 20
        chart.y_axis.title = "%"
        chart.add_data(Reference(ws, min_col=4, min_row=row, max_row=last),
                       titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=row + 1, max_row=last))
        ws.add_chart(chart, "L%d" % row)
