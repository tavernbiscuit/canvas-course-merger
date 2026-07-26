# Canvas Course Merger

Canvas Course Merger is an administrative web application that creates a blank
Canvas destination course and cross-lists SIS-backed sections into it. Faculty
authorization remains in the institution's existing request process; a Canvas
administrator enters and executes the approved request.

The application validates live Canvas data before creating anything, repeats
that safety check immediately before execution, and records the administrator,
request provenance, API attempts, and outcomes in an audit log.

## How it works

1. Record the faculty identity and external request or ticket reference.
2. Add at least two source SIS section IDs to each **Destination course**.
3. Validate the sections, term, subaccounts, permissions, and current
   cross-list state against Canvas.
4. Choose a destination subaccount only when the sources span subaccounts or an
   approved exception is needed.
5. Review the generated Canvas payload, type the destination name to confirm,
   and queue the merge.
6. Follow course creation and section-level progress on the request page.

Previous requests can be found by entering a complete or partial ticket
reference on the requests page. Request history is shown 25 entries at a time;
the audit log is shown 50 events at a time.

Source SIS IDs use:

```text
year.season.course_abbreviation.course_number.crn
2026.fall.clj.101.12345
```

Each destination is blank, unpublished, and has no SIS course ID. It uses the
validated Canvas term and selected subaccount. Examples include:

```text
CLJ 101 (12345, 34567)
CLJ/ENG 101 (12345, 23456)
CLJ/ENG 101/102 (All Sections)
```

The application does not publish courses, copy content, de-cross-list sections,
or roll back successful moves. Failed sections can be retried against the
existing destination. An ambiguous course-creation response requires
administrator reconciliation and is never automatically retried.

### Validation and subaccounts

**Recheck current Canvas state** is read-only. It refreshes the administrator's
permissions, SIS metadata, source courses, term, subaccounts, cross-list state,
and generated destination details. A change invalidates the prior confirmation.

When all sources share an approved subaccount, it becomes the destination
automatically. Mixed-subaccount groups require one group-level choice from the
accounts the signed-in administrator can manage.

`CANVAS_ALLOWED_ACCOUNT_IDS` is an optional destination-account allowlist, not a
user allowlist. Leave it empty to permit any manageable destination account in
the configured root account.

### Optional bulk import

Manual entry is the primary workflow. CSV and XLSX files are also accepted with
these columns:

| Column | Description |
| --- | --- |
| `merge_group_key` | File-local label that groups sections into one destination |
| `source_sis_id` | SIS section ID in the format shown above |

```csv
merge_group_key,source_sis_id
destination-1,2026.fall.clj.101.12345
destination-1,2026.fall.clj.101.34567
destination-2,2026.fall.clj.101.67890
destination-2,2026.fall.eng.101.78901
```

The group key is never sent to Canvas. The legacy
`destination_subaccount` column remains accepted, but new files should omit it.

## Canvas Developer Key

A scoped Canvas **API Developer Key** is required for both local Canvas Test
evaluation and shared deployment.

In the Canvas root account:

1. Open **Admin → Developer Keys** and create an **API Key**.
2. Set the key name and owner email.
3. Add the exact **Redirect URI**:
   `<APP_BASE_URL>/auth/callback`.
4. Enable **Enforce Scopes** and **Allow Include Parameters**.
5. Enable expiring tokens; the application supports token refresh.
6. For initial testing, enable **Test Cluster Only** if it is available.
7. Save the key, record its ID and secret, and switch it on.

Configure these exact scopes:

```text
url:GET|/api/v1/users/:user_id/profile
url:GET|/api/v1/manageable_accounts
url:GET|/api/v1/accounts/:id
url:GET|/api/v1/accounts/:account_id/permissions
url:GET|/api/v1/sections/:id
url:GET|/api/v1/courses/:id
url:POST|/api/v1/accounts/:account_id/courses
url:POST|/api/v1/sections/:id/crosslist/:new_course_id
```

The Developer Key scopes limit which endpoints may be called. The signed-in
administrator's Canvas role must separately allow SIS reads, course management,
section management, and course creation in the relevant accounts. After OAuth,
the application rejects users who cannot manage a configured Canvas account.

