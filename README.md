# Canvas Course Merger

An administrative web application for safely creating blank Canvas destination
courses and cross-listing SIS-backed sections into them.

## What it does

- Authenticates administrators with Canvas OAuth2.
- Records the verified faculty requester and external ticket/reference.
- Accepts manual, CSV, or XLSX intake with multiple merge groups.
- Performs whole-group live Canvas validation before creating anything.
- Generates deterministic cross-department course names and codes.
- Creates blank, unpublished destinations without SIS course IDs.
- Revalidates immediately before execution and processes sections independently.
- Preserves an application audit trail and supports safe failed-section retries.

The application never publishes courses, copies content, de-cross-lists sections,
or automatically retries an ambiguous course-creation request.

## Local setup

Python 3.12 or newer and PostgreSQL are recommended. SQLite is enabled only as a
development convenience.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
```

Load `.env` through your process manager or shell, then run:

```bash
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
.venv/bin/canvas-merger-worker
```

The web application and worker must use the same `DATABASE_URL`,
`APP_TOKEN_ENCRYPTION_KEY`, and Canvas OAuth configuration.

## Canvas Developer Key

Create a scoped API Developer Key in the institution's Canvas root account.
Register this exact redirect URI:

```text
${APP_BASE_URL}/auth/callback
```

The required OAuth scopes are listed in `.env.example`. The authorizing admin
must also have Canvas permissions to read SIS data, manage courses, and edit
course sections in the relevant accounts.

The application uses the signed-in administrator's access token for every
Canvas operation. Access and refresh tokens are encrypted in the database.

## Intake formats

CSV and XLSX files use:

| Column | Required | Description |
| --- | --- | --- |
| `merge_group_key` | Yes | Groups sections into one destination shell. |
| `source_sis_id` | Yes | `year.season.abbreviation.number.crn` |
| `destination_subaccount` | Conditional | Canvas account ID for cross-subaccount merges. |

Example:

```csv
merge_group_key,source_sis_id,destination_subaccount
group-1,2026.fall.clj.101.12345,
group-1,2026.fall.eng.101.23456,
```

If source sections span subaccounts and the column is blank, the admin selects
an approved manageable destination in the review screen.

## Production notes

- Use PostgreSQL and run `alembic upgrade head` before starting a release.
- Set `APP_ENV=production` so insecure development defaults are rejected.
- Set a unique 32+ character session secret and a Fernet encryption key.
- Terminate HTTPS at a trusted reverse proxy.
- Run one or more web processes and at least one worker process.
- Back up PostgreSQL; it contains workflow state, audit events, and encrypted
  OAuth credentials.
- Keep `APP_BASE_URL` and the Canvas redirect URI synchronized.
- Restrict `CANVAS_ALLOWED_ACCOUNT_IDS` when only selected subaccounts are in scope.
- Forward only trusted proxy headers and retain structured application logs.

The included container image is optional. The application does not depend on
Podman, RHEL, Kubernetes, or a specific hosting provider.

## Verification

```bash
.venv/bin/pytest
.venv/bin/alembic upgrade head
```
