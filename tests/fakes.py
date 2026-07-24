from __future__ import annotations

from typing import Any

from app.canvas import CanvasError, CanvasResponse


class FakeCanvas:
    def __init__(
        self,
        *,
        sections: dict[str, dict[str, Any]] | None = None,
        courses: dict[int, dict[str, Any]] | None = None,
        accounts: dict[int, dict[str, Any]] | None = None,
        crosslist_failures: set[str] | None = None,
        ambiguous_create: bool = False,
    ):
        self.sections = sections or {}
        self.courses = courses or {}
        self.accounts = accounts or {}
        self.crosslist_failures = crosslist_failures or set()
        self.ambiguous_create = ambiguous_create
        self.created: list[dict[str, Any]] = []
        self.crosslisted: list[tuple[str, int]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def manageable_accounts(self):
        return list(self.accounts.values())

    def account_permissions(self, account_id: int):
        return {"manage_courses": True, "manage_sections": True, "read_sis": True}

    def get_account(self, account_id: int):
        return self.accounts[account_id]

    def get_section_by_sis_id(self, sis_id: str):
        if sis_id not in self.sections:
            raise CanvasError("Section not found", status_code=404)
        return dict(self.sections[sis_id])

    def get_course(self, course_id: int):
        return dict(self.courses[course_id])

    def create_course(self, account_id: int, *, name: str, course_code: str, term_id: int):
        if self.ambiguous_create:
            raise CanvasError("Timed out", ambiguous=True)
        payload = {
            "account_id": account_id,
            "name": name,
            "course_code": course_code,
            "term_id": term_id,
        }
        self.created.append(payload)
        return CanvasResponse({"id": 9001}, 200, "create-request")

    def crosslist_section(self, sis_id: str, destination_course_id: int):
        if sis_id in self.crosslist_failures:
            raise CanvasError("Temporary failure", status_code=503, retryable=True)
        self.crosslisted.append((sis_id, destination_course_id))
        section = self.sections[sis_id]
        section["nonxlist_course_id"] = section["course_id"]
        section["course_id"] = destination_course_id
        return CanvasResponse(dict(section), 200, f"xlist-{sis_id}")


def canvas_fixture(*, mixed_accounts: bool = False, already_crosslisted: bool = False):
    sis_a = "2026.fall.clj.101.12345"
    sis_b = "2026.fall.eng.101.23456"
    account_b = 20 if mixed_accounts else 10
    sections = {
        sis_a: {
            "id": 101,
            "course_id": 201,
            "sis_section_id": sis_a,
            "sis_course_id": sis_a,
            "nonxlist_course_id": 201 if already_crosslisted else None,
        },
        sis_b: {
            "id": 102,
            "course_id": 202,
            "sis_section_id": sis_b,
            "sis_course_id": sis_b,
            "nonxlist_course_id": None,
        },
    }
    courses = {
        201: {
            "id": 201,
            "sis_course_id": sis_a,
            "account_id": 10,
            "term": {"id": 50, "name": "Fall 2026"},
        },
        202: {
            "id": 202,
            "sis_course_id": sis_b,
            "account_id": account_b,
            "term": {"id": 50, "name": "Fall 2026"},
        },
    }
    accounts = {
        10: {"id": 10, "name": "Justice", "root_account_id": 1},
        20: {"id": 20, "name": "English", "root_account_id": 1},
    }
    return FakeCanvas(sections=sections, courses=courses, accounts=accounts)
