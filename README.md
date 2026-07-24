# Canvas Course Merger

Canvas Course Merger is an administrative web application that creates a new,
blank Canvas course and cross-lists SIS-backed sections into it. It validates
the current Canvas state before creating anything and records which Canvas
administrator performed every operation. Confirmed work runs through a durable
background queue with live progress on the request page.

Faculty do not use this application directly. They continue submitting merge
requests through the existing institutional process. After that request has
been verified, a Canvas administrator enters it here.

## Can I test it locally?

Yes. The repository includes a small local Canvas simulator, so you can test the
complete sign-in, intake, validation, destination creation, worker, and
cross-list workflow without:

- Hosting the application publicly
- Creating a real Canvas Developer Key
- Connecting to your institution's Canvas environment
- Changing any real courses

The simulator is unauthenticated and intended only for a computer you control.
Bind it to `127.0.0.1` exactly as shown below. Never deploy or expose it.

## Local demo: start here

### 1. Install the application

Python 3.12 or newer is required.

```bash
git clone https://github.com/tavernbiscuit/canvas_course_merger.git
cd canvas_course_merger
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.local.example .env
.venv/bin/alembic upgrade head
```

The application automatically loads `.env`. The supplied local values are
deliberately insecure and must never be used outside local development.

### 2. Start the local Canvas simulator

Open a terminal in the repository:

```bash
.venv/bin/uvicorn app.mock_canvas:app --host 127.0.0.1 --port 9000
```

The simulator starts with four SIS-backed sections:

```text
2026.fall.clj.101.12345
2026.fall.clj.101.34567
2026.fall.eng.101.23456
2026.fall.eng.102.45678
```

Restarting the simulator restores this original Canvas state. It does not remove
requests or audit history from `canvas_merger.db`; those application records are
intentionally persistent across restarts.

### 3. Start the web application

Open a second terminal:

