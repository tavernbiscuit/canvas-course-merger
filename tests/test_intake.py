from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from app.intake import IntakeError, parse_csv, parse_manual, parse_xlsx


def test_parse_csv():
    rows = parse_csv(
        b"merge_group_key,source_sis_id,destination_subaccount\nalpha,2026.fall.clj.101.12345,42\n"
    )
    assert rows[0].merge_group_key == "alpha"
    assert rows[0].destination_subaccount == 42


def test_parse_manual_optional_account():
    rows = parse_manual("alpha, 2026.fall.clj.101.12345\nalpha, 2026.fall.eng.101.23456, 42")
    assert [row.destination_subaccount for row in rows] == [None, 42]


def test_parse_xlsx():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["merge_group_key", "source_sis_id", "destination_subaccount"])
    sheet.append(["alpha", "2026.fall.clj.101.12345", 42])
    stream = io.BytesIO()
    workbook.save(stream)
    rows = parse_xlsx(stream.getvalue())
    assert rows[0].source_sis_id == "2026.fall.clj.101.12345"


def test_missing_columns_rejected():
    with pytest.raises(IntakeError, match="Missing required columns"):
        parse_csv(b"source_sis_id\n2026.fall.clj.101.12345\n")
