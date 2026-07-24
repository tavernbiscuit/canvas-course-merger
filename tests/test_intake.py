from __future__ import annotations

import io

import pytest
from openpyxl import Workbook
from starlette.datastructures import FormData

from app.intake import (
    IntakeError,
    parse_csv,
    parse_destination_groups,
    parse_xlsx,
)


def test_parse_csv():
    rows = parse_csv(
        b"merge_group_key,source_sis_id,destination_subaccount\nalpha,2026.fall.clj.101.12345,42\n"
    )
    assert rows[0].merge_group_key == "alpha"
    assert rows[0].destination_subaccount == 42


def test_parse_new_two_column_csv():
    rows = parse_csv(
        b"merge_group_key,source_sis_id\n"
        b"alpha,2026.fall.clj.101.12345\n"
        b"alpha,2026.fall.clj.101.34567\n"
    )
    assert len(rows) == 2
    assert all(row.destination_subaccount is None for row in rows)


def test_parse_structured_destination_groups():
    rows = parse_destination_groups(
        FormData(
            [
                ("group_index", "4"),
                ("source_sis_id_4", "2026.fall.clj.101.12345"),
                ("source_sis_id_4", "2026.fall.clj.101.34567"),
                ("group_index", "9"),
                ("destination_account_id_9", "20"),
                ("source_sis_id_9", "2026.fall.eng.101.23456"),
                ("source_sis_id_9", "2026.fall.eng.102.45678"),
            ]
        )
    )
    assert [row.merge_group_key for row in rows] == [
        "destination-1",
        "destination-1",
        "destination-2",
        "destination-2",
    ]
    assert all(row.destination_subaccount is None for row in rows)


def test_structured_destination_requires_two_sections():
    with pytest.raises(IntakeError, match="at least two source Canvas sections"):
        parse_destination_groups(
            FormData(
                [
                    ("group_index", "0"),
                    ("source_sis_id_0", "2026.fall.clj.101.12345"),
                ]
            )
        )


def test_parse_xlsx():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["merge_group_key", "source_sis_id"])
    sheet.append(["alpha", "2026.fall.clj.101.12345"])
    stream = io.BytesIO()
    workbook.save(stream)
    rows = parse_xlsx(stream.getvalue())
    assert rows[0].source_sis_id == "2026.fall.clj.101.12345"
    assert rows[0].destination_subaccount is None


def test_missing_columns_rejected():
    with pytest.raises(IntakeError, match="Missing required columns"):
        parse_csv(b"source_sis_id\n2026.fall.clj.101.12345\n")
