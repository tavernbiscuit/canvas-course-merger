from __future__ import annotations

import pytest

from app.domain import DomainValidationError, destination_metadata, parse_sis_id


def sections(*values: str):
    return [parse_sis_id(value) for value in values]


def test_parse_and_normalize_sis_id():
    parsed = parse_sis_id("2026.Fall.clj.101.12345")
    assert parsed.normalized == "2026.fall.clj.101.12345"
    assert parsed.abbreviation == "CLJ"
    assert parsed.course_number == "101"


@pytest.mark.parametrize(
    "value",
    ["", "2026.fall.clj.101", "fall.2026.clj.101.12345", "2026.fall.clj.101.ABC"],
)
def test_reject_invalid_sis_id(value):
    with pytest.raises(DomainValidationError):
        parse_sis_id(value)


def test_single_course_pair_two_sections():
    name, code = destination_metadata(
        sections("2026.fall.clj.101.12345", "2026.fall.clj.101.23456")
    )
    assert code == "CLJ 101"
    assert name == "CLJ 101 (12345, 23456)"


def test_compresses_shared_number_across_departments():
    name, code = destination_metadata(
        sections("2026.fall.eng.101.23456", "2026.fall.clj.101.12345")
    )
    assert code == "CLJ/ENG 101"
    assert name == "CLJ/ENG 101 (12345, 23456)"


def test_preserves_parallel_abbreviation_number_pairing():
    _, code = destination_metadata(sections("2026.fall.eng.102.23456", "2026.fall.clj.101.12345"))
    assert code == "CLJ/ENG 101/102"


def test_groups_multiple_numbers_by_abbreviation():
    name, code = destination_metadata(
        sections(
            "2026.fall.eng.201.33333",
            "2026.fall.clj.102.22222",
            "2026.fall.clj.101.11111",
        )
    )
    assert code == "CLJ 101/102 / ENG 201"
    assert name == "CLJ 101/102 / ENG 201 (All Sections)"