```bash
cd canvas_course_merger
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 4. Start the execution worker

Open a third terminal:

```bash
cd canvas_course_merger
.venv/bin/canvas-merger-worker
```

The worker performs confirmed course creation and cross-list operations. A
request will remain queued if the worker is not running.

### 5. Try a merge

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) and select **Sign in with
Canvas**. In local-demo mode, the simulator signs you in automatically as
`Local Canvas Admin`.

Create a request with any example faculty identity and reference. Under
**Destination course 1**, enter these two source SIS section IDs:

```text
2026.fall.clj.101.12345
2026.fall.clj.101.34567
```

The application will propose:

```text
Course name: CLJ 101 (12345, 34567)
Course code: CLJ 101
Term: Fall 2026
Subaccount: Criminal Justice
```

Type the generated course name into the confirmation field, queue the merge,
and watch the live tracker move through its safety check, destination creation,
and section cross-list steps. The local simulator is fast, so some stages may
complete between tracker updates.

To try a cross-department and cross-subaccount merge, restart the simulator and
use these two section IDs in one destination course:

```text
2026.fall.clj.101.12345
2026.fall.eng.101.23456
```

The review screen will require a preferred destination subaccount because the
source sections span subaccounts. It will generate the course code
`CLJ/ENG 101`.

## Entering merge requests

Structured entry is the primary workflow. Each **Destination course** card
represents one new blank and unpublished Canvas course:

1. Add at least two source SIS section IDs to the card.
2. Use **Add another destination course** when one faculty request contains
   multiple separate merges.
3. Save the request so the application can resolve and validate the sections in
   Canvas.
4. If the source sections span subaccounts, select one destination subaccount
   on the review screen and revalidate.
5. Review the generated destination payload, type its course name to confirm,
   and monitor execution on the same page.

The application generates an internal group key for structured entries; admins
do not enter or manage that key.

Subaccount selection is validation-driven and intentionally conservative:

- If every source section belongs to one approved Canvas subaccount, that
  subaccount becomes the destination automatically. No dropdown is shown during
  intake.
- If source sections span subaccounts, the application requires an explicit
  group-level destination selection on the review screen.
- The application does not silently choose the first section's subaccount for a
  mixed-subaccount merge.
- After validation, **Change destination subaccount** remains available as an
  optional review-screen control for an approved exception.

Subaccounts are not maintained in the application. When a choice is required,
the dropdown is populated from the Canvas accounts that the signed-in
administrator can manage. `CANVAS_ALLOWED_ACCOUNT_IDS` may optionally restrict
that list.

## Destination course behavior

Each destination group creates one blank, unpublished Canvas course:

- It uses the verified Canvas enrollment term and selected destination
  subaccount.
- It receives no SIS course ID. The child sections retain their SIS IDs and
  remain the enrollment-sync boundary.
- Its course code is the generated department/course descriptor, such as
  `CLJ 101`, `CLJ/ENG 101`, or `CLJ/ENG 101/102`.
- A two-section course name lists both CRNs, such as
  `CLJ/ENG 101 (12345, 23456)`.
- A course with three or more sections uses `(All Sections)`.

The application does not publish the destination, copy content, de-cross-list
sections, or automatically undo successful section moves.

## Live validation and execution progress

**Recheck current Canvas state** is a read-only safety action. It contacts
Canvas again to refresh:

- The signed-in administrator's source and destination account permissions
- Each SIS section's identity, source course, and current cross-list state
- The common Canvas enrollment term
- Destination subaccount eligibility
- The generated destination name, course code, and confirmation snapshot

It does not create a course or move a section. If Canvas state or generated
destination metadata has changed, the prior confirmation is invalidated. The
worker performs the same safety check once more immediately before course
creation to close the gap between an administrator's review and execution.

After confirmation, the request page displays a live execution tracker. It
shows the destination's queue position, pre-execution safety check, blank-course
creation, section-by-section cross-list progress, and final outcome. The browser
polls a read-only application endpoint while work is active; those status reads
come from the application database—PostgreSQL in production or SQLite in the
local demo—and do not make extra Canvas API calls. Polling stops at a final
state, and the page loads the final retry or reconciliation actions
automatically.

Execution is not tied to the browser tab. An administrator may leave the page
and return to the request later; the separate worker continues processing the
database-backed job.

## Optional bulk import

CSV and XLSX upload remains available under **Bulk import** for unusually large
requests. New files need only two columns:

| Column | Required | Description |
| --- | --- | --- |
| `merge_group_key` | Yes | Temporary file-local label. Rows with the same value are cross-listed into one new Canvas destination course. |
| `source_sis_id` | Yes | Source Canvas SIS section ID in the form `year.season.abbreviation.number.crn`. |

Example:

```csv
merge_group_key,source_sis_id
destination-clj-101,2026.fall.clj.101.12345
destination-clj-101,2026.fall.clj.101.34567
destination-clj-eng-101,2026.fall.clj.101.67890
destination-clj-eng-101,2026.fall.eng.101.78901
```

`merge_group_key` is never sent to Canvas and does not become a course name,
course code, SIS ID, Canvas course ID, or section ID. It only associates rows
within that uploaded file.

The previous optional `destination_subaccount` column is still accepted for
backward compatibility, but new files should omit it. Common source
subaccounts are assigned automatically, and mixed-subaccount destinations are
chosen once per destination on the review screen.

## Frontend and UIC branding

The interface follows the [UIC visual identity](https://brand.uic.edu/visual-identity/)
and the visual language used by [Learning Technology
Solutions](https://learning.uic.edu/). It uses the official Fire Engine Red,
Navy Pier Blue, Steel Gray, and Expo White palette. The font stack prefers
Theinhardt when it is available on an institution-managed device and otherwise
falls back to Helvetica or Arial; the application does not download a
proprietary font or third-party web font.

The frontend is server-rendered with FastAPI and Jinja. A small, framework-free
JavaScript file provides progressive enhancements such as repeatable destination
and section fields, bulk-upload mode feedback, dismissible notices, and
table-row navigation. It also polls the read-only progress endpoint while an
execution is active. Core navigation, form submission, validation results, and
confirmations continue to work without a client-side application runtime.

This keeps local development and production deployment to one Python
application with no Node.js build step. A React, Vue, or similar single-page
frontend can still be introduced later if the application develops workflows
that need substantial client-side state, but it is not required for the current
administrative intake and review screens.

## Automated tests

Automated tests do not require Canvas, PostgreSQL, or a public web server:

```bash
.venv/bin/pytest
.venv/bin/ruff check app tests migrations
.venv/bin/alembic check
```

Tests cover SIS parsing, destination naming, CSV/XLSX intake, blocked groups,
mixed subaccounts, stale-state detection, partial failure, retries, ambiguous
course creation, queue positions, execution progress states, OAuth URL
construction, and web-route rendering.

## Connecting to real Canvas

The local simulator is only for development. A real Canvas connection requires
a hosted application URL and a Canvas API Developer Key.

### 1. Establish the application URL

Deploy the application behind HTTPS and choose its stable base URL, for example:

```text
https://canvas-merger.example.edu
```

The OAuth callback is:

```text
https://canvas-merger.example.edu/auth/callback
```

That exact callback must be registered on the Canvas Developer Key. If your
Canvas configuration requires a publicly reachable callback, deploy the
application first or use an institution-approved temporary HTTPS tunnel for
development. Do not expose the included Canvas simulator through that tunnel.

### 2. Create a scoped Developer Key

In the Canvas root account, create an API Developer Key with the redirect URI
above. The required endpoint scopes are listed in `.env.example`.

The signed-in administrator must also have Canvas permissions to:

- Read SIS data
- Manage courses
- Edit/manage course sections
- Create courses in the selected destination subaccount

Use a test/beta-only Developer Key first. Do not begin against production
Canvas.

### 3. Configure the application

```bash
cp .env.example .env
```

Set at least:

```text
APP_ENV=production
APP_BASE_URL=https://canvas-merger.example.edu
DATABASE_URL=postgresql+psycopg://...
CANVAS_BASE_URL=https://your-institution.instructure.com
CANVAS_CLIENT_ID=...
CANVAS_CLIENT_SECRET=...
CANVAS_ROOT_ACCOUNT_ID=...
```

Generate unique secrets:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Use the first value for `APP_SESSION_SECRET` and the second for
`APP_TOKEN_ENCRYPTION_KEY`.

### 4. Initialize and run

```bash
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
.venv/bin/canvas-merger-worker
```

Run the web process and worker under a process manager and place the web process
behind an HTTPS reverse proxy. Both processes must receive the same database,
Canvas, and encryption configuration.

## Operational notes

- Production requires PostgreSQL; SQLite is limited to local development.
- Back up PostgreSQL. It contains workflow state, audit events, and encrypted
  OAuth credentials.
- Restrict `CANVAS_ALLOWED_ACCOUNT_IDS` if only certain subaccounts are in scope.
- Monitor `/health/live` for process liveness and `/health/ready` for database
  readiness.
- Run the web process and execution worker as separately supervised services;
  queued work does not execute without the worker.
- Course creation is never automatically retried after an ambiguous response.
- The application never publishes courses, copies content, de-cross-lists
  sections, or rolls back successful moves.
- The optional container image does not make the application dependent on
  Docker, Podman, RHEL, Kubernetes, or a particular host.
