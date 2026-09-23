from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

import pandas as pd

from aml_agent.analytics.review_workbook import REPORT_SHEET_NAMES, build_review_workbook

_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def test_review_workbook_is_deterministic_and_preserves_gid_as_text() -> None:
    gid = 10_000_000_031_152_841_00
    assessments = pd.DataFrame(
        [
            {
                "gid": gid,
                "role": "coordinator",
                "role_score": 0.97,
                "cluster_id": 4,
                "priority_score": 0.96,
                "evidence": "Observed paths from 11 seeds; bridge percentile 94.4%.",
            }
        ]
    )
    clusters = pd.DataFrame(
        [
            {
                "cluster_id": 4,
                "n_nodes": 12,
                "n_seed": 2,
                "sum_kzt_internal": 1_250_000.0,
                "top_gids": [gid],
                "hypothesis": "Observed connected activity for analyst review.",
            }
        ]
    )
    top_nodes = pd.DataFrame(
        [
            {
                "rank": 1,
                "gid": gid,
                "role": "coordinator",
                "priority_score": 0.96,
                "why": "Observed paths from 11 seeds; in/out degree 8/2.",
            }
        ]
    )
    audit = {"run_id": "00000000-0000-0000-0000-000000000001", "target_gids": [gid]}

    first = build_review_workbook(assessments, clusters, top_nodes, audit)
    second = build_review_workbook(assessments, clusters, top_nodes, audit)

    assert first == second
    assert first.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        names = tuple(
            sheet.attrib["name"]
            for sheet in workbook.findall(f"{_MAIN_NS}sheets/{_MAIN_NS}sheet")
        )
        assert names == REPORT_SHEET_NAMES

        queue = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        pane = queue.find(f"{_MAIN_NS}sheetViews/{_MAIN_NS}sheetView/{_MAIN_NS}pane")
        assert pane is not None and pane.attrib["state"] == "frozen"
        queue_table = ElementTree.fromstring(archive.read("xl/tables/table1.xml"))
        assert queue_table.find(f"{_MAIN_NS}autoFilter") is not None
        gid_cell = queue.find(f".//{_MAIN_NS}c[@r='B6']")
        assert gid_cell is not None and gid_cell.attrib.get("t") == "s"

        columns = queue.findall(f"{_MAIN_NS}cols/{_MAIN_NS}col")
        assert any(float(column.attrib["width"]) >= 90 for column in columns)
