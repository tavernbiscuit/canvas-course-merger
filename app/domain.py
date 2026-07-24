from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

SIS_ID_PATTERN = re.compile(
    r"^(?P<year>\d{4})\."
    r"(?P<season>[a-z][a-z0-9_-]*)\."
    r"(?P<abbreviation>[a-z][a-z0-9_-]*)\."
    r"(?P<course_number>[a-z0-9][a-z0-9_-]*)\."
    r"(?P<crn>\d+)$",
    re.IGNORECASE,
)


class DomainValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedSisId:
    raw: str
    normalized: str
    year: int
    season: str
    abbreviation: str
    course_number: str
    crn: str


def parse_sis_id(value: str) -> ParsedSisId:
    raw = value.strip()
    match = SIS_ID_PATTERN.fullmatch(raw)
    if not match:
        raise DomainValidationError(
            "Expected year.season.abbreviation.course_number.crn "
            "(for example 2026.fall.clj.101.12345)"
        )
    parts = match.groupdict()
    year = int(parts["year"])
    season = parts["season"].lower()
    abbreviation = parts["abbreviation"].upper()
    course_number = parts["course_number"].upper()
    crn = parts["crn"]
    normalized = f"{year}.{season}.{abbreviation.lower()}.{course_number.lower()}.{crn}"
    return ParsedSisId(
        raw=raw,
        normalized=normalized,
        year=year,
        season=season,
        abbreviation=abbreviation,
        course_number=course_number,
        crn=crn,
    )


def natural_key(value: str) -> tuple[tuple[int, int | str], ...]:
    parts = re.split(r"(\d+)", value)
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold()) for part in parts if part
    )


def course_descriptor(sections: Iterable[ParsedSisId]) -> str:
    numbers_by_abbreviation: dict[str, set[str]] = defaultdict(set)
    for section in sections:
        numbers_by_abbreviation[section.abbreviation].add(section.course_number)
    if not numbers_by_abbreviation:
        raise DomainValidationError("At least one section is required")

    abbreviations = sorted(numbers_by_abbreviation)
    number_lists = {
        abbreviation: sorted(numbers, key=natural_key)
        for abbreviation, numbers in numbers_by_abbreviation.items()
    }

    if all(len(numbers) == 1 for numbers in number_lists.values()):
        ordered_numbers = [number_lists[abbreviation][0] for abbreviation in abbreviations]
        abbreviation_text = "/".join(abbreviations)
        number_text = (
            ordered_numbers[0] if len(set(ordered_numbers)) == 1 else "/".join(ordered_numbers)
        )
        return f"{abbreviation_text} {number_text}"

    return " / ".join(
        f"{abbreviation} {'/'.join(number_lists[abbreviation])}" for abbreviation in abbreviations
    )


def destination_metadata(sections: Iterable[ParsedSisId]) -> tuple[str, str]:
    section_list = list(sections)
    descriptor = course_descriptor(section_list)
    crns = sorted({section.crn for section in section_list}, key=natural_key)
    suffix = ", ".join(crns) if len(section_list) == 2 else "All Sections"
    return f"{descriptor} ({suffix})", descriptor


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()