The Developer Key redirect URI and `APP_BASE_URL` must agree exactly. Examples:

```text
Local:  http://localhost:8000/auth/callback
Hosted: https://canvas-merger.example.edu/auth/callback
```

See Canvas's [Developer Key documentation](https://developerdocs.instructure.com/services/canvas/oauth2/file.developer_keys)
and [OAuth2 endpoints](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth_endpoints)
for the underlying Canvas settings and redirect behavior.

## Optional local evaluation with Canvas Test

This is the shortest path to real Canvas data without a shared deployment. The
app and the browser completing OAuth must run on the same computer, and Canvas
must permit the loopback callback shown above.

Python 3.12 or newer is required:

```bash
git clone https://github.com/tavernbiscuit/canvas_course_merger.git
cd canvas_course_merger
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Virtual-environment activation applies only to the current terminal. Run
`source .venv/bin/activate` again in each new terminal before using the
application commands below.

Set the Canvas Test values in `.env`:

```text
APP_ENV=development
APP_BASE_URL=http://localhost:8000
DATABASE_URL=sqlite:///./canvas_merger.db
CANVAS_BASE_URL=https://your-test-canvas-domain
CANVAS_ENVIRONMENT_LABEL=Canvas Test
CANVAS_CLIENT_ID=your-developer-key-id
CANVAS_CLIENT_SECRET=your-developer-key-secret
CANVAS_ROOT_ACCOUNT_ID=your-institutional-root-account-id
CANVAS_ALLOWED_ACCOUNT_IDS=
```

Keep the `CANVAS_OAUTH_SCOPES` value supplied in `.env.example`. Generate unique
local secrets and place the results in `APP_SESSION_SECRET` and
`APP_TOKEN_ENCRYPTION_KEY`:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Initialize the database and start the web app:

```bash
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open [http://localhost:8000](http://localhost:8000), sign in through Canvas
Test, and validate known test sections. Validation does not change Canvas.

When you are ready to create courses and cross-list sections in Canvas Test,
start the worker in a second terminal:

```bash
cd canvas_course_merger
source .venv/bin/activate
canvas-merger-worker
```

Without the worker, confirmed requests remain queued. Use disposable test
sections; stopping or restarting the application does not undo Canvas changes.
SQLite is suitable only for this one-computer evaluation.

## Shared deployment

For shared or sustained use, configure:

```text
APP_ENV=production
APP_BASE_URL=https://canvas-merger.example.edu
DATABASE_URL=postgresql+psycopg://...
CANVAS_BASE_URL=https://your-canvas-domain
CANVAS_ENVIRONMENT_LABEL=Canvas Test
CANVAS_CLIENT_ID=...
CANVAS_CLIENT_SECRET=...
CANVAS_ROOT_ACCOUNT_ID=...
```

Production mode requires HTTPS, PostgreSQL, unique session and encryption
secrets, and real Canvas credentials.

`CANVAS_ENVIRONMENT_LABEL` is displayed with the configured Canvas hostname on
every page so administrators can confirm which Canvas environment they are
using. Use `Canvas Test` during the pilot and update the label for any future
production deployment.

Run migrations, the web process, and the worker with the same environment:

```bash
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
.venv/bin/canvas-merger-worker
```

Place the web process behind an HTTPS reverse proxy and supervise the web and
worker as separate services. The included Dockerfile builds an OCI image usable
with Podman or Docker.

Operational requirements:

- Back up PostgreSQL; it contains workflow state, audit records, and encrypted
  OAuth credentials.
- Protect `.env` and the token-encryption key outside the repository.
- Monitor `/health/live`, `/health/ready`, service state, disk capacity,
  certificate expiry, and backup age.
- Preserve the token-encryption key across deployments and database restores.

## Development

The UI is server-rendered with FastAPI and Jinja, with lightweight JavaScript
for progressive enhancement and live progress polling. It follows UIC branding
without requiring a Node.js build.

Automated tests use isolated fakes and do not require Canvas or PostgreSQL:

```bash
source .venv/bin/activate
pytest
ruff check app tests migrations
alembic check
```
