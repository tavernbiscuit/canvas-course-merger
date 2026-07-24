# Canvas Course Merger

Canvas Course Merger is an administrative web application that creates a new,
blank Canvas course and cross-lists SIS-backed sections into it. It validates
the current Canvas state before creating anything and records which Canvas
administrator performed every operation.

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

Restarting the simulator restores this original state.

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

Create a request with any example faculty identity and reference, then enter:

```text
destination-clj-101, 2026.fall.clj.101.12345
destination-clj-101, 2026.fall.clj.101.34567
```

The application will propose:

```text
Course name: CLJ 101 (12345, 34567)
Course code: CLJ 101
Term: Fall 2026
Subaccount: Criminal Justice
```

Type the generated course name into the confirmation field, queue the merge,
and refresh the request page after the worker processes it.

To try a cross-department and cross-subaccount merge, restart the simulator and
use:

```text
destination-clj-eng-101, 2026.fall.clj.101.12345
destination-clj-eng-101, 2026.fall.eng.101.23456
```

The review screen will require a preferred destination subaccount and will
generate the course code `CLJ/ENG 101`.

## How does `merge_group_key` map to Canvas?

Think of `merge_group_key` as the **destination course group**. It is a
temporary label that answers this question:

> Which source Canvas sections should be cross-listed into the same new
> destination Canvas course?

Give every source section that belongs in the same destination course the same
key. The application validates those sections together, creates one blank and
unpublished Canvas destination course, and cross-lists every eligible section
in that group into it.

- Rows with the **same** key are cross-listed into **one** new destination
  course.
- Rows with **different** keys are cross-listed into **different** new
  destination courses.
- Each destination course group must contain at least two distinct Canvas
  sections.
- The key is not looked up in Canvas and is never sent to Canvas.
- It does not become the Canvas course name, course code, SIS ID, course ID, or
  section ID.
- It only needs to distinguish destination courses within the current intake
  request; a later request may reuse it.

For example, this spreadsheet provisions two new Canvas destination courses:

| `merge_group_key` (destination course group) | Source Canvas SIS section ID | Canvas result |
| --- | --- | --- |
| `destination-clj-101` | `2026.fall.clj.101.12345` | Cross-list into destination A |
| `destination-clj-101` | `2026.fall.clj.101.34567` | Cross-list into destination A |
| `destination-eng-101-102` | `2026.fall.eng.101.23456` | Cross-list into destination B |
| `destination-eng-101-102` | `2026.fall.eng.102.45678` | Cross-list into destination B |

The value is for the admin's reference, so use something recognizable such as
`destination-clj-eng-101`, `ticket-482-destination-a`, or
`nursing-all-sections`. The application derives the real Canvas course name
and course code from the source SIS IDs—not from this label.

## Intake formats

Administrators may enter rows manually or upload CSV/XLSX files.

| Column | Required | Description |
| --- | --- | --- |
| `merge_group_key` | Yes | Destination course group. Use the same value for every source section that should be cross-listed into one new Canvas destination course. |
| `source_sis_id` | Yes | Source Canvas SIS section ID in the form `year.season.abbreviation.number.crn`. |
| `destination_subaccount` | Conditional | Preferred Canvas destination subaccount ID when source sections span subaccounts. |

Example CSV:

```csv
merge_group_key,source_sis_id,destination_subaccount
destination-clj-101,2026.fall.clj.101.12345,
destination-clj-101,2026.fall.clj.101.34567,
destination-clj-eng-101,2026.fall.clj.101.67890,10
destination-clj-eng-101,2026.fall.eng.101.78901,10
```

Leaving `destination_subaccount` blank is normal. If all source sections share
one approved subaccount, the application selects it automatically. If the
sections span subaccounts, the review screen asks the administrator to choose
from the Canvas accounts they can manage.

## Automated tests

Automated tests do not require Canvas, PostgreSQL, or a public web server:

```bash
.venv/bin/pytest
.venv/bin/ruff check app tests migrations
.venv/bin/alembic check
```

Tests cover SIS parsing, destination naming, CSV/XLSX intake, blocked groups,
mixed subaccounts, stale-state detection, partial failure, retries, ambiguous
course creation, OAuth URL construction, and web-route rendering.

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
- Course creation is never automatically retried after an ambiguous response.
- The application never publishes courses, copies content, de-cross-lists
  sections, or rolls back successful moves.
- The optional container image does not make the application dependent on
  Docker, Podman, RHEL, Kubernetes, or a particular host.
