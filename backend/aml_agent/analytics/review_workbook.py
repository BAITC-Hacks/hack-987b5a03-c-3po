"""Reader-facing Excel export for manual AML review.

The CSV files remain the authoritative machine-readable contract.  This module
only presents the same deterministic values with spreadsheet ergonomics that a
CSV file cannot store: widths, wrapped text, filters, number formats and frozen
headers.
"""

from __future__ import annotations

import io
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

import pandas as pd
import xlsxwriter

from .exports import CLUSTERS_COLUMNS, NODES_ROLES_COLUMNS, TOP_NODES_COLUMNS

REPORT_ARTIFACT_NAME: Final = "aml_review_report.xlsx"
REPORT_SHEET_NAMES: Final[tuple[str, ...]] = (
    "Priority queue",
    "Node assessments",
    "Clusters",
    "Run audit",
)

_BRAND_BLUE = "#163A5F"
_ACCENT_BLUE = "#DCE6F1"
_TEXT = "#17212B"
_MUTED = "#5B6573"


def build_review_workbook(
    assessments: pd.DataFrame,
    clusters: pd.DataFrame,
    top_nodes: pd.DataFrame,
    audit: Mapping[str, Any],
) -> bytes:
    """Return deterministic XLSX bytes for the verified export bundle."""

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        output,
        {
            "in_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    )
    workbook.set_properties(
        {
            "title": "AML review report",
            "subject": "Reader-facing copy of deterministic AML analytics exports",
            "author": "AML Agent",
            "company": "AML Agent",
            "created": datetime(1980, 1, 1),
        }
    )

    formats = _formats(workbook)
    _write_priority_sheet(
        workbook,
        formats,
        top_nodes.loc[:, TOP_NODES_COLUMNS].sort_values("rank", kind="mergesort"),
    )
    _write_assessments_sheet(
        workbook,
        formats,
        assessments.loc[:, NODES_ROLES_COLUMNS].sort_values("gid", kind="mergesort"),
    )
    _write_clusters_sheet(
        workbook,
        formats,
        clusters.loc[:, CLUSTERS_COLUMNS].sort_values("cluster_id", kind="mergesort"),
    )
    _write_audit_sheet(workbook, formats, audit)
    workbook.close()
    return output.getvalue()


def _formats(workbook: xlsxwriter.Workbook) -> dict[str, xlsxwriter.format.Format]:
    base = {"font_name": "Arial", "font_size": 10, "font_color": _TEXT}
    return {
        "title": workbook.add_format(
            {**base, "bold": True, "font_size": 16, "font_color": _BRAND_BLUE}
        ),
        "note": workbook.add_format({**base, "italic": True, "font_color": _MUTED}),
        "text": workbook.add_format({**base, "valign": "top"}),
        "identifier": workbook.add_format({**base, "num_format": "@", "valign": "top"}),
        "integer": workbook.add_format({**base, "num_format": "#,##0", "valign": "top"}),
        "score": workbook.add_format({**base, "num_format": "0.0%", "valign": "top"}),
        "amount": workbook.add_format(
            {**base, "num_format": '#,##0 "KZT"', "valign": "top"}
        ),
        "wrapped": workbook.add_format({**base, "text_wrap": True, "valign": "top"}),
        "audit_key": workbook.add_format(
            {**base, "bold": True, "bg_color": _ACCENT_BLUE, "valign": "top"}
        ),
    }


def _configure_sheet(
    worksheet: xlsxwriter.worksheet.Worksheet,
    formats: Mapping[str, xlsxwriter.format.Format],
    title: str,
    note: str,
) -> int:
    worksheet.hide_gridlines(2)
    worksheet.set_tab_color(_BRAND_BLUE)
    worksheet.set_zoom(90)
    worksheet.set_row(0, 8)
    worksheet.set_row(1, 24)
    worksheet.write(1, 0, title, formats["title"])
    worksheet.write(2, 0, note, formats["note"])
    return 4


def _add_table(
    worksheet: xlsxwriter.worksheet.Worksheet,
    header_row: int,
    row_count: int,
    columns: Sequence[str],
    name: str,
) -> None:
    worksheet.add_table(
        header_row,
        0,
        header_row + row_count,
        len(columns) - 1,
        {
            "name": name,
            "style": "Table Style Medium 2",
            "columns": [{"header": column} for column in columns],
        },
    )
    worksheet.freeze_panes(header_row + 1, 0)


def _write_priority_sheet(
    workbook: xlsxwriter.Workbook,
    formats: Mapping[str, xlsxwriter.format.Format],
    frame: pd.DataFrame,
) -> None:
    worksheet = workbook.add_worksheet(REPORT_SHEET_NAMES[0])
    header_row = _configure_sheet(
        worksheet,
        formats,
        "Priority review queue",
        "Ranked hypotheses for analyst review. Scores indicate rule strength, not guilt.",
    )
    widths = (8, 22, 18, 16, 95)
    column_formats = (
        formats["integer"],
        formats["identifier"],
        formats["text"],
        formats["score"],
        formats["wrapped"],
    )
    for column, (width, cell_format) in enumerate(zip(widths, column_formats, strict=True)):
        worksheet.set_column(column, column, width, cell_format)
    for offset, row in enumerate(frame.itertuples(index=False, name=None), start=1):
        excel_row = header_row + offset
        worksheet.set_row(excel_row, 34)
        worksheet.write_number(excel_row, 0, int(row[0]), formats["integer"])
        worksheet.write_string(excel_row, 1, str(int(row[1])), formats["identifier"])
        worksheet.write_string(excel_row, 2, str(row[2]), formats["text"])
        worksheet.write_number(excel_row, 3, float(row[3]), formats["score"])
        worksheet.write_string(excel_row, 4, str(row[4]), formats["wrapped"])
    _add_table(worksheet, header_row, len(frame), TOP_NODES_COLUMNS, "PriorityQueue")
    if len(frame):
        worksheet.conditional_format(
            header_row + 1,
            3,
            header_row + len(frame),
            3,
            {
                "type": "3_color_scale",
                "min_color": "#FEE2E2",
                "mid_color": "#FEF3C7",
                "max_color": "#DCFCE7",
            },
        )


def _write_assessments_sheet(
    workbook: xlsxwriter.Workbook,
    formats: Mapping[str, xlsxwriter.format.Format],
    frame: pd.DataFrame,
) -> None:
    worksheet = workbook.add_worksheet(REPORT_SHEET_NAMES[1])
    header_row = _configure_sheet(
        worksheet,
        formats,
        "Node assessments",
        "One deterministic assessment per client. GID is stored as text to preserve every digit.",
    )
    widths = (22, 18, 14, 12, 14, 95)
    column_formats = (
        formats["identifier"],
        formats["text"],
        formats["score"],
        formats["integer"],
        formats["score"],
        formats["wrapped"],
    )
    for column, (width, cell_format) in enumerate(zip(widths, column_formats, strict=True)):
        worksheet.set_column(column, column, width, cell_format)
    for offset, row in enumerate(frame.itertuples(index=False, name=None), start=1):
        excel_row = header_row + offset
        worksheet.set_row(excel_row, 32)
        worksheet.write_string(excel_row, 0, str(int(row[0])), formats["identifier"])
        worksheet.write_string(excel_row, 1, str(row[1]), formats["text"])
        worksheet.write_number(excel_row, 2, float(row[2]), formats["score"])
        worksheet.write_number(excel_row, 3, int(row[3]), formats["integer"])
        worksheet.write_number(excel_row, 4, float(row[4]), formats["score"])
        worksheet.write_string(excel_row, 5, str(row[5]), formats["wrapped"])
    _add_table(worksheet, header_row, len(frame), NODES_ROLES_COLUMNS, "NodeAssessments")


def _write_clusters_sheet(
    workbook: xlsxwriter.Workbook,
    formats: Mapping[str, xlsxwriter.format.Format],
    frame: pd.DataFrame,
) -> None:
    worksheet = workbook.add_worksheet(REPORT_SHEET_NAMES[2])
    header_row = _configure_sheet(
        worksheet,
        formats,
        "Cluster overview",
        "Community summaries and cautious hypotheses derived from observed transfers.",
    )
    widths = (12, 12, 10, 20, 60, 80)
    column_formats = (
        formats["integer"],
        formats["integer"],
        formats["integer"],
        formats["amount"],
        formats["wrapped"],
        formats["wrapped"],
    )
    for column, (width, cell_format) in enumerate(zip(widths, column_formats, strict=True)):
        worksheet.set_column(column, column, width, cell_format)
    for offset, row in enumerate(frame.itertuples(index=False, name=None), start=1):
        excel_row = header_row + offset
        worksheet.set_row(excel_row, 42)
        worksheet.write_number(excel_row, 0, int(row[0]), formats["integer"])
        worksheet.write_number(excel_row, 1, int(row[1]), formats["integer"])
        worksheet.write_number(excel_row, 2, int(row[2]), formats["integer"])
        worksheet.write_number(excel_row, 3, float(row[3]), formats["amount"])
        worksheet.write_string(excel_row, 4, _sequence_text(row[4]), formats["wrapped"])
        worksheet.write_string(excel_row, 5, str(row[5]), formats["wrapped"])
    _add_table(worksheet, header_row, len(frame), CLUSTERS_COLUMNS, "ClusterOverview")


def _write_audit_sheet(
    workbook: xlsxwriter.Workbook,
    formats: Mapping[str, xlsxwriter.format.Format],
    audit: Mapping[str, Any],
) -> None:
    worksheet = workbook.add_worksheet(REPORT_SHEET_NAMES[3])
    header_row = _configure_sheet(
        worksheet,
        formats,
        "Run audit",
        "Metadata captured at export. Downloads are released only after independent verification.",
    )
    worksheet.set_column(0, 0, 28, formats["audit_key"])
    worksheet.set_column(1, 1, 100, formats["wrapped"])
    rows = [(str(key), _audit_value(value)) for key, value in audit.items()]
    for offset, (key, value) in enumerate(rows, start=1):
        excel_row = header_row + offset
        worksheet.set_row(excel_row, 30 if len(value) < 100 else 56)
        worksheet.write_string(excel_row, 0, key, formats["audit_key"])
        worksheet.write_string(excel_row, 1, value, formats["wrapped"])
    _add_table(worksheet, header_row, len(rows), ("Field", "Value"), "RunAudit")


def _sequence_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(int(item)) for item in value)
    return str(value)


def _audit_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value)
