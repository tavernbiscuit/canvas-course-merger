# Canvas course merge automation

## Product boundary

This application is an administrative operations tool. Faculty continue to
submit merge requests through the institution's existing process, where their
instructor status is verified. A Canvas administrator records the faculty
identity and external request reference, enters or imports the requested
sections, reviews live validation, and executes the merge.

Faculty-facing language calls the operation a **merge**. Internally, Canvas
implements it by cross-listing sections into a newly created destination course.

## Access and audit

Administrators sign in through a scoped Canvas OAuth2 Developer Key. The
application verifies that the user has manageable accounts and required Canvas
permissions. Canvas API calls use that administrator's user-specific token,
while the application records an append-only workflow audit containing:

- Canvas administrator identity
- Faculty identity and external request reference
- Submitted SIS identifiers
- Validation snapshots and confirmation hash
- Destination provisioning and section-move attempts
- Canvas request identifiers, sanitized errors, retries, and outcomes

OAuth tokens are encrypted in the database. The application rechecks permissions
and refreshes expiring credentials before execution.

## Intake contract

Manual entry, CSV, and XLSX intake support:

| Field | Requirement |
| --- | --- |
| `merge_group_key` | Groups sections that share one destination. |
| `source_sis_id` | Uses `year.season.abbreviation.number.crn`. |
| `destination_subaccount` | Optional Canvas account ID; required when a mixed-subaccount group cannot default safely. |

The institution uses the same value for the SIS course ID and SIS section ID.
The application resolves the section by SIS section ID, then verifies that the
original course has the identical SIS course ID.

Every group must contain at least two distinct sections and share one SIS
year/season and one Canvas enrollment term. Departments, course numbers, CRNs,
and source subaccounts may differ. One ineligible or already-cross-listed
section blocks the entire group before a destination is created.

## Destination policy

The application derives ordered abbreviation/number pairs:

- `CLJ 101`
- `CLJ/ENG 101`
- `CLJ/ENG 101/102`
- `CLJ 101/102 / ENG 201`

Two-section names list numeric CRNs, such as
`CLJ/ENG 101/102 (12345, 23456)`. Three or more sections use
`(All Sections)`. The descriptor without the parenthetical suffix is the Canvas
course code.

The destination:

- Uses the source sections' verified Canvas enrollment term.
- Defaults to their common approved subaccount.
- Requires an administrator-selected manageable subaccount for cross-subaccount groups.
- Remains within the configured Canvas root account.
- Has no SIS course ID.
- Is created blank and unpublished.

## Safety and execution

Approval is bound to an exact validation snapshot and generated destination
payload. Immediately before provisioning, the worker reruns whole-group
validation. It then re-fetches each section immediately before its mutation.

After Canvas returns the new course ID, the worker commits it as a recovery
boundary before moving any sections. Section failures do not reverse successful
moves; they are recorded and may be safely retried against the same destination.
An empty destination is retained for retry.

Course creation is special because the destination has no unique SIS ID. Before
sending the creation request, the worker durably marks the outcome as unknown.
Only a received successful response clears that state and records the Canvas
course ID. A timeout, ambiguous server response, or worker crash during that
window requires manual reconciliation and is never automatically retried.

The application never publishes courses, copies content, de-cross-lists
sections, or automatically rolls back successful moves.

## Architecture

The implementation is a deployment-neutral Python application consisting of:

- FastAPI server-rendered administrative interface
- Isolated Canvas OAuth/API adapter
- SQLAlchemy workflow and audit model
- MySQL 8.0+ production database
- Database-backed background worker
- Alembic migrations
- CSV/XLSX in-memory processing
- Environment-based secrets and configuration
- Health endpoints and structured logging

SQLite is supported only for local development and tests. The application can
run behind any trusted HTTPS reverse proxy and does not depend on RHEL, Podman,
Kubernetes, or a particular hosting provider.

## Production gate

Before production enablement:

1. Register a test/beta-only scoped Canvas Developer Key.
2. Confirm Canvas permission keys and endpoint scopes in the institution's test environment.
3. Configure the permitted root account and optional subaccount allowlist.
4. Run database migrations and backup/restore testing.
5. Exercise eligible, blocked, cross-subaccount, stale, partial-failure, OAuth-revocation, rate-limit, and ambiguous-creation scenarios.
6. Pilot with Canvas test/beta before creating a production Developer Key.
